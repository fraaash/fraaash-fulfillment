"""
Core fulfillment logic.

Airtable field IDs used (Purchase Orders table — tblMK2nWUx0XQIVjK):
  fldFYQrIuVVZMSksf  Process Status        (singleSelect)
  fldkdp4L2oc3ZhcB2  Collection Method     (singleSelect)
  fldnLVIuIWrPZda6v  Airway Bill           (singleLineText)
  fldDOE1yMEI16z5UK  Tracking No. Message  (formula)
  fldmU2FR9iN5QzBDP  Order ID              (formula)
  fldEziLo8ARk98mfd  Customer Name         (multipleLookupValues)
  fldhL2eZOtf4xbVkF  Address               (multipleLookupValues)
  fldGpEjEeUZjqq3eu  Contact               (multipleLookupValues)
  fld1yu148Cuf7pC3j  State                 (multipleLookupValues)
  fldocjAjARRU1MxLW  Postcode              (multipleLookupValues)
  fldGitxGp6zLKmf13  Parcel Description    (formula)
  fld6WCGUNpodRhoYM  Delivery Date         (date)
"""

import logging
import re
from datetime import date, datetime, timezone

import httpx

from clients.airtable import AirtableClient
from clients.ninjavan import NinjaVanClient
from clients.sharepoint import SharePointClient
from clients.telegram import TelegramClient
from config import settings

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

TABLE_ID = "tblMK2nWUx0XQIVjK"

# Inventory base — for auto-logging Out movements when orders are delivered
INV_BASE           = "app4Rm9ZIGWaFeCf4"
INV_MOVEMENT_TABLE = "tblSx11BYxubiGdHk"
AT_API             = "https://api.airtable.com/v0"

# Field IDs (from Airtable webhook payloads which use IDs, not names)
F_STATUS          = "fldFYQrIuVVZMSksf"
F_COLLECTION      = "fldkdp4L2oc3ZhcB2"
F_AIRWAY_BILL     = "fldnLVIuIWrPZda6v"
F_TRACKING_MSG    = "fldDOE1yMEI16z5UK"
F_ORDER_ID        = "fldmU2FR9iN5QzBDP"
F_CUSTOMER_NAME   = "fldEziLo8ARk98mfd"
F_ADDRESS         = "fldhL2eZOtf4xbVkF"
F_CONTACT         = "fldGpEjEeUZjqq3eu"
F_STATE           = "fld1yu148Cuf7pC3j"
F_POSTCODE        = "fldocjAjARRU1MxLW"
F_PARCEL_DESC     = "fldGitxGp6zLKmf13"
F_DELIVERY_DATE   = "fld6WCGUNpodRhoYM"

STATUS_PENDING     = "Pending"
STATUS_IN_PROGRESS = "In Progress"
STATUS_DELIVERED   = "Delivered"
STATUS_COLLECTED   = "Collected"
COURIER_REQUIRED   = "Courier Required"


# ── Handler ────────────────────────────────────────────────────────────────────

class FulfillmentHandler:
    def __init__(self):
        self.airtable    = AirtableClient()
        self.ninjavan    = NinjaVanClient()
        self.sharepoint  = SharePointClient()
        self.telegram    = TelegramClient()

    async def process_payload(self, payload: dict) -> None:
        """Route a single Airtable webhook payload to the correct handler."""
        # Detect stale payloads (replayed after a service restart).
        # We still process them to advance the cursor, but suppress Telegram sends.
        is_stale = _is_stale_payload(payload, max_age_seconds=600)

        # ── Newly created records ──────────────────────────────────────────────
        created_tables = payload.get("createdTablesById", {})
        created_table  = created_tables.get(TABLE_ID, {})
        for record_id, record_data in created_table.get("createdRecordsById", {}).items():
            fields = record_data.get("cellValuesByFieldId", {})
            await self._handle_new_record(record_id, fields)

        # ── Changed records ────────────────────────────────────────────────────
        changed_tables = payload.get("changedTablesById", {})
        changed_table  = changed_tables.get(TABLE_ID, {})
        for record_id, change in changed_table.get("changedRecordsById", {}).items():
            await self._handle_changed_record(record_id, change, is_stale=is_stale)

    # ── New record ─────────────────────────────────────────────────────────────

    async def _handle_new_record(self, record_id: str, fields: dict) -> None:
        # New records are never auto-processed — airway bill is only purchased
        # when you manually move the order to "In Progress".
        logger.info(f"[{record_id}] New order created — waiting for In Progress status")

    # ── Changed record ─────────────────────────────────────────────────────────

    async def _handle_changed_record(self, record_id: str, change: dict, is_stale: bool = False) -> None:
        current  = change.get("current",  {}).get("cellValuesByFieldId", {})
        previous = change.get("previous", {}).get("cellValuesByFieldId", {})

        curr_status = _choice(current.get(F_STATUS))
        prev_status = _choice(previous.get(F_STATUS))

        # ── Trigger 1: any status change → In Progress ────────────────────────
        # We don't gate on prev_status == Pending because orders may start with
        # a blank status (created via manual form) or come from another state.
        # The Airway Bill emptiness check is the real idempotency guard.
        if curr_status == STATUS_IN_PROGRESS and prev_status != STATUS_IN_PROGRESS:
            record = await self.airtable.get_record(record_id)
            full   = record.get("fields", {})

            if (
                full.get("Collection Method") == COURIER_REQUIRED
                and not full.get("Airway Bill")
            ):
                logger.info(f"[{record_id}] Status → In Progress (prev={prev_status!r}) — purchasing airway bill")
                await self._purchase_airway_bill(record_id, full, use_field_ids=False)
            elif full.get("Collection Method") != COURIER_REQUIRED:
                logger.info(f"[{record_id}] Status → In Progress but not Courier — skipping airway bill")
            else:
                logger.info(f"[{record_id}] Status → In Progress but Airway Bill already exists — skipping")

        # ── Trigger 2: status flipped to Delivered / Collected ─────────────────
        if (
            curr_status in (STATUS_DELIVERED, STATUS_COLLECTED)
            and prev_status not in (STATUS_DELIVERED, STATUS_COLLECTED)
        ):
            record = await self.airtable.get_record(record_id)
            full   = record.get("fields", {})

            # Send tracking message for courier orders
            if full.get("Collection Method") == COURIER_REQUIRED:
                tracking_msg = full.get("Tracking No. Message", "")
                if tracking_msg:
                    if is_stale:
                        logger.info(
                            f"[{record_id}] Status → {curr_status} — skipping Telegram "
                            f"(stale payload, replayed after restart)"
                        )
                    else:
                        logger.info(f"[{record_id}] Status → {curr_status} — sending Telegram")
                        await self.telegram.send_message(
                            chat_id=settings.TELEGRAM_OPS_CHAT_ID,
                            text=tracking_msg,
                        )
                else:
                    logger.warning(f"[{record_id}] Tracking No. Message is empty — skipping Telegram")

            # Auto-log inventory Out movement for all delivered orders with product quantities
            bb = int(full.get("Chicken Quantity") or 0)
            gg = int(full.get("Salmon Quantity") or 0)
            order_id      = str(full.get("Order ID") or record_id)
            delivery_date = full.get("Delivery Date") or date.today().isoformat()

            if bb > 0 or gg > 0:
                try:
                    await self._log_inventory_out(bb, gg, order_id, delivery_date)
                    logger.info(f"[{record_id}] Auto-logged inventory out: {bb} BB, {gg} GG for order {order_id}")
                    if not is_stale:
                        parts = []
                        if bb: parts.append(f"🐔 BB: *{bb} boxes*")
                        if gg: parts.append(f"🐟 GG: *{gg} boxes*")
                        try:
                            d = date.fromisoformat(delivery_date)
                            date_label = d.strftime("%-d %B %Y")
                        except Exception:
                            date_label = delivery_date
                        await self.telegram.send_message(
                            chat_id=settings.TELEGRAM_OPS_CHAT_ID,
                            text=(
                                f"📦 *Inventory Out logged — {order_id}*\n"
                                + "  |  ".join(parts)
                                + f"\n_{date_label}_"
                            ),
                        )
                except Exception as exc:
                    logger.error(f"[{record_id}] Auto inventory-out failed: {exc}", exc_info=True)

    # ── Inventory out (auto-logged on Delivered/Collected) ─────────────────────

    async def _log_inventory_out(
        self, bb: int, gg: int, order_id: str, delivery_date: str
    ) -> None:
        """Create an inventory Out movement in the Inventory base."""
        fields: dict = {
            "fldnkV4GeBZmNe8Fy": delivery_date,
            "fldESxOVa6nglAy0J": "Out",
            "fldkGMAzNKJzDBYCS": f"Courier delivery – {order_id}",
        }
        if bb:
            fields["fld2O5oOrRAaABCr9"] = -bb
            fields["fldUYRurduQ37qopd"] = bb * 6
        if gg:
            fields["fld2uxP8aLheTQwQN"] = -gg
            fields["fldROm2Yl2Le2W7Vn"] = gg * 6

        url     = f"{AT_API}/{INV_BASE}/{INV_MOVEMENT_TABLE}"
        headers = {
            "Authorization": f"Bearer {settings.AIRTABLE_TOKEN}",
            "Content-Type":  "application/json",
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(url, headers=headers, json={"fields": fields})
            r.raise_for_status()

    # ── Airway bill purchase ───────────────────────────────────────────────────

    async def _purchase_airway_bill(
        self,
        record_id: str,
        fields: dict,
        use_field_ids: bool = True,
    ) -> None:
        """
        Buy an airway bill from Ninja Van, save the PDF to SharePoint,
        and write the tracking number back to Airtable.

        `use_field_ids=True`  → fields dict keyed by Airtable field IDs (from webhook)
        `use_field_ids=False` → fields dict keyed by field names (from get_record)
        """
        try:
            if use_field_ids:
                customer_name = _first(fields.get(F_CUSTOMER_NAME))
                address       = _first(fields.get(F_ADDRESS))
                contact       = _first(fields.get(F_CONTACT))
                state         = _first(fields.get(F_STATE))
                postcode      = _first(fields.get(F_POSTCODE))
                order_id      = str(fields.get(F_ORDER_ID) or record_id)
                parcel_desc   = str(fields.get(F_PARCEL_DESC) or "Pet Food")
                delivery_date = fields.get(F_DELIVERY_DATE)
            else:
                customer_name = _first(fields.get("Customer Name"))
                address       = _first(fields.get("Address"))
                contact       = _first(fields.get("Contact"))
                state         = _first(fields.get("State"))
                postcode      = _first(fields.get("Postcode"))
                order_id      = str(fields.get("Order ID") or record_id)
                parcel_desc   = str(fields.get("Parcel Description") or "Pet Food")
                delivery_date = fields.get("Delivery Date")

            tracking_number, pdf_bytes = await self.ninjavan.create_order(
                customer_name=customer_name,
                address=address,
                contact=contact,
                state=state,
                postcode=postcode,
                order_reference=_safe_reference(order_id),
                parcel_description=parcel_desc,
                delivery_date=delivery_date,
            )

            # Save PDF → SharePoint: 7. Operation/Airway Bills/June 2026/
            month_folder = datetime.now().strftime("%B %Y")   # e.g. "June 2026"
            filename     = f"{_safe_filename(order_id)}_{tracking_number}.pdf"
            await self.sharepoint.upload_airway_bill(
                pdf_bytes=pdf_bytes,
                month_folder=month_folder,
                filename=filename,
            )

            # Write tracking number back to Airtable
            await self.airtable.update_record(
                record_id=record_id,
                fields={"Airway Bill": tracking_number},
            )

            logger.info(
                f"[{record_id}] ✅ Airway bill done — "
                f"tracking: {tracking_number}, file: {filename}"
            )

        except Exception as exc:
            # Log the response body if it's an HTTP error — tells us WHY NinjaVan rejected
            resp_body = ""
            if hasattr(exc, "response") and exc.response is not None:
                try:
                    resp_body = exc.response.text
                except Exception:
                    pass
            logger.error(
                f"[{record_id}] ❌ Airway bill purchase failed: {exc}"
                + (f"\nNinjaVan response body: {resp_body}" if resp_body else ""),
                exc_info=True,
            )
            raise


# ── Utilities ──────────────────────────────────────────────────────────────────

def _choice(value) -> str:
    """Extract .name from an Airtable singleSelect field value."""
    if isinstance(value, dict):
        return value.get("name", "")
    return str(value) if value else ""


def _first(value) -> str:
    """Return first element from a lookup array, or the value itself as string."""
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value) if value else ""


def _safe_reference(text: str) -> str:
    """Strip characters not allowed in Ninja Van purchase_order_number (max 30 chars)."""
    return re.sub(r"[^A-Za-z0-9\-_]", "", text)[:30]


def _safe_filename(text: str) -> str:
    """Make a string safe for use as a filename."""
    return re.sub(r"[^A-Za-z0-9\-_]", "_", text)[:40]


def _is_stale_payload(payload: dict, max_age_seconds: int = 600) -> bool:
    """Return True if the Airtable webhook payload is older than max_age_seconds.

    Render's free tier wipes the filesystem on restart, losing the cursor file.
    On the next ping, old payloads are replayed from the beginning. We detect
    this by comparing the payload's timestamp to now — if it's older than
    max_age_seconds (default 10 min) we treat it as stale and suppress
    external side-effects like Telegram messages.
    """
    ts_str = payload.get("timestamp", "")
    if not ts_str:
        return False
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        if age > max_age_seconds:
            logger.info(f"Stale payload detected — age {age:.0f}s > {max_age_seconds}s threshold")
            return True
    except Exception:
        pass
  