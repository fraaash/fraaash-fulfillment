import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse
import httpx

from clients.airtable import AirtableClient
from handlers.airway_bill_processor import AirwayBillProcessor
from handlers.fulfillment import FulfillmentHandler
from handlers.telegram_query import TelegramQueryHandler
from config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

# Cursor is persisted in Airtable (System Config table) — survives restarts.
AIRWAY_BILL_POLL_INTERVAL_SECONDS = 300  # 5 minutes

# Airtable webhooks expire 7 days after creation and then stop delivering
# silently. We refresh well inside that window and re-create if it's gone.
WEBHOOK_REFRESH_INTERVAL_SECONDS = 21600   # 6 hours
SERVICE_URL  = "https://fraaash-fulfillment.onrender.com"
AT_TABLE_ID  = "tblMK2nWUx0XQIVjK"

airtable = AirtableClient()
handler = FulfillmentHandler()
telegram_handler = TelegramQueryHandler()
airway_processor = AirwayBillProcessor()
_drain_locks: dict = {}


async def _load_cursor(webhook_id: str):
    """Load webhook cursor from Airtable — survives service restarts."""
    val = await airtable.get_config(f"webhook_cursor_{webhook_id}")
    if val is not None:
        try:
            return int(val)
        except ValueError:
            pass
    return None


async def _save_cursor(webhook_id: str, cursor: int) -> None:
    """Persist webhook cursor to Airtable."""
    await airtable.set_config(f"webhook_cursor_{webhook_id}", str(cursor))


async def drain_payloads(webhook_id: str) -> None:
    if webhook_id not in _drain_locks:
        _drain_locks[webhook_id] = asyncio.Lock()
    async with _drain_locks[webhook_id]:
        await _do_drain(webhook_id)

async def _do_drain(webhook_id: str) -> None:
    cursor = await _load_cursor(webhook_id)
    while True:
        data = await airtable.get_webhook_payloads(webhook_id, cursor)
        payloads = data.get("payloads", [])
        new_cursor = data.get("cursor")
        might_have_more = data.get("mightHaveMore", False)
        for payload in payloads:
            try:
                await handler.process_payload(payload)
            except Exception as exc:
                logger.error(f"Error processing payload: {exc}", exc_info=True)
        if new_cursor:
            await _save_cursor(webhook_id, new_cursor)
            cursor = new_cursor
        if not might_have_more:
            break


async def _poll_airway_bills_loop() -> None:
    """Background task: poll SharePoint for new airway bill PDFs every 5 minutes."""
    while True:
        await asyncio.sleep(AIRWAY_BILL_POLL_INTERVAL_SECONDS)
        try:
            await airway_processor.poll_and_process()
        except Exception as exc:
            logger.error(f"Airway bill polling loop error: {exc}", exc_info=True)


async def _ensure_airtable_webhook() -> None:
    """
    Keep the Airtable webhook alive.

    Airtable webhooks expire 7 days after creation. When they lapse they are
    silently disabled — Airtable simply stops sending notifications, with no
    error anywhere. That is what took this integration down: the hook created
    on 17 Aug 2026 expired on 24 Aug and nothing noticed.

    On every run we:
      1. List the base's webhooks.
      2. Find ours by notificationUrl.
      3. Refresh it (extends the expiry by another 7 days).
      4. Re-create it if it is missing or has become disabled.
    """
    at_base = f"https://api.airtable.com/v0/bases/{settings.AIRTABLE_BASE_ID}/webhooks"
    headers = {
        "Authorization": f"Bearer {settings.AIRTABLE_TOKEN}",
        "Content-Type":  "application/json",
    }
    our_url = f"{SERVICE_URL}/webhook/airtable"

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(at_base, headers=headers)
            resp.raise_for_status()
            hooks = resp.json().get("webhooks", [])

            ours = next((w for w in hooks if w.get("notificationUrl") == our_url), None)

            if ours and ours.get("isHookEnabled"):
                r = await client.post(f"{at_base}/{ours['id']}/refresh", headers=headers)
                if r.status_code < 300:
                    logger.info(
                        f"Airtable webhook {ours['id']} refreshed — "
                        f"expires {r.json().get('expirationTime', 'unknown')}"
                    )
                    return
                logger.warning(
                    f"Airtable webhook refresh failed ({r.status_code}): {r.text} — recreating"
                )

            # Missing, disabled, or refresh failed → clean up and recreate.
            if ours:
                logger.warning(
                    f"Airtable webhook {ours['id']} is disabled/stale — deleting and recreating"
                )
                try:
                    await client.delete(f"{at_base}/{ours['id']}", headers=headers)
                except Exception as exc:
                    logger.warning(f"Could not delete old webhook: {exc}")

            payload = {
                "notificationUrl": our_url,
                "specification": {
                    "options": {
                        "filters": {
                            "fromSources": ["client"],
                            "dataTypes":   ["tableData"],
                            "recordChangeScope": AT_TABLE_ID,
                        },
                        "includes": {
                            "includePreviousCellValues":       True,
                            "includePreviousFieldDefinitions": False,
                        },
                    }
                },
            }
            r = await client.post(at_base, headers=headers, json=payload)
            r.raise_for_status()
            new_id = r.json().get("id")
            logger.info(f"✅ Airtable webhook created: {new_id} → {our_url}")

    except Exception as exc:
        logger.error(f"Airtable webhook ensure failed: {exc}", exc_info=True)


async def _webhook_refresh_loop() -> None:
    """Background task: keep the Airtable webhook alive, forever."""
    while True:
        await asyncio.sleep(WEBHOOK_REFRESH_INTERVAL_SECONDS)
        await _ensure_airtable_webhook()


async def _register_telegram_webhook() -> None:
    """Register this service as the Telegram bot webhook on every startup."""
    url = f"https://fraaash-fulfillment.onrender.com/webhook/telegram"
    tg_url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/setWebhook"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(tg_url, json={"url": url})
            data = r.json()
            if data.get("ok"):
                logger.info(f"Telegram webhook registered → {url}")
            else:
                logger.warning(f"Telegram webhook registration failed: {data}")
    except Exception as exc:
        logger.error(f"Could not register Telegram webhook: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Fraaash Fulfillment Automation started")
    await _register_telegram_webhook()
    await _ensure_airtable_webhook()
    task    = asyncio.create_task(_poll_airway_bills_loop())
    wh_task = asyncio.create_task(_webhook_refresh_loop())
    yield
    task.cancel()
    wh_task.cancel()
    logger.info("Fraaash Fulfillment Automation shutting down")


app = FastAPI(title="Fraaash Fulfillment Automation", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "fraaash-fulfillment"}


@app.post("/webhook/airtable")
async def airtable_webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        body = await request.json()
        webhook_id = body.get("webhook", {}).get("id")
        if webhook_id:
            background_tasks.add_task(drain_payloads, webhook_id)
        else:
            logger.warning(f"Webhook ping missing webhook.id: {body}")
    except Exception as exc:
        logger.error(f"Error parsing webhook ping: {exc}")
    return JSONResponse({"status": "received"}, status_code=200)


@app.post("/webhook/telegram")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        update = await request.json()
        background_tasks.add_task(telegram_handler.handle_update, update)
    except Exception as exc:
        logger.error(f"Error parsing Telegram update: {exc}")
    return JSONResponse({"status": "ok"}, status_code=200)


@app.post("/process-airway-bills")
async def process_airway_bills(background_tasks: BackgroundTasks):
    """Manual trigger: scan SharePoint now and process any new airway bill PDFs."""
    background_tasks.add_task(airway_processor.poll_and_process)
    return JSONResponse({"status": "started", "message": "Airway bill processing triggered"})
