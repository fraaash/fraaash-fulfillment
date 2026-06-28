import logging
from datetime import datetime, timedelta

import httpx

from config import settings

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
# SharePoint path: Documents/7. Operation/Airway Bills/{Month Year}/{filename}
AIRWAY_BILLS_ROOT = "7. Operation/Airway Bills"


class SharePointClient:
    """Microsoft Graph API client for uploading airway bill PDFs to SharePoint."""

    def __init__(self):
        self._token: str | None = None
        self._token_expiry: datetime | None = None

    # ── Auth ──────────────────────────────────────────────────────────────────

    async def _get_token(self) -> str:
        if self._token and self._token_expiry and datetime.now() < self._token_expiry:
            return self._token

        url = (
            f"https://login.microsoftonline.com/"
            f"{settings.SHAREPOINT_TENANT_ID}/oauth2/v2.0/token"
        )
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": settings.SHAREPOINT_CLIENT_ID,
                    "client_secret": settings.SHAREPOINT_CLIENT_SECRET,
                    "scope": "https://graph.microsoft.com/.default",
                },
            )
            resp.raise_for_status()
            data = resp.json()

        self._token = data["access_token"]
        expires_in = int(data.get("expires_in", 3600))
        self._token_expiry = datetime.now() + timedelta(seconds=expires_in - 60)
        logger.info("SharePoint (Graph) access token refreshed")
        return self._token

    # ── Upload ────────────────────────────────────────────────────────────────

    async def upload_airway_bill(
        self,
        pdf_bytes: bytes,
        month_folder: str,
        filename: str,
    ) -> str:
        """
        Upload a PDF to:
          Documents/7. Operation/Airway Bills/{month_folder}/{filename}

        Returns the SharePoint web URL of the uploaded file.
        """
        token = await self._get_token()
        auth_headers = {"Authorization": f"Bearer {token}"}
        site_id = settings.SHAREPOINT_SITE_ID
        drive_id = settings.SHAREPOINT_DRIVE_ID

        folder_path = f"{AIRWAY_BILLS_ROOT}/{month_folder}"
        file_path = f"{folder_path}/{filename}"

        async with httpx.AsyncClient(timeout=60.0) as client:
            # Ensure the month folder exists (creates it if missing)
            await self._ensure_folder(client, auth_headers, site_id, drive_id, folder_path)

            # Upload via Graph simple upload (works for files < 4 MB)
            upload_url = (
                f"{GRAPH_BASE}/sites/{site_id}/drives/{drive_id}"
                f"/root:/{file_path}:/content"
            )
            resp = await client.put(
                upload_url,
                headers={**auth_headers, "Content-Type": "application/pdf"},
                content=pdf_bytes,
            )
            resp.raise_for_status()
            web_url: str = resp.json().get("webUrl", "")
            logger.info(f"Airway bill uploaded to SharePoint: {file_path}")
            return web_url

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _ensure_folder(
        self,
        client: httpx.AsyncClient,
        auth_headers: dict,
        site_id: str,
        drive_id: str,
        folder_path: str,
    ) -> None:
        """Create the folder (and any missing parents) if it doesn't already exist."""
        check_url = (
            f"{GRAPH_BASE}/sites/{site_id}/drives/{drive_id}/root:/{folder_path}"
        )
        resp = await client.get(check_url, headers=auth_headers)

        if resp.status_code == 200:
            return  # folder already exists

        if resp.status_code == 404:
            # Split into parent path + new folder name and create it
            parts = folder_path.rsplit("/", 1)
            parent_path = parts[0]
            new_folder = parts[1]

            # Recursively ensure parent exists first
            if "/" in parent_path:
                await self._ensure_folder(
                    client, auth_headers, site_id, drive_id, parent_path
                )

            parent_url = (
                f"{GRAPH_BASE}/sites/{site_id}/drives/{drive_id}"
                f"/root:/{parent_path}:/children"
            )
            create_resp = await client.post(
                parent_url,
                headers={**auth_headers, "Content-Type": "application/json"},
                json={
                    "name": new_folder,
                    "folder": {},
                    "@microsoft.graph.conflictBehavior": "fail",
                },
            )
            # 409 Conflict = folder already exists (race condition) — that's fine
            if create_resp.status_code not in (200, 201, 409):
                create_resp.raise_for_status()
            logger.info(f"SharePoint folder created: {folder_path}")
        else:
            resp.raise_for_status()
