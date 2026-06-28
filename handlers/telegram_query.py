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
import re
from datetime import date, timedelta
from typing import Optional

import httpx

from clients.airtable import AirtableClient
from config import settings

logger = logging.getLogger(__name__)

TRIGGER_WORDS = {"tracking", "track", "airway", "awb", "bill", "waybill"}
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

    async def handle_update(self, update: dict) -> None:
        """
        Entry point for every Telegram update.
        - Messages from the ops group that look like tracking queries → answer them.
        - Everything else → forward to PawBot.
        """
        message = update.get("message") or update.get("edited_message")
        if not message:
            await self._forward_to_pawbot(update)
            return

        chat_id  = str(message.get("chat", {}).get("id", ""))
        text     = (message.get("text") or "").strip()
        msg_id   = message.get("message_id")

        if chat_id == OPS_CHAT_ID and self._is_tracking_query(text):
            if self._is_date_query(text):
                await self._answer_date_query(chat_id, msg_id, text)
            else:
                await self._answer_tracking_query(chat_id, msg_id, text)
        else:
            await self._forward_to_pawbot(update)

    # ── Query detection ────────────────────────────────────────────────────────

    def _is_tracking_query(self, text: str) -> bool:
        words = set(re.findall(r"[a-zA-Z]+", text.lower()))
        return bool(words & TRIGGER_WORDS)

    def _is_date_query(self, text: str) -> bool:
        """True if the query is about orders on a specific date, not a single order."""
        words = set(re.findall(r"[a-zA-Z]+", text.lower()))
        has_date_word  = bool(words & DATE_WORDS) or bool(words & set(MONTH_MAP.keys()))
        has_date_nums  = bool(re.search(r"\b\d{1,2}[\/\-]\d{1,2}\b", text))
        return has_date_word or has_date_nums

    # ── Date-based answer ──────────────────────────────────────────────────────

    async def _answer_date_query(self, chat_id: str, reply_to: int, text: str) -> None:
        """Handle 'send tracking numbers for orders delivered today/28 June/etc.'"""
        target_date = self._extract_date(text)
        if not target_date:
            await self._send_message(
                chat_id,
                "❓ I couldn't understand the date. Try: *today*, *yesterday*, *28 June*, or *28/6*.",
                reply_to_message_id=reply_to,
            )
            return

        want_message = self._wants_tracking_message(text)
        date_iso     = target_date.isoformat()          # e.g. "2026-06-28"
        date_label   = target_date.strftime("%-d %B %Y") if hasattr(target_date, "strftime") else date_iso

        logger.info(f"Date query — date={date_iso}, want_message={want_message}")
        records = await self.airtable.search_orders_by_delivery_date(date_iso)

        if not records:
            await self._send_message(
                chat_id,
                f"📭 No courier orders found for *{date_label}*.",
                reply_to_message_id=reply_to,
            )
            return

        # Send summary first
        await self._send_message(
            chat_id,
            f"📦 Found *{len(records)}* courier order(s) for *{date_label}*:",
            reply_to_message_id=reply_to,
        )

        # Send one message per order
        for record in records:
            msg = self._format_record(record, want_message=want_message)
            await self._send_message(chat_id, msg)

    def _extract_date(self, text: str) -> Optional[date]:
        """Parse a date from a natural language query. Returns a date object or None."""
        t = text.lower()
        today = date.today()

        if "today" in t:
            return today
        if "yesterday" in t:
            return today - timedelta(days=1)
        if "tomorrow" in t:
            return today + timedelta(days=1)

        # Try "28 June" / "June 28" / "28 June 2026"
        for month_name, month_num in MONTH_MAP.items():
            # e.g. "28 june" or "28 june 2026"
            m = re.search(rf"\b(\d{{1,2}})\s+{month_name}(?:\s+(\d{{4}}))?\b", t)
            if m:
                day  = int(m.group(1))
                year = int(m.group(2)) if m.group(2) else today.year
                try:
                    return date(year, month_num, day)
                except ValueError:
                    pass
            # e.g. "june 28" or "june 28 2026"
            m = re.search(rf"\b{month_name}\s+(\d{{1,2}})(?:\s+(\d{{4}}))?\b", t)
            if m:
                day  = int(m.group(1))
                year = int(m.group(2)) if m.group(2) else today.year
                try:
                    return date(year, month_num, day)
                except ValueError:
                    pass

        # Try numeric formats: 28/6, 28-6, 28/6/2026, 28-6-2026
        m = re.search(r"\b(\d{1,2})[\/\-](\d{1,2})(?:[\/\-](\d{4}))?\b", text)
        if m:
            try:
                day   = int(m.group(1))
                month = int(m.group(2))
                year  = int(m.group(3)) if m.group(3) else today.year
                return date(year, month, day)
            except ValueError:
                pass

        # Try ISO format: 2026-06-28
        m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                pass

        return None

    # ── Single-order answer ────────────────────────────────────────────────────

    async def _answer_tracking_query(self, chat_id: str, reply_to: int, text: str) -> None:
        order_number, customer_name = self._extract_query_parts(text)
        want_message = self._wants_tracking_message(text)
        logger.info(
            f"Tracking query — order_number={order_number!r}, "
            f"customer_name={customer_name!r}, want_message={want_message}"
        )

        records = await self.airtable.search_orders(
            order_number=order_number,
            customer_name=customer_name,
        )

        if not records:
            reply = (
                "❌ No order found"
                + (f" for order number *{order_number}*" if order_number else "")
                + (f" / customer *{customer_name}*" if customer_name else "")
                + ".\n\nPlease check the order number or name and try again."
            )
        elif len(records) == 1:
            reply = self._format_record(records[0], want_message=want_message)
        else:
            # Multiple matches — list them concisely
            lines = [f"Found {len(records)} orders:\n"]
            for r in records[:5]:
                f = r.get("fields", {})
                lines.append(
                    f"• {f.get('Order No.') or f.get('Order Number', '?')} — "
                    f"{_first(f.get('Customer Name'))} — "
                    f"AWB: {f.get('Airway Bill') or '(not yet purchased)'}"
                )
            if len(records) > 5:
                lines.append(f"...and {len(records) - 5} more. Please be more specific.")
            reply = "\n".join(lines)

        await self._send_message(chat_id, reply, reply_to_message_id=reply_to)

    def _wants_tracking_message(self, text: str) -> bool:
        """Return True if the user asked for the full tracking message (not just the AWB number)."""
        t = text.lower()
        return "message" in t or "link" in t or "template" in t

    def _format_record(self, record: dict, want_message: bool = False) -> str:
        f = record.get("fields", {})
        order_no     = f.get("Order No.") or f.get("Order Number") or f.get("Order ID") or "?"
        customer     = _first(f.get("Customer Name")) or "?"
        airway_bill  = f.get("Airway Bill") or "(not yet purchased)"
        tracking_msg = f.get("Tracking No. Message") or ""

        if want_message:
            # Return the full Tracking No. Message text (ready to forward to customer)
            if tracking_msg:
                return tracking_msg
            else:
                return (
                    f"📦 *{order_no}* — {customer}\n"
                    f"⚠️ No tracking message available yet (airway bill: `{airway_bill}`)"
                )
        else:
            # Return AWB / tracking number + status
            status = f.get("Process Status") or "?"
            return (
                f"📦 *Order:* {order_no}\n"
                f"👤 *Customer:* {customer}\n"
                f"📋 *Status:* {status}\n"
                f"🏷 *Airway Bill:* `{airway_bill}`"
            )

    def _extract_query_parts(self, text: str):
        """
        Extract (order_number, customer_name) from a free-text query.
        Order number = contiguous digit sequence (4–6 digits).
        Customer name = remaining non-trigger, non-stopword words.
        """
        # Find order number — a standalone number like 00423, 423, etc.
        num_match = re.search(r"\b(\d{3,6})\b", text)
        order_number = num_match.group(1) if num_match else ""

        # Remove trigger words, stopwords, the order number, and punctuation
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
        """Forward the raw update to PawBot's webhook so it keeps working."""
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
