"""
Handles natural-language tracking queries from the Fraaash Operation Telegram group.
Trigger phrases (case-insensitive):
  "tracking", "track", "airway", "awb", "bill no", "waybill"
Example messages it understands:
  "what is the tracking number for order 00423 chen lee soo"
  "airway bill for 00423"
  "track chen lee soo"
  "awb 00423"
"""
import logging
import reh
from datetime import date, timedelta
from typing import Optional
import httpx
from clients.airtable import AirtableClient
from handlers.inventory import InventoryHandler
from config import settings

logger = logging.getLogger(__name__)

TRIGGER_WORDS  = {"tracking", "track", "airway", "awb", "bill", "waybill"}
UPDATE_WORDS   = {"update", "delivered", "changed", "set", "mark", "delivery", "sent", "ship", "shipped", "dispatch", "dispatched", "out"}
DATE_WORDS    = {"today", "yesterday", "tomorrow"}
MONTH_MAP     = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

PAWBOT_WEBHOOK = "https://pawbot-sope.onrender.com/webhook"
OPS_CHAT_ID   = str(settings.TELEGRAM_OPS_CHAT_ID)
TG_BASE       = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}"

class TelegramQueryHandler:
    def __init__(self):
        self.airtable = AirtableClient()
        self.inventory_handler = InventoryHandler()

    async def handle_update(self, update: dict) -> None:

        """
        Entry point for every Telegram update.
        - Inventory queries → handled by InventoryHandler.
        - Order delivery date updates → answered directly.
        - Tracking queries → answered directly.
        - Everything else → forward to PawBot.
        """


        message = update.get("message") or update.get("edited_message")
        if not message:
            await self._forward_to_pawbot(update)
            return

        chat_id  = str(message.get("chat", {}).get("id", ""))
        text     = (message.get("text") or "").strip()
        msg_id   = message.get("message_id")

        if chat_id == OPS_CHAT_ID:
            if self.inventory_handler.is_inventory_query(text):
                await self.inventory_handler.handle(chat_id, msg_id, text)
            elif self._is_update_query(text):
                await self._answer_update_query(chat_id, msg_id, text)
            elif self._is_tracking_query(text):
                if self._is_date_query(text):
                    await self._answer_date_query(chat_id, msg_id, text)
                else:
                    await self._answer_tracking_query(chat_id, msg_id, text)
            else:
                await self._forward_to_pawbot(update)
        else:
            await self._forward_to_pawbot(update)

    # ── Query detection ────────────────────────────────────────────────────────
    def _is_tracking_query(self, text: str) -> bool:
        words = set(re.findall(r"[a-zA-Z]+", text.lower()))
        return bool(words & TRIGGER_WORDS)

    def _is_update_query(self, text: str) -> bool:
        words = set(re.findall(r"[a-zA-Z]+", text.lower()))
        has_update = bool(words & UPDATE_WORDS)
        has_order  = bool(re.search(r"\b\d{3,6}\b", text))
        has_date   = bool(words & DATE_WORDS) or bool(words & set(MONTH_MAP.keys())) \
                     or bool(re.search(r"\b\d{1,2}[\/\-]\d{1,2}\b", text))
        return has_update and has_order and has_date

    def _is_date_query(self, text: str) -> bool:
        words = set(re.findall(r"[a-zA-Z]+", text.lower()))
        has_date_word  = bool(words & DATE_WORDS) or bool(words & set(MONTH_MAP.keys()))
        has_date_nums  = bool(re.search(r"\b\d{1,2}[\/\-]\d{1,2}\b", text))
        return has_date_word or has_date_nums

    # ── Date-based answer ──────────────────────────────────────────────────────
    async def _answer_date_query(self, chat_id: str, reply_to: int, text: str) -> None:
        target_date = self._extract_date(text)
        if not target_date:
            await self._send_message(
                chat_id,
                "❓ I couldn\'t understand the date. Try: *today*, *yesterday*, *28 June*, or *28/6*.",
                reply_to_message_id=reply_to,
            )
            return

        want_message = self._wants_tracking_message(text)
        date_iso     = target_date.isoformat()
        date_label   = target_date.strftime("%-d %B %Y") if hasattr(target_date, "strftime") else date_iso

        logger.info(f"Date query — date={date_iso}, want_message={want_message}")

        records = await self.airtable.search_orders_by_delivery_date(date_iso)
        if not records:
            await self._send_message(
                chat_id,
                f"\U0001f4ed No courier orders found for *{date_label}*.",
                reply_to_message_id=reply_to,
            )
            return

        await self._send_message(
            chat_id,
            f"\U0001f4e6 Found *{len(records)}* courier order(s) for *{date_label}*:",
            reply_to_message_id=reply_to,
        )

        for record in records:
            msg = self._format_record(record, want_message=want_message)
            await self._send_message(chat_id, msg)

    def _extract_date(self, text: str) -> Optional[date]:
        t = text.lower()
        today = date.today()
        if "today" in t:
            return today
        if "yesterday" in t:
            return today - timedelta(days=1)
        if "tomorrow" in t:
            return today + timedelta(days=1)

        for month_name, month_num in MONTH_MAP.items():
            m = re.search(rf"\b(\d{{1,2}})\s+{month_name}(?:\s+(\d{{4}}))?\b", t)
            if m:
                day  = int(m.group(1))
                year = int(m.group(2)) if m.group(2) else today.year
                try:
                    return date(year, month_num, day)
                except ValueError:
                    pass
            m = re.search(rf"\b{month_name}\s+(\d{{1,2}})(?:\s+(\d{{4}}))?\b", t)
            if m:
                day  = int(m.group(1))
                year = int(m.group(2)) if m.group(2) else today.year
                try:
                    return date(year, month_num, day)
                except ValueError:
                    pass

        m = re.search(r"\b(\d{1,2})[\/\-](\d{1,2})(?:[\/\-](\d{4}))?\b", text)
        if m:
            try:
                day   = int(m.group(1))
                month = int(m.group(2))
                year  = int(m.group(3)) if m.group(3) else today.year
                return date(year, month, day)
            except ValueError:
                pass

        m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                pass

        return None

    # ── Update delivery date ───────────────────────────────────────────────────
    async def _answer_update_query(self, chat_id: str, reply_to: int, text: str) -> None:

        """Handle delivery date updates for one or multiple orders."""


        order_numbers = re.findall(r"\b(\d{3,6})\b", text)
        if not order_numbers:
            await self._send_message(chat_id, "❓ Please include at least one order number.", reply_to_message_id=reply_to)
            return

        target_date = self._extract_date(text)
        if not target_date:
            await self._send_message(
                chat_id,
                "❓ I couldn\'t understand the date. Try: *today*, *29 June*, or *29/6*.",
                reply_to_message_id=reply_to,
            )
            return

        t = text.lower()
        also_mark_delivered = "delivered" in t or "sent" in t or "shipped" in t or "dispatched" in t
        date_label = target_date.strftime("%d %B %Y")
        updates: dict = {"Delivery Date": target_date.isoformat()}
        if also_mark_delivered:
            updates["Process Status"] = "Delivered"

        ok_lines   = []
        fail_lines = []
        for order_number in order_numbers:
            records = await self.airtable.search_orders(order_number=order_number)
            if not records:
                fail_lines.append(f"• ❌ *{order_number}* — not found")
                continue
            record    = records[0]
            record_id = record["id"]
            fields    = record.get("fields", {})
            order_no  = fields.get("Order No.") or fields.get("Order Number") or order_number
            customer  = _first(fields.get("Customer Name")) or "?"
            await self.airtable.update_record(record_id=record_id, fields=updates)
            ok_lines.append(f"• ✅ *{order_no}* — {customer}")
            logger.info(f"Updated order {order_number}: {updates}")

        status_note = " + status → *Delivered*" if also_mark_delivered else ""
        summary = f"\U0001f4c5 Delivery Date → *{date_label}*{status_note}\n\n"
        if ok_lines:
            summary += "\n".join(ok_lines)
        if fail_lines:
            summary += "\n" + "\n".join(fail_lines)
        await self._send_message(chat_id, summary, reply_to_message_id=reply_to)

    # ── Single-order answer ────────────────────────────────────────────────────
    async def _answer_tracking_query(self, chat_id: str, reply_to: int, text: str) -> None:
        want_message  = self._wants_tracking_message(text)
        order_numbers = re.findall(r"\b(\d{3,6})\b", text)
        if order_numbers:
            for order_number in order_numbers:
                records = await self.airtable.search_orders(order_number=order_number)
                if not records:
                    await self._send_message(
                        chat_id,
                        f"❌ Order *{order_number}* not found.",
                        reply_to_message_id=reply_to,
                    )
                else:
                    await self._send_message(
                        chat_id,
                        self._format_record(records[0], want_message=want_message),
                        reply_to_message_id=reply_to,
                    )
        else:
            _, customer_name = self._extract_query_parts(text)
            logger.info(f"Tracking query by name — customer_name={customer_name!r}")
            records = await self.airtable.search_orders(customer_name=customer_name)
            if not records:
                await self._send_message(
                    chat_id,
                    f"❌ No order found for customer *{customer_name}*.",
                    reply_to_message_id=reply_to,
                )
            elif len(records) == 1:
                await self._send_message(
                    chat_id,
                    self._format_record(records[0], want_message=want_message),
                    reply_to_message_id=reply_to,
                )
            else:
                lines = [f"Found {len(records)} orders for *{customer_name}*:\n"]
                for r in records[:5]:
                    f = r.get("fields", {})
                    lines.append(
                        f"• {f.get(\'Order No.\') or f.get(\'Order Number\', \'?\')} — "
                        f"AWB: {f.get(\'Airway Bill\') or \'(not yet purchased)\'}"
                    )
                if len(records) > 5:
                    lines.append(f"...and {len(records) - 5} more. Please add an order number.")
                await self._send_message(chat_id, "\n".join(lines), reply_to_message_id=reply_to)

    def _wants_tracking_message(self, text: str) -> bool:
        t = text.lower()
        return "message" in t or "link" in t or "template" in t

    def _format_record(self, record: dict, want_message: bool = False) -> str:
        f = record.get("fields", {})
        order_no     = f.get("Order No.") or f.get("Order Number") or f.get("Order ID") or "?"
        customer     = _first(f.get("Customer Name")) or "?"
        airway_bill  = f.get("Airway Bill") or "(not yet purchased)"
        tracking_msg = f.get("Tracking No. Message") or ""
        if want_message:
            if tracking_msg:
                return tracking_msg
            else:
                return (
                    f"\U0001f4e6 *{order_no}* — {customer}\n"
                    f"⚠️ No tracking message available yet (airway bill: `{airway_bill}`)"
                )
        else:
            status = f.get("Process Status") or "?"
            return (
                f"\U0001f4e6 *Order:* {order_no}\n"
                f"\U0001f464 *Customer:* {customer}\n"
                f"\U0001f4cb *Status:* {status}\n"
                f"\U0001f3f7 *Airway Bill:* `{airway_bill}`"
            )

    def _extract_query_parts(self, text: str):
        num_match = re.search(r"\b(\d{3,6})\b", text)
        order_number = num_match.group(1) if num_match else ""
        stopwords = {
            "what", "is", "the", "for", "of", "a", "an", "please",
            "can", "you", "me", "give", "show", "find", "check",
            "no", "number", "order", "tracking", "track", "airway",
            "awb", "bill", "waybill", "hi", "hey", "hello",
        }
        words = re.findall(r"[a-zA-Z]+", text.lower())
        name_words = [w for w in words if w not in stopwords and len(w) >= 2]
        customer_name = " ".join(name_words)
        return order_number, customer_name

    # ── Telegram API calls ─────────────────────────────────────────────────────
    async def _send_message(
        self, chat_id: str, text: str, reply_to_message_id: Optional[int] = None
    ) -> None:
        payload: dict = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{TG_BASE}/sendMessage", json=payload)
            resp.raise_for_status()

    async def _forward_to_pawbot(self, update: dict) -> None:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(PAWBOT_WEBHOOK, json=update)
        except Exception as exc:
            logger.warning(f"Could not forward update to PawBot: {exc}")

# ── Helpers ────────────────────────────────────────────────────────────────────
def _first(value) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value) if value else ""
