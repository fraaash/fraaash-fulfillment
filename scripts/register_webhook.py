"""
Run ONCE after deploying to Render to register the Airtable webhook.

Usage:
  RENDER_URL=https://your-app.onrender.com python scripts/register_webhook.py
"""

import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

AIRTABLE_TOKEN = os.environ["AIRTABLE_TOKEN"]
BASE_ID        = os.getenv("AIRTABLE_BASE_ID", "appqaeML2BR2aklix")
TABLE_ID       = "tblMK2nWUx0XQIVjK"
RENDER_URL     = os.environ.get("RENDER_URL", "").rstrip("/")

if not RENDER_URL:
    print("❌  Set RENDER_URL first:  export RENDER_URL=https://your-app.onrender.com")
    sys.exit(1)

headers = {
    "Authorization": f"Bearer {AIRTABLE_TOKEN}",
    "Content-Type":  "application/json",
}

payload = {
    "notificationUrl": f"{RENDER_URL}/webhook/airtable",
    "specification": {
        "options": {
            "filters": {
                "fromSources": ["client"],
                "dataTypes":   ["tableData"],
                "recordChangeScope": TABLE_ID,
            },
            "includes": {
                "includePreviousCellValues":      True,
                "includePreviousFieldDefinitions": False,
            },
        }
    },
}

resp = httpx.post(
    f"https://api.airtable.com/v0/bases/{BASE_ID}/webhooks",
    headers=headers,
    json=payload,
    timeout=15,
)
resp.raise_for_status()
data = resp.json()

print("✅  Webhook registered!")
print(f"   Webhook ID : {data['id']}")
print(f"   MAC secret : {data.get('macSecret', 'N/A')}")
print(f"   Cursor URL : https://api.airtable.com/v0/bases/{BASE_ID}/webhooks/{data['id']}/payloads")
print()
print("Save the Webhook ID — you'll need it if you ever want to inspect or delete this webhook.")
