import logging
from datetime import datetime, timedelta

import httpx

from config import settings

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
AIRWAY_BILLS_ROOT = "7. Operation/Airway Bills"


class SharePointClient:
    def __init__(self):
        self._token = None
        self._token_expiry = None

    async def _get_token(self):
        if self._token and self._token_expiry and datetime.now() < self._token_expiry:
            return self._token
        url = (
            f"https://login.microsoftonline.com/"
            f"{settings.SHAREPOINT_TENANT_ID}/oauth2/v2.0/token"
        )
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, data={
                "grant_type": "client_credentials",
                "client_id": settings.SHAREPOINT_CLIENT_ID,
                "client_secret": settings.SHAREPOINT_CLIENT_SECRET,
                "scope": "https://graph.microsoft.com/.default",
            })
            resp.raise_for_status()
            data = resp.json()
        self._token = data["access_token"]
        self._token_expiry = datetime.now() + timedelta(seconds=int(data.get("expires_in", 3600)) - 60)
        logger.info("SharePoint (Graph) access token refreshed")
        return self._token

    async def list_airway_bill_pdfs(self):
        token = await self._get_token()
        headers = {"Authorization": f"Bearer {token}"}
        site_id = settings.SHAREPOINT_SITE_ID
        drive_id = settings.SHAREPOINT_DRIVE_ID
        pdfs = []
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            root_url = (
                f"{GRAPH_BASE}/sites/{site_id}/drives/{drive_id}"
                f"/root:/{AIRWAY_BILLS_ROOT}:/children"
            )
            resp = await client.get(root_url, headers=headers)
            resp.raise_for_status()
            for item in resp.json().get("value", []):
                if item.get("file") and item["name"].lower().endswith(".pdf"):
                    pdfs.append({"id": item["id"], "name": item["name"], "parentPath": AIRWAY_BILLS_ROOT})
                elif item.get("folder"):
                    sub = item["name"]
                    # Skip months before July 2026 (handled manually)
                    try:
                        month_num = int(sub.split(".")[0].strip())
                        year_part = sub.rsplit(" ", 1)[-1]
                        if year_part == "2026" and month_num < 7:
                            continue
                    except (ValueError, IndexError):
                        pass
                    sr = await client.get(
                        f"{GRAPH_BASE}/sites/{site_id}/drives/{drive_id}"
                        f"/root:/{AIRWAY_BILLS_ROOT}/{sub}:/children",
                        headers=headers
                    )
                    sr.raise_for_status()
                    for si in sr.json().get("value", []):
                        if si.get("file") and si["name"].lower().endswith(".pdf"):
                            pdfs.append({"id": si["id"], "name": si["name"], "parentPath": f"{AIRWAY_BILLS_ROOT}/{sub}"})
        logger.info(f"Found {len(pdfs)} airway bill PDF(s) in SharePoint")
        return pdfs

    async def download_item(self, item_id):
        token = await self._get_token()
        headers = {"Authorization": f"Bearer {token}"}
        site_id = settings.SHAREPOINT_SITE_ID
        drive_id = settings.SHAREPOINT_DRIVE_ID
        url = f"{GRAPH_BASE}/sites/{site_id}/drives/{drive_id}/items/{item_id}/content"
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.content

    async def upload_airway_bill(self, pdf_bytes, month_folder, filename):
        token = await self._get_token()
        auth_headers = {"Authorization": f"Bearer {token}"}
        site_id = settings.SHAREPOINT_SITE_ID
        drive_id = settings.SHAREPOINT_DRIVE_ID
        folder_path = f"{AIRWAY_BILLS_ROOT}/{month_folder}"
        file_path = f"{folder_path}/{filename}"
        async with httpx.AsyncClient(timeout=60.0) as client:
            await self._ensure_folder(client, auth_headers, site_id, drive_id, folder_path)
            url = f"{GRAPH_BASE}/sites/{site_id}/drives/{drive_id}/root:/{file_path}:/content"
            resp = await client.put(url, headers={**auth_headers, "Content-Type": "application/pdf"}, content=pdf_bytes)
            resp.raise_for_status()
            web_url = resp.json().get("webUrl", "")
            logger.info(f"Airway bill uploaded to SharePoint: {file_path}")
            return web_url

    async def _ensure_folder(self, client, auth_headers, site_id, drive_id, folder_path):
        resp = await client.get(
            f"{GRAPH_BASE}/sites/{site_id}/drives/{drive_id}/root:/{folder_path}",
            headers=auth_headers
        )
        if resp.status_code == 200:
            return
        if resp.status_code == 404:
            parent_path, new_folder = folder_path.rsplit("/", 1)
            if "/" in parent_path:
                await self._ensure_folder(client, auth_headers, site_id, drive_id, parent_path)
            cr = await client.post(
                f"{GRAPH_BASE}/sites/{site_id}/drives/{drive_id}/root:/{parent_path}:/children",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"name": new_folder, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"}
            )
            if cr.status_code not in (200, 201, 409):
                cr.raise_for_status()
            logger.info(f"SharePoint folder created: {folder_path}")
        else:
            resp.raise_for_status()
