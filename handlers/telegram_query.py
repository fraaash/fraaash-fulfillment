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
from typing import Optional

import httpx

from clients.airtable import AirtableClient
from config import settings

logger = logging.getLogger(__name__)

TRIGGER_WORDS = {"tracking", "track", "airway", "awb", "bill", "waybill"}
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
            await self._answer_tracking_query(chat_id, msg_id, text)
        else:
            await self._forward_to_pawbot(update)

    # ── Query detection ────────────────────────────────────────────────────────

    def _is_tracking_query(self, text: str) -> bool:
        words = set(re.findall(r"[a-zA-Z]+", text.lower()))
        return bool(words & TRIGGER_WORDS)

    # ── Answer ─────────────────────────────────────────────────────────────────

    async def _answer_tracking_query(self, chat_id: str, reply_to: int, text: str) -> None:
        order_number, customer_name = self._extract_query_parts(text)
        logger.info(
            f"Tracking query — order_number={order_number!r}, customer_name={customer_name!r}"
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
            reply = self._format_record(records[0])
        else:
            # Multiple matches — list them concisely
            lines = [f"Found {len(records)} orders:\n"]
            for r in records[:5]:  # cap at 5
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

    def _format_record(self, record: dict) -> str:
        f = record.get("fields", {})
        order_no     = f.get("Order No.") or f.get("Order Number") or f.get("Order ID") or "?"
        customer     = _first(f.get("Customer Name")) or "?"
        airway_bill  = f.get("Airway Bill") or "(not yet purchased)"
        status       = f.get("Process Status") or "?"
        tracking_msg = f.get("Tracking No. Message") or ""

        lines = [
            f"📦 *Order:* {order_no}",
            f"👤 *Customer:* {customer}",
            f"🏷 *Airway Bill / Tracking No.:* `{airway_bill}`",
            f"📋 *Status:* {status}",
        ]
        if tracking_msg:
            lines.append(f"\n{tracking_msg}")

        return "\n".join(lines)

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
