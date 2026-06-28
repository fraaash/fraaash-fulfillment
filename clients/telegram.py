import logging

import httpx

from config import settings

logger = logging.getLogger(__name__)


class TelegramClient:
    """Telegram Bot API client for sending messages to the ops group."""

    def __init__(self):
        self._base = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}"

    async def send_message(self, chat_id: str, text: str) -> None:
        """Send a plain-text message to a Telegram chat."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{self._base}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    # Keep parse_mode out so special characters in addresses don't break it
                },
            )
            resp.raise_for_status()
            logger.info(f"Telegram message sent to chat {chat_id}")
