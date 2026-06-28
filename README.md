# Fraaash Fulfillment Automation

Automated fulfilment pipeline for Fraaash courier orders:

1. **New order detected** (Airtable: Collection Method = *Courier Required*, Process Status = *Pending*)
   → Purchases airway bill from **Ninja Van Malaysia**
   → Saves PDF to **SharePoint** (`7. Operation/Airway Bills/[Month Year]/`)
   → Writes tracking number back to Airtable **Airway Bill** field

2. **Order shipped** (Airtable: Process Status → *Delivered* or *Collected*)
   → Sends the **Tracking No. Message** field to the Telegram **Inventory / Ops group**

---

## Prerequisites

- Python 3.11+
- A Render account (free tier is fine)
- A GitHub repo (push this folder to it)
- Ninja Van Malaysia API credentials
- An Azure AD app registration (for SharePoint)
- A Telegram bot added to your ops group

---

## Step 1 — Ninja Van API credentials

1. Log in to the [Ninja Van Shipper Portal](https://ship.ninjavan.co/my)
2. Go to **Settings → API**
3. Generate a Client ID and Client Secret
4. Save them — you'll need `NINJAVAN_CLIENT_ID` and `NINJAVAN_CLIENT_SECRET`

---

## Step 2 — Azure AD app registration (SharePoint)

1. Go to [portal.azure.com](https://portal.azure.com) → **Azure Active Directory → App registrations → New registration**
2. Name it `fraaash-fulfillment`, leave defaults, click **Register**
3. Copy the **Application (client) ID** → `SHAREPOINT_CLIENT_ID`
4. Copy the **Directory (tenant) ID** → `SHAREPOINT_TENANT_ID`
5. Go to **Certificates & secrets → New client secret**, create one, copy the value → `SHAREPOINT_CLIENT_SECRET`
6. Go to **API permissions → Add a permission → Microsoft Graph → Application permissions**
   - Add `Sites.ReadWrite.All`  *(or `Files.ReadWrite.All` if you prefer a narrower scope)*
7. Click **Grant admin consent**
8. Run the helper script to get the Site ID and Drive ID:
   ```bash
   pip install httpx python-dotenv
   python scripts/get_sharepoint_ids.py
   ```
   Copy the printed `SHAREPOINT_SITE_ID` and `SHAREPOINT_DRIVE_ID` values.

---

## Step 3 — Telegram bot setup

You already have a bot. To use it in the ops/inventory group:

1. Add the bot to the **Inventory / Ops Telegram group** (same way you added it to Fraaash Orders)
2. Send any message in that group
3. Run:
   ```bash
   python scripts/get_telegram_chat_id.py
   ```
4. Copy the chat ID for the ops group (it will be a negative number like `-1001234567890`) → `TELEGRAM_OPS_CHAT_ID`

---

## Step 4 — Fill in `.env`

Copy `.env.example` to `.env` and fill in all values:

```bash
cp .env.example .env
# then edit .env
```

Key values to fill:
| Variable | Where to get it |
|---|---|
| `AIRTABLE_TOKEN` | Airtable → Account → API → Personal access token |
| `NINJAVAN_CLIENT_ID / SECRET` | Ninja Van Shipper Portal → Settings → API |
| `SHIPPER_*` | Your Fraaash warehouse address details |
| `SHAREPOINT_TENANT_ID` | Azure Portal → App registration → Overview |
| `SHAREPOINT_CLIENT_ID` | Azure Portal → App registration → Overview |
| `SHAREPOINT_CLIENT_SECRET` | Azure Portal → Certificates & secrets |
| `SHAREPOINT_SITE_ID` | Run `scripts/get_sharepoint_ids.py` |
| `SHAREPOINT_DRIVE_ID` | Run `scripts/get_sharepoint_ids.py` |
| `TELEGRAM_BOT_TOKEN` | From BotFather |
| `TELEGRAM_OPS_CHAT_ID` | Run `scripts/get_telegram_chat_id.py` |

---

## Step 5 — Deploy to Render

1. Push this folder to a GitHub repo
2. Go to [render.com](https://render.com) → **New → Web Service**
3. Connect your GitHub repo
4. Set:
   - **Runtime**: Python
   - **Build command**: `pip install -r requirements.txt`
   - **Start command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Add all environment variables from your `.env` under **Environment**
6. Deploy — note the URL (e.g. `https://fraaash-fulfillment.onrender.com`)

---

## Step 6 — Register the Airtable webhook

Run once from your local machine (with `.env` filled in):

```bash
RENDER_URL=https://fraaash-fulfillment.onrender.com python scripts/register_webhook.py
```

That's it — the webhook is now live and Airtable will ping your service on every change to the Purchase Orders table.

---

## Project structure

```
fraaash-fulfillment/
├── main.py                        # FastAPI app + webhook endpoint
├── config.py                      # Pydantic settings (reads from .env)
├── requirements.txt
├── .env.example
├── clients/
│   ├── airtable.py                # Airtable REST API
│   ├── ninjavan.py                # Ninja Van MY API (orders + waybill PDF)
│   ├── sharepoint.py              # Microsoft Graph API (upload to SharePoint)
│   └── telegram.py                # Telegram Bot API
├── handlers/
│   └── fulfillment.py             # Business logic (airway bill + Telegram trigger)
└── scripts/
    ├── register_webhook.py        # One-time Airtable webhook registration
    ├── get_sharepoint_ids.py      # Helper to retrieve Site ID + Drive ID
    └── get_telegram_chat_id.py    # Helper to retrieve ops group chat ID
```

---

## How it works

```
Airtable change
      │
      ▼
POST /webhook/airtable  (Render)
      │
      ├─ New record, Courier Required + Pending + no Airway Bill
      │     → Ninja Van: create order → get PDF
      │     → SharePoint: save PDF to 7. Operation/Airway Bills/[Month]/
      │     → Airtable: write tracking number to Airway Bill field
      │
      └─ Status changed to Delivered / Collected (Courier order)
            → Telegram: send Tracking No. Message to ops group
```

---

## Troubleshooting

**Webhook not firing** — Check Render logs. Verify the webhook was registered with `scripts/register_webhook.py` and that the Render URL is correct.

**SharePoint upload fails** — Confirm the Azure app has `Sites.ReadWrite.All` permission with admin consent granted. Re-run `scripts/get_sharepoint_ids.py` to verify the IDs.

**Ninja Van 401** — Client credentials may have expired or be wrong. Regenerate from the Shipper Portal.

**Telegram message not sent** — Make sure the bot is a member of the ops group, and that `TELEGRAM_OPS_CHAT_ID` is the correct (negative) group ID.

**Duplicate airway bills** — The handler checks that the `Airway Bill` field is empty before purchasing. If you see duplicates, check that the field is not being cleared elsewhere.
