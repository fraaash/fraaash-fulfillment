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
        """Search Purchase Orders by order number and/or customer name.

        Priority: if order_number is given, search only by that (much more precise).
        Fall back to customer name only when no order number is provided.
        """
        if order_number:
            # Exact match on Order Number field (e.g. "00423")
            num_filters = [f'{{Order Number}} = "{order_number}"']
            # Also accept without leading zeros (user types "423" instead of "00423")
            stripped = order_number.lstrip("0")
            if stripped and stripped != order_number:
                num_filters.append(f'RIGHT({{Order Number}}, {len(stripped)}) = "{stripped}"')
            formula = f'OR({", ".join(num_filters)})'
        elif customer_name:
            # No order number — search by customer name words (AND logic for precision)
            name_filters = []
            for word in customer_name.split():
                if len(word) >= 2:
                    name_filters.append(
                        f'FIND(LOWER("{word}"), LOWER(ARRAYJOIN({{Customer Name}}, " ")))'
                    )
            if not name_filters:
                return []
            formula = f'AND({", ".join(name_filters)})'
        else:
            return []
        url = f"{AIRTABLE_BASE}/{self._base_id}/{TABLE_ID}"
        # Pass fields[] as a list of tuples so httpx sends repeated params correctly
        params = [
            ("filterByFormula", formula),
            ("fields[]", "Order ID"),
            ("fields[]", "Order Number"),
            ("fields[]", "Order No."),
            ("fields[]", "Customer Name"),
            ("fields[]", "Airway Bill"),
            ("fields[]", "Tracking No. Message"),
            ("fields[]", "Process Status"),
            ("fields[]", "Collection Method"),
            ("fields[]", "Delivery Date"),
        ]
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=self._headers, params=params)
            resp.raise_for_status()
            return resp.json().get("records", [])


    # ── System Config (key-value store for persistent state) ──────────────────

    CONFIG_TABLE_ID = "tblBezI6wEwN96A3l"

    async def get_config(self, key: str):
        """Read a value from the System Config table. Returns None if not found."""
        url = f"{self.BASE_URL}/{self.CONFIG_TABLE_ID}"
        params = {"filterByFormula": f'{{Key}}="{key}"', "fields[]": "Value"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=self._headers, params=params)
            resp.raise_for_status()
            records = resp.json().get("records", [])
            if records:
                return records[0]["fields"].get("Value")
        return None

    async def set_config(self, key: str, value: str) -> None:
        """Upsert a key-value pair in the System Config table."""
        url = f"{self.BASE_URL}/{self.CONFIG_TABLE_ID}"
        params = {"filterByFormula": f'{{Key}}="{key}"', "fields[]": "Value"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=self._headers, params=params)
            resp.raise_for_status()
            records = resp.json().get("records", [])
            if records:
                rid = records[0]["id"]
                await client.patch(
                    f"{url}/{rid}", headers=self._headers,
                    json={"fields": {"Value": value}}
                )
            else:
                await client.post(
                    url, headers=self._headers,
                    json={"records": [{"fields": {"Key": key, "Value": value}}]}
                )

    async def search_orders_by_delivery_date(self, date_iso: str) -> list:
        """Return all Courier Required orders whose Delivery Date matches date_iso (YYYY-MM-DD)."""
        formula = (
            f'AND(DATESTR({{Delivery Date}}) = "{date_iso}", '
            f'{{Collection Method}} = "Courier Required")'
        )
        params = [
            ("filterByFormula", formula),
            ("fields[]", "Order ID"),
            ("fields[]", "Order Number"),
            ("fields[]", "Order No."),
            ("fields[]", "Customer Name"),
            ("fields[]", "Airway Bill"),
            ("fields[]", "Tracking No. Message"),
            ("fields[]", "Process Status"),
            ("fields[]", "Delivery Date"),
        ]
        url = f"{AIRTABLE_BASE}/{self._base_id}/{TABLE_ID}"
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
