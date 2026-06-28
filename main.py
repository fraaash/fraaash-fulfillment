"""
Fraaash Fulfillment Automation
————————————————————————————————
FastAPI service that:
  1. Receives Airtable webhook pings
  2. Fetches the actual change payloads
  3. Purchases Ninja Van airway bills for new courier orders
  4. Saves PDFs to SharePoint
  5. Updates Airtable with tracking numbers
  6. Sends Telegram tracking messages when orders are Delivered / Collected
"""

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse

from clients.airtable import AirtableClient
from handlers.fulfillment import FulfillmentHandler
from handlers.telegram_query import TelegramQueryHandler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

# Cursor is stored in a local file so we resume from where we left off after restarts.
# On Render's free tier the filesystem is ephemeral, but losing the cursor only means
# we might re-process a few payloads — the idempotency checks in FulfillmentHandler
# (Airway Bill field must be empty) prevent duplicate purchases.
CURSOR_FILE = Path("webhook_cursor.json")

airtable        = AirtableClient()
handler         = FulfillmentHandler()
telegram_handler = TelegramQueryHandler()


# ── Cursor helpers ─────────────────────────────────────────────────────────────

def _load_cursors() -> dict:
    if CURSOR_FILE.exists():
        try:
            return json.loads(CURSOR_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save_cursor(webhook_id: str, cursor: int) -> None:
    data = _load_cursors()
    data[webhook_id] = cursor
    CURSOR_FILE.write_text(json.dumps(data))


# ── Payload processing ─────────────────────────────────────────────────────────

async def drain_payloads(webhook_id: str) -> None:
    """Fetch all pending payloads for a webhook and pass each to the handler."""
    cursors = _load_cursors()
    cursor  = cursors.get(webhook_id)

    while True:
        data         = await airtable.get_webhook_payloads(webhook_id, cursor)
        payloads     = data.get("payloads", [])
        new_cursor   = data.get("cursor")
        might_have_more = data.get("mightHaveMore", False)

        for payload in payloads:
            try:
                await handler.process_payload(payload)
            except Exception as exc:
                # Log and continue — don't let one bad payload block the rest
                logger.error(f"Error processing payload: {exc}", exc_info=True)

        if new_cursor:
            _save_cursor(webhook_id, new_cursor)
            cursor = new_cursor

        if not might_have_more:
            break


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Fraaash Fulfillment Automation started")
    yield
    logger.info("Fraaash Fulfillment Automation shutting down")


# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(title="Fraaash Fulfillment Automation", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "fraaash-fulfillment"}


@app.post("/webhook/airtable")
async def airtable_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Airtable sends a lightweight ping here whenever records change.
    We immediately return 200 and drain payloads in the background.
    """
    try:
        body       = await request.json()
        webhook_id = body.get("webhook", {}).get("id")
        if webhook_id:
            background_tasks.add_task(drain_payloads, webhook_id)
        else:
            logger.warning(f"Webhook ping missing webhook.id: {body}")
    except Exception as exc:
        logger.error(f"Error parsing webhook ping: {exc}")

    # Always retur