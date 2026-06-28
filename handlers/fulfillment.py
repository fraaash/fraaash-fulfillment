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
from datetime import datetime

from clients.airtable import AirtableClient
from clients.ninjavan import NinjaVanClient
from clients.sharepoint import SharePointClient
from clients.telegram import TelegramClient
from config import settings

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

TABLE_ID = "tblMK2nWUx0XQIVjK"

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

STATUS_PENDING    = "Pending"
STATUS_DELIVERED  = "Delivered"
STATUS_COLLECTED  = "Collected"
COURIER_REQUIRED  = "Courier Required"


# ── Handler ────────────────────────────────────────────────────────────────────

class FulfillmentHandler:
    def __init__(self):
        self.airtable    = AirtableClient()
        self.ninjavan    = NinjaVanClient()
        self.sharepoint  = SharePointClient()
        self.telegram    = TelegramClient()

    async def process_payload(self, payload: dict) -> None:
        """Route a single Airtable webhook payload to the correct handler."""
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
            await self._handle_changed_record(record_id, change)

    # ── New record ─────────────────────────────────────────────────────────────

    async def _handle_new_record(self, record_id: str, fields: dict) -> None:
        status     = _choice(fields.get(F_STATUS))
        collection = _choice(fields.get(F_COLLECTION))
        airway_bill = fields.get(F_AIRWAY_BILL)

        if (
            collection == COURIER_REQUIRED
            and status == STATUS_PENDING
            and not airway_bill
        ):
            logger.info(f"[{record_id}] New courier order — purchasing airway bill")
            await self._purchase_airway_bill(record_id, fields, use_field_ids=True)

    # ── Changed record ─────────────────────────────────────────────────────────

    async def _handle_changed_record(self, record_id: str, change: dict) -> None:
        current  = change.get("current",  {}).get("cellValuesByFieldId", {})
        previous = change.get("previous", {}).get("cellValuesByFieldId", {})

        curr_status = _choice(current.get(F_STATUS))
        prev_status = _choice(previous.get(F_STATUS))

        # ── Trigger 2: status flipped to Delivered / Collected ─────────────────
        if (
            curr_status in (STATUS_DELIVERED, STATUS_COLLECTED)
            and prev_status not in (STATUS_DELIVERED, STATUS_COLLECTED)
        ):
            # Need the full record (field names) to read formula fields
            record = await self.airtable.get_record(record_id)
            full   = record.get("fields", {})

            if full.get("Collection Method") == COURIER_REQUIRED:
                tracking_msg = full.get("Tracking No. Message", "")
                if tracking_msg:
                    logger.info(f"[{record_id}] Status → {curr_status} — sending Telegram")
                    await self.telegram.send_message(
                        chat_id=settings.TELEGRAM_OPS_CHAT_ID,
                        text=tracking_msg,
                    )
                else:
                    logger.warning(f"[{record_id}] Tracking No. Message is empty — skipping Telegram")

        # ── Trigger 1 (edge case): Collection Method set to Courier on existing record ──
        curr_collection = _choice(current.get(F_COLLECTION))
        prev_collection = _choice(previous.get(F_COLLECTION))

        if (
            curr_collection == COURIER_REQUIRED
            and prev_collection != COURIER_REQUIRED
        ):
            record = await self.airtable.get_record(record_id)
            full   = record.get("fields", {})

            if (
                full.get("Process Status") == STATUS_PENDING
                and not full.get("Airway Bill")
            ):
                logger.info(
                    f"[{record_id}] Collection Method changed to Courier — purchasing airway bill"
                )
                await self._purchase_airway_bill(record_id, full, use_field_ids=False)

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
            logger.error(f"[{record_id}] ❌ Airway bill purchase failed: {exc}", exc_info=True)
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
