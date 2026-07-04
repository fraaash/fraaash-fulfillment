import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse

from clients.airtable import AirtableClient
from handlers.airway_bill_processor import AirwayBillProcessor
from handlers.fulfillment import FulfillmentHandler
from handlers.telegram_query import TelegramQueryHandler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

# Cursor is persisted in Airtable (System Config table) — survives restarts.
AIRWAY_BILL_POLL_INTERVAL_SECONDS = 300  # 5 minutes

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Fraaash Fulfillment Automation started")
    task = asyncio.create_task(_poll_airway_bills_loop())
    yield
    task.cancel()
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
