from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Airtable
    AIRTABLE_TOKEN: str
    AIRTABLE_BASE_ID: str = "appqaeML2BR2aklix"

    # Ninja Van Malaysia
    NINJAVAN_CLIENT_ID: str
    NINJAVAN_CLIENT_SECRET: str

    # Your Fraaash shipper details (used on every airway bill)
    SHIPPER_NAME: str = "Fraaash"
    SHIPPER_PHONE: str          # e.g. +60123456789
    SHIPPER_EMAIL: str = "ops@fraaash.com"
    SHIPPER_ADDRESS: str
    SHIPPER_CITY: str
    SHIPPER_STATE: str
    SHIPPER_POSTCODE: str
    DEFAULT_PARCEL_WEIGHT_KG: float = 2.0

    # SharePoint / Microsoft Graph
    SHAREPOINT_TENANT_ID: str   # Azure AD tenant ID
    SHAREPOINT_CLIENT_ID: str   # Azure AD app (client) ID
    SHAREPOINT_CLIENT_SECRET: str
    SHAREPOINT_SITE_ID: str     # from scripts/get_sharepoint_ids.py
    SHAREPOINT_DRIVE_ID: str    # from scripts/get_sharepoint_ids.py

    # Telegram
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_OPS_CHAT_ID: str   # the inventory/ops group chat ID (negative number)

    class Config:
        env_file = ".env"


settings = Settings()
