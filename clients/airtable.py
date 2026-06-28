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

    async def search_orders(self, order_number: str = "", customer_name: str = "") -> list:
        """Search Purchase Orders by order number and/or customer name."""
        filters = []
        if order_number:
            filters.append(f'SEARCH("{order_number}", {{Order Number}})')
            filters.append(f'SEARCH("{order_number}", {{Order No.}})')
        if customer_name:
            # Search each word separately for flexibility
            for word in customer_name.split():
                if len(word) >= 2:
                    filters.append(f'SEARCH(LOWER("{word}"), LOWER({{Customer Name}}))')

        if not filters:
            return []

        formula = f'OR({", ".join(filters)})'
        url = f"{AIRTABLE_BASE}/{self._base_id}/{TABLE_ID}"
        params = {
            "filterByFormula": formula,
            "fields[]": [
                "Order ID", "Order Number", "Order No.", "Customer Name",
                "Airway Bill", "Tracking No. Message", "Process Status",
                "Collection Method", "Delivery Date",
            ],
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=self._headers, params=params)
            resp.raise_for_status()
            return resp.json().get("records", [])

    async def get_webhook_payloads(self, webhook_id: str, cursor=None) -> dict:
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
