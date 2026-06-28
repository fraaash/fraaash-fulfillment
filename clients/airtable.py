import logging

import httpx

from config import settings

logger = logging.getLogger(__name__)

AIRTABLE_BASE = "https://api.airtable.com/v0"
TABLE_ID = "tblMK2nWUx0XQIVjK"  # Purchase Orders


class AirtableClient:
    def __init__(self):
        self._headers = {
            "Authorization": f"Bearer {settings.AIRTABLE_TOKEN}",
            "Content-Type": "application/json",
        }
        self._base_id = settings.AIRTABLE_BASE_ID

    async def get_record(self, record_id: str) -> dict:
        """Fetch a single Purchase Order record by ID (returns field names, not IDs)."""
        url = f"{AIRTABLE_BASE}/{self._base_id}/{TABLE_ID}/{record_id}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=self._headers)
            resp.raise_for_status()
            return resp.json()

    async def update_record(self, record_id: str, fields: dict) -> dict:
        """Update fields on a Purchase Order record (use field names as keys)."""
        url = f"{AIRTABLE_BASE}/{self._base_id}/{TABLE_ID}/{record_id}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.patch(
                url,
                headers=self._headers,
                json={"fields": fields},
            )
            resp.raise_for_status()
            data = resp.json()
            logger.info(f"Airtable record {record_id} updated: {list(fields.keys())}")
            return data

    async def get_webhook_payloads(self, webhook_id: str, cursor: int | None = None) -> dict:
        """Fetch pending webhook payloads starting from cursor."""
        url = (
            f"{AIRTABLE_BASE}/bases/{self._base_id}"
            f"/webhooks/{webhook_id}/payloads"
        )
        params = {}
        if cursor is not None:
            params["cursor"] = cursor

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=self._headers, params=params)
            resp.raise_for_status()
            return resp.json()
