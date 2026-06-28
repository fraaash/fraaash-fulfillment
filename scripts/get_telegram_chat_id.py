"""
Prints the chat IDs of all recent Telegram conversations your bot can see.
Run this AFTER adding your bot to the ops/inventory group and sending
any message in that group.

Usage:
  TELEGRAM_BOT_TOKEN=your_token python scripts/get_telegram_chat_id.py
"""

import os
import httpx
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

# If a webhook is active, getUpdates returns 409. Remove it temporarily.
webhook_info = httpx.get(f"{BASE}/getWebhookInfo", timeout=15).json()
existing_webhook = webhook_info.get("result", {}).get("url", "")

if existing_webhook:
    print(f"⚠️  Webhook detected: {existing_webhook}")
    print("Temporarily removing it to fetch chat IDs...\n")
    httpx.post(f"{BASE}/deleteWebhook", timeout=15)
    print("Webhook removed. Re-register it after this script finishes.\n")
    print(f"Your webhook URL to re-register: {existing_webhook}\n")

resp = httpx.get(f"{BASE}/getUpdates", timeout=15)
resp.raise_for_status()
updates = resp.json().get("result", [])

if not updates:
    print("No updates found.")
    print("Make sure you:")
    print("  1. Added the bot to the group")
    print("  2. Sent a message in the group (so the bot received an update)")
    print("  3. Have NOT called getUpdates with offset before (clears history)")
else:
    seen = set()
    print("\nChats your bot has seen:\n")
    for u in updates:
        msg  = u.get("message") or u.get("channel_post") or {}
        chat = msg.get("chat", {})
        cid  = chat.get("id")
        if cid and cid not in seen:
            seen.add(cid)
            print(f"  Chat ID : {cid}")
            print(f"  Type    : {chat.get('type')}")
            print(f"  Title   : {chat.get('title') or chat.get('username') or chat.get('first_name')}")
            print()
    print("Set TELEGRAM_OPS_CHAT_ID to the ID of your inventory/ops group (negative number).")
