"""
Run ONCE to retrieve the SharePoint Site ID and Drive (Documents library) ID
that you need to add to your .env / Render environment variables.

Requirements:
  pip install httpx python-dotenv

Usage:
  python scripts/get_sharepoint_ids.py

You need SHAREPOINT_TENANT_ID, SHAREPOINT_CLIENT_ID, SHAREPOINT_CLIENT_SECRET
already set in your .env (or environment) before running this.
"""

import os

import httpx
from dotenv import load_dotenv

load_dotenv()

TENANT_ID     = os.environ["SHAREPOINT_TENANT_ID"]
CLIENT_ID     = os.environ["SHAREPOINT_CLIENT_ID"]
CLIENT_SECRET = os.environ["SHAREPOINT_CLIENT_SECRET"]
SITE_HOST     = "projectpawsdnbhd.sharepoint.com"
SITE_PATH     = "/sites/Fraaash"

# ── 1. Get access token ────────────────────────────────────────────────────────
token_resp = httpx.post(
    f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token",
    data={
        "grant_type":    "client_credentials",
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope":         "https://graph.microsoft.com/.default",
    },
    timeout=15,
)
token_resp.raise_for_status()
token = token_resp.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}

# ── 2. Get Site ID ─────────────────────────────────────────────────────────────
site_resp = httpx.get(
    f"https://graph.microsoft.com/v1.0/sites/{SITE_HOST}:{SITE_PATH}",
    headers=headers,
    timeout=15,
)
site_resp.raise_for_status()
site_data = site_resp.json()
site_id   = site_data["id"]
print(f"\n✅  SHAREPOINT_SITE_ID = {site_id}")

# ── 3. Get Drive (Documents library) ID ───────────────────────────────────────
drives_resp = httpx.get(
    f"https://graph.microsoft.com/v1.0/sites/{site_id}/drives",
    headers=headers,
    timeout=15,
)
drives_resp.raise_for_status()
drives = drives_resp.json().get("value", [])

print("\nAvailable drives / document libraries:")
for d in drives:
    print(f"  [{d['driveType']}]  {d['name']}  →  {d['id']}")

# Pick "Documents" (the default library)
doc_drive = next(
    (d for d in drives if d["name"] in ("Documents", "Shared Documents", "Dokumen")),
    drives[0] if drives else None,
)
if doc_drive:
    print(f"\n✅  SHAREPOINT_DRIVE_ID = {doc_drive['id']}")
    print(f"   (Drive name: {doc_drive['name']})\n")
    print("Add both values to your .env and Render environment variables.")
else:
    print("\n⚠️  Could not auto-detect the Documents drive — copy the correct ID from the list above.")
