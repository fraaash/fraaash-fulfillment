import logging
from datetime import datetime, timedelta

import httpx

from config import settings

logger = logging.getLogger(__name__)

NV_BASE       = "https://api.ninjavan.co/MY"
NV_AUTH_BASE  = "https://aaa.ninjavan.co/MY"   # OAuth uses different subdomain


class NinjaVanClient:
    """Ninja Van Malaysia API client -- handles OAuth2 and order creation."""

    def __init__(self):
        self._token: str | None = None
        self._token_expiry: datetime | None = None

    # Auth

    async def _get_token(self) -> str:
        if self._token and self._token_expiry and datetime.now() < self._token_expiry:
            return self._token

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{NV_AUTH_BASE}/2.0/oauth/access_token",
                json={
                    "grant_type": "client_credentials",
                    "client_id": settings.NINJAVAN_CLIENT_ID,
                    "client_secret": settings.NINJAVAN_CLIENT_SECRET,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        self._token = data["access_token"]
        expires_in = int(data.get("expires_in", 3600))
        self._token_expiry = datetime.now() + timedelta(seconds=expires_in - 60)
        logger.info("Ninja Van access token refreshed")
        return self._token

    # Order creation

    async def create_order(
        self,
        customer_name: str,
        address: str,
        contact: str,
        state: str,
        postcode: str,
        order_reference: str,
        parcel_description: str,
        delivery_date: str | None = None,
    ) -> tuple[str, bytes]:
        token = await self._get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        if not delivery_date:
            delivery_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        payload = {
            "service_type": "Parcel",
            "service_level": "Standard",
            "from": {
                "name": settings.SHIPPER_NAME,
                "phone_number": settings.SHIPPER_PHONE,
                "email": settings.SHIPPER_EMAIL,
                "address": {
                    "address1": settings.SHIPPER_ADDRESS,
                    "city": settings.SHIPPER_CITY,
                    "state": settings.SHIPPER_STATE,
                    "country": "MY",
                    "postcode": settings.SHIPPER_POSTCODE,
                    "address_type": "office",
                },
            },
            "to": {
                "name": customer_name or "Customer",
                "phone_number": self._normalise_phone(contact),
                "address": {
                    "address1": address or "-",
                    "country": "MY",
                    "state": state or "",
                    "postcode": postcode or "",
                    "address_type": "home",
                },
            },
            "parcel_job": {
                "is_pickup_required": False,
                "delivery_start_date": delivery_date,
                "delivery_timeslot": {
                    "start_time": "09:00",
                    "end_time": "22:00",
                    "timezone": "Asia/Kuala_Lumpur",
                },
                "dimensions": {
                    "weight": settings.DEFAULT_PARCEL_WEIGHT_KG,
                },
                "items": [
                    {
                        "item_description": parcel_description or "Pet Food",
                        "quantity": 1,
                        "is_dangerous_good": False,
                    }
                ],
                "allow_weekend_delivery": True,
                "purchase_order_number": order_reference or "",
            },
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{NV_BASE}/4.2/orders",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            order_data = resp.json()
            tracking_number: str = order_data["tracking_number"]
            logger.info(f"Ninja Van order created -- tracking: {tracking_number}")

            pdf_resp = await client.get(
                f"{NV_BASE}/2.0/reports/waybill",
                headers=headers,
                params={
                    "tid": tracking_number,
                    "hide_shipper_details": "false",
                },
                timeout=30.0,
            )
            pdf_resp.raise_for_status()
            pdf_bytes = pdf_resp.content

        return tracking_number, pdf_bytes

    @staticmethod
    def _normalise_phone(phone: str) -> str:
        if not phone:
            return settings.SHIPPER_PHONE
        p = phone.strip().replace(" ", "").replace("-", "")
        if p.startswith("0"):
            return "+60" + p[1:]
        if p.startswith("60") and not p.startswith("+"):
            return "+" + p
        if not p.startswith("+"):
            return "+60" + p
        return p
