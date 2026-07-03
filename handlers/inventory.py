"""
handlers/inventory.py

Handles inventory management queries from the Fraaash Operation Telegram group.

Trigger phrases Pawbot understands (case-insensitive):
  Stock query:       "how many bb left", "how many bawk bawk", "stock", "inventory"
  Delivery out:      "19 BB and 18 GG delivered today", "sent 10 chicken 5 salmon"
  Production plan:   "plan 50 BB 50 GG on 5 July", "produce 70 bawk bawk 50 gulu gulu tomorrow"
  Actual produced:   "produced 48 BB 47 GG today", "completed production 70 BB 50 GG"
  Packaging check:   "check packaging stock", "foam box low", "packaging alert"
"""

import logging
import math
import re
from datetime import date, timedelta
from typing import Optional

import httpx

from config import settings

logger = logging.getLogger(__name__)

# ── Airtable constants ─────────────────────────────────────────────────────────
INV_BASE            = "app4Rm9ZIGWaFeCf4"
INV_MOVEMENT_TABLE  = "tblSx11BYxubiGdHk"
PRODUCTION_TABLE    = "tbl5BrwA9TxTWWt1c"
PACKAGING_MAT_TABLE = "tblhtEc389AR40yLn"
PACKAGING_MOV_TABLE = "tblDBhUN82TQR7Rgp"
INGREDIENT_PO_TABLE = "tblDISF7CjOFCYYxF"
AT_API              = "https://api.airtable.com/v0"

HAN_KEE_ID = "recHng8u15qAyZdnd"
INGREDIENT_IDS = {
    "chicken_breast": "recgqmf9fTRKC25fl",
    "chicken_heart":  "recbv1UR5HhNrxd66",
    "chicken_liver":  "recz9WtL78CvV9QSh",
}

# ── Telegram constants ─────────────────────────────────────────────────────────
TG_BASE     = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}"
OPS_CHAT_ID = str(settings.TELEGRAM_OPS_CHAT_ID)

# ── Trigger word sets ──────────────────────────────────────────────────────────
STOCK_WORDS     = {"stock", "inventory", "available", "left", "remaining"}
PRODUCT_WORDS   = {"bawk", "gulu", "chicken", "salmon", "bb", "gg"}
PACKAGING_WORDS = {"foam", "sleeve", "label", "packaging", "ice", "tape", "card"}
PLAN_WORDS      = {"plan", "produce", "production", "batch", "planning", "prepare", "preparing"}
PRODUCED_WORDS  = {"produced", "completed", "done", "finished", "actual"}
PKG_CHECK_WORDS = {"check", "alert", "low", "reorder"}

MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


class InventoryHandler:
    """Handles all inventory-related Telegram commands for the Fraaash ops group."""

    # ── Detection ──────────────────────────────────────────────────────────────
    def is_inventory_query(self, text: str) -> bool:
        lower = text.lower()
        words = set(re.findall(r"[a-zA-Z]+", lower))
        return self._detect_intent(lower, words) is not None

    def _detect_intent(self, lower: str, words: set) -> Optional[str]:
        # Order matters — more specific checks first
        if self._is_production_actual(lower, words):  return "production_actual"
        if self._is_production_plan(lower, words):    return "production_plan"
        if self._is_inventory_out(lower, words):      return "inventory_out"
        if self._is_packaging_check(lower, words):    return "packaging_check"
        if self._is_stock_query(lower, words):        return "stock_query"
        return None

    def _is_stock_query(self, lower: str, words: set) -> bool:
        has_stock    = bool(words & STOCK_WORDS)
        has_product  = bool(words & PRODUCT_WORDS)
        has_pkg      = bool(words & PACKAGING_WORDS)
        has_how      = "how" in words
        return has_stock or (has_how and (has_product or has_pkg))

    def _is_inventory_out(self, lower: str, words: set) -> bool:
        OUT_WORDS = {"delivered", "deliver", "sent", "shipped", "dispatched", "courier"}
        has_out     = bool(words & OUT_WORDS)
        has_product = bool(words & PRODUCT_WORDS)
        has_qty     = bool(re.search(r"\b\d+\b", lower))
        # 4-6 digit numbers are order IDs — leave those to the order handler
        has_order   = bool(re.search(r"\b\d{4,6}\b", lower))
        return has_out and has_product and has_qty and not has_order

    def _is_production_plan(self, lower: str, words: set) -> bool:
        return bool(words & PLAN_WORDS) and bool(re.search(r"\b\d+\b", lower)) and bool(words & PRODUCT_WORDS)

    def _is_production_actual(self, lower: str, words: set) -> bool:
        return bool(words & PRODUCED_WORDS) and bool(re.search(r"\b\d+\b", lower)) and bool(words & PRODUCT_WORDS)

    def _is_packaging_check(self, lower: str, words: set) -> bool:
        return bool(words & PACKAGING_WORDS) and bool(words & PKG_CHECK_WORDS)

    # ── Main dispatcher ────────────────────────────────────────────────────────
    async def handle(self, chat_id: str, msg_id: int, text: str) -> None:
        lower  = text.lower()
        words  = set(re.findall(r"[a-zA-Z]+", lower))
        intent = self._detect_intent(lower, words)
        try:
<<<<<<< Updated upstream
            if intent == "stock_query":       await self._stock_query(chat_id, msg_id, text)
            elif intent == "inventory_out":   await self._inventory_out(chat_id, msg_id, text)
            elif intent == "production_plan": await self._production_plan(chat_id, msg_id, text)
            elif intent == "production_actual": await self._production_actual(chat_id, msg_id, text)
            elif intent == "packaging_check": await self._packaging_check(chat_id, msg_id)
            else:
                await self._send(chat_id, (
                    "❓ I couldn't understand that. Try:\n"
=======
            if intent == "stock_query":         await self._stock_query(chat_id, msg_id, text)
            elif intent == "inventory_out":     await self._inventory_out(chat_id, msg_id, text)
            elif intent == "production_plan":   await self._production_plan(chat_id, msg_id, text)
            elif intent == "production_actual": await self._production_actual(chat_id, msg_id, text)
            elif intent == "packaging_check":   await self._packaging_check(chat_id, msg_id)
            else:
                await self._send(chat_id, (
                    "❓ I couldn\'t understand that. Try:\n"
>>>>>>> Stashed changes
                    "• *how many bb left*\n"
                    "• *19 BB and 18 GG delivered today*\n"
                    "• *plan 50 BB 50 GG on 5 July*\n"
                    "• *produced 48 BB 47 GG today*\n"
                    "• *check packaging stock*"
                ), msg_id)
        except Exception as exc:
            logger.error(f"InventoryHandler error (intent={intent}): {exc}", exc_info=True)
            await self._send(chat_id, "⚠️ Something went wrong. Please try again.", msg_id)

    # ── 1. Stock query ─────────────────────────────────────────────────────────
    async def _stock_query(self, chat_id: str, msg_id: int, text: str) -> None:
        lower = text.lower()
        words = set(re.findall(r"[a-zA-Z]+", lower))
        ask_product = bool(words & PRODUCT_WORDS) or not bool(words & PACKAGING_WORDS)
        ask_pkg     = bool(words & PACKAGING_WORDS) or not bool(words & PRODUCT_WORDS)

<<<<<<< Updated upstream
        lines = ["📦 *Fraaash Inventory*\n"]
=======
        lines = ["\U0001f4e6 *Fraaash Inventory*\n"]
>>>>>>> Stashed changes

        if ask_product:
            bb, gg = await self._get_product_stock()
            lines += [
<<<<<<< Updated upstream
                "*🐔 Product Stock:*",
=======
                "*\U0001f414 Product Stock:*",
>>>>>>> Stashed changes
                f"• Bawk Bawk (BB): *{bb} boxes*",
                f"• Gulu Gulu (GG): *{gg} boxes*",
                "",
            ]
        if ask_pkg:
            pkg = await self._get_packaging_stock()
<<<<<<< Updated upstream
            lines.append("*📦 Packaging Stock:*")
=======
            lines.append("*\U0001f4e6 Packaging Stock:*")
>>>>>>> Stashed changes
            for name, qty, reorder in pkg:
                warn = " ⚠️ LOW" if reorder is not None and qty <= reorder else ""
                lines.append(f"• {name}: *{qty}*{warn}")

        await self._send(chat_id, "\n".join(lines), msg_id)

    # ── 2. Inventory out (delivery) ────────────────────────────────────────────
    async def _inventory_out(self, chat_id: str, msg_id: int, text: str) -> None:
        bb, gg = self._extract_bb_gg(text)
        if bb == 0 and gg == 0:
<<<<<<< Updated upstream
            await self._send(chat_id, "❓ Couldn't find quantities. Try: *19 BB and 18 GG delivered today*", msg_id)
=======
            await self._send(chat_id, "❓ Couldn\'t find quantities. Try: *19 BB and 18 GG delivered today*", msg_id)
>>>>>>> Stashed changes
            return

        target_date = self._extract_date(text) or date.today()
        date_iso    = target_date.isoformat()
        date_label  = target_date.strftime("%-d %B %Y")

        fields: dict = {
            "fldnkV4GeBZmNe8Fy": date_iso,
            "fldESxOVa6nglAy0J": "Out",
            "fldkGMAzNKJzDBYCS": f"Courier delivery – {date_label}",
        }
        if bb: fields["fld2O5oOrRAaABCr9"] = bb;  fields["fldUYRurduQ37qopd"] = bb * 6
        if gg: fields["fld2uxP8aLheTQwQN"] = gg;  fields["fldROm2Yl2Le2W7Vn"] = gg * 6

        await self._at_create(INV_MOVEMENT_TABLE, fields)
        new_bb, new_gg = await self._get_product_stock()

        lines = [f"✅ *Inventory Out — {date_label}*", ""]
<<<<<<< Updated upstream
        if bb: lines.append(f"🐔 BB out: *{bb} boxes*")
        if gg: lines.append(f"🐟 GG out: *{gg} boxes*")
=======
        if bb: lines.append(f"\U0001f414 BB out: *{bb} boxes*")
        if gg: lines.append(f"\U0001f41f GG out: *{gg} boxes*")
>>>>>>> Stashed changes
        lines += ["", "*Updated stock:*", f"• BB: *{new_bb} boxes*", f"• GG: *{new_gg} boxes*"]
        await self._send(chat_id, "\n".join(lines), msg_id)

    # ── 3. Plan production batch ───────────────────────────────────────────────
    async def _production_plan(self, chat_id: str, msg_id: int, text: str) -> None:
        bb, gg = self._extract_bb_gg(text)
        if bb == 0 and gg == 0:
<<<<<<< Updated upstream
            await self._send(chat_id, "❓ Couldn't find quantities. Try: *plan 50 BB 50 GG on 5 July*", msg_id)
=======
            await self._send(chat_id, "❓ Couldn\'t find quantities. Try: *plan 50 BB 50 GG on 5 July*", msg_id)
>>>>>>> Stashed changes
            return

        target_date = self._extract_date(text) or date.today()
        date_iso    = target_date.isoformat()
        date_label  = target_date.strftime("%-d %B %Y")
        batch_id    = f"BATCH-{target_date.strftime('%y%m%d')}"
        today_iso   = date.today().isoformat()

        # Create production batch record
        batch        = await self._at_create(PRODUCTION_TABLE, {
            "fldyCdkRmFuFhUXX0": batch_id,
            "fldxjxVuLMBDNlMPP": date_iso,
            "fldKTZYSlr63HIOHf": "Planned",
            "fldx2AMZUKF7DNFCN": bb,
            "fldXt1jLSVSblAxTW": gg,
        })
        batch_rec_id = batch["id"]

        # Create Han Kee purchase orders
        ing       = _calc_ingredients(bb, gg)
        po_prefix = f"PO-{target_date.strftime('%y%m%d')}"
        for i, (ing_id, qty, price) in enumerate([
            (INGREDIENT_IDS["chicken_breast"], ing["chicken_breast_kg"], 14.20),
            (INGREDIENT_IDS["chicken_heart"],  ing["chicken_heart_kg"],  8.60),
            (INGREDIENT_IDS["chicken_liver"],  ing["chicken_liver_kg"],  3.00),
        ], start=1):
            await self._at_create(INGREDIENT_PO_TABLE, {
                "fld9NjFpMMnurNFQ4": f"{po_prefix}-{i:03d}",
                "fldxBtLd4fhLGZo9q": today_iso,
                "fld4yP3qygTv9MsoP": [ing_id],
                "fldkMcpr0BiWkPGea": [HAN_KEE_ID],
                "fldu4Gpafhqi7s26V": [batch_rec_id],
                "fldNsJHdwwZIc4lFi": qty,
                "fld2vro40iBPCNv3C": "kg",
                "fldz34KRZOFbQrMqS": price,
                "fldC7JZf98Iy1D1T5": "To Order",
            })

        eggs = math.ceil(ing["egg_yolk_kg"] * 1000 / 13)
        lines = [
            f"✅ *{batch_id} planned — {date_label}*",
<<<<<<< Updated upstream
            f"🐔 BB: *{bb} boxes*  |  🐟 GG: *{gg} boxes*",
            "",
            "📋 *Ingredients to buy:*",
            "",
            "🏭 *Han Kee Processing* (POs logged as To Order):",
            f"  • Chicken Breast: *{ing['chicken_breast_kg']} kg*",
            f"  • Chicken Heart: *{ing['chicken_heart_kg']} kg*",
            f"  • Chicken Liver: *{ing['chicken_liver_kg']} kg*",
            "",
            "🛒 *Other ingredients:*",
            f"  • Salmon: *{ing['salmon_kg']} kg*",
            f"  • Egg Yolk: *{ing['egg_yolk_kg']} kg* (~{eggs} eggs)",
            f"  • Pumpkin: *{ing['pumpkin_kg']} kg*",
            f"  • Carrot: *{ing['carrot_kg']} kg*",
            f"  • Salmon Oil: *{ing['salmon_oil_g']} g*",
            f"  • Feline Multivitamin: *{ing['multivitamin_g']} g*",
            f"  • Eggshell Powder: *{ing['eggshell_g']} g*",
            f"  • Taurine: *{ing['taurine_g']} g*",
            "",
            "📦 *Packaging needed:*",
            f"  • Sleeve Labels: *{ing['sleeve_labels']} pcs*",
            f"  • Packaging Boxes: *{ing['packaging_boxes']} pcs*",
=======
            f"\U0001f414 BB: *{bb} boxes*  |  \U0001f41f GG: *{gg} boxes*",
            "",
            "\U0001f4cb *Ingredients to buy:*",
            "",
            "\U0001f3ed *Han Kee Processing* (POs logged as To Order):",
            f"  • Chicken Breast: *{ing[\'chicken_breast_kg\']} kg*",
            f"  • Chicken Heart: *{ing[\'chicken_heart_kg\']} kg*",
            f"  • Chicken Liver: *{ing[\'chicken_liver_kg\']} kg*",
            "",
            "\U0001f6d2 *Other ingredients:*",
            f"  • Salmon: *{ing[\'salmon_kg\']} kg*",
            f"  • Egg Yolk: *{ing[\'egg_yolk_kg\']} kg* (~{eggs} eggs)",
            f"  • Pumpkin: *{ing[\'pumpkin_kg\']} kg*",
            f"  • Carrot: *{ing[\'carrot_kg\']} kg*",
            f"  • Salmon Oil: *{ing[\'salmon_oil_g\']} g*",
            f"  • Feline Multivitamin: *{ing[\'multivitamin_g\']} g*",
            f"  • Eggshell Powder: *{ing[\'eggshell_g\']} g*",
            f"  • Taurine: *{ing[\'taurine_g\']} g*",
            "",
            "\U0001f4e6 *Packaging needed:*",
            f"  • Sleeve Labels: *{ing[\'sleeve_labels\']} pcs*",
            f"  • Packaging Boxes: *{ing[\'packaging_boxes\']} pcs*",
>>>>>>> Stashed changes
        ]
        await self._send(chat_id, "\n".join(lines), msg_id)

    # ── 4. Update actual production ────────────────────────────────────────────
    async def _production_actual(self, chat_id: str, msg_id: int, text: str) -> None:
        bb, gg = self._extract_bb_gg(text)
        if bb == 0 and gg == 0:
<<<<<<< Updated upstream
            await self._send(chat_id, "❓ Couldn't find quantities. Try: *produced 48 BB 47 GG today*", msg_id)
=======
            await self._send(chat_id, "❓ Couldn\'t find quantities. Try: *produced 48 BB 47 GG today*", msg_id)
>>>>>>> Stashed changes
            return

        target_date = self._extract_date(text) or date.today()
        date_iso    = target_date.isoformat()
        date_label  = target_date.strftime("%-d %B %Y")

        batch = await self._find_planned_batch(date_iso)
        if not batch:
            await self._send(
                chat_id,
                f"❌ No planned batch found for *{date_label}*.\n"
                f"Create one first: *plan {bb} BB {gg} GG on {date_label}*",
                msg_id,
            )
            return

        batch_id     = batch["fields"].get("fldyCdkRmFuFhUXX0", "?")
        batch_rec_id = batch["id"]

<<<<<<< Updated upstream
        # Mark batch completed with actual quantities
=======
>>>>>>> Stashed changes
        await self._at_update(PRODUCTION_TABLE, batch_rec_id, {
            "fldexuMJ3JM5RAFTw": bb,
            "fldM1yMQ0h1Okxpa2": gg,
            "fldKTZYSlr63HIOHf": "Completed",
        })

<<<<<<< Updated upstream
        # Log inventory In movement
=======
>>>>>>> Stashed changes
        inv_fields: dict = {
            "fldnkV4GeBZmNe8Fy": date_iso,
            "fldESxOVa6nglAy0J": "In",
            "fldkGMAzNKJzDBYCS": f"Production {batch_id}",
            "fld0rN66vMWP6D33r": [batch_rec_id],
        }
        if bb: inv_fields["fld2O5oOrRAaABCr9"] = bb;  inv_fields["fldUYRurduQ37qopd"] = bb * 6
        if gg: inv_fields["fld2uxP8aLheTQwQN"] = gg;  inv_fields["fldROm2Yl2Le2W7Vn"] = gg * 6
        await self._at_create(INV_MOVEMENT_TABLE, inv_fields)

        new_bb, new_gg = await self._get_product_stock()
        lines = [f"✅ *{batch_id} completed — {date_label}*", ""]
<<<<<<< Updated upstream
        if bb: lines.append(f"🐔 BB produced: *{bb} boxes*")
        if gg: lines.append(f"🐟 GG produced: *{gg} boxes*")
=======
        if bb: lines.append(f"\U0001f414 BB produced: *{bb} boxes*")
        if gg: lines.append(f"\U0001f41f GG produced: *{gg} boxes*")
>>>>>>> Stashed changes
        lines += ["", "*Updated stock:*", f"• BB: *{new_bb} boxes*", f"• GG: *{new_gg} boxes*"]
        await self._send(chat_id, "\n".join(lines), msg_id)

    # ── 5. Packaging check ─────────────────────────────────────────────────────
    async def _packaging_check(self, chat_id: str, msg_id: Optional[int] = None) -> None:
        pkg = await self._get_packaging_stock()
        low = [(n, q, r) for n, q, r in pkg if r is not None and q <= r]

        if not low:
            lines = ["✅ *Packaging Stock — All Good*", ""]
            for name, qty, reorder in pkg:
                lines.append(f"• {name}: {qty}" + (f"  _(reorder ≤ {reorder})_" if reorder else ""))
        else:
            lines = ["⚠️ *Packaging Alert — Low Stock!*", "", "*Need to reorder:*"]
            for name, qty, reorder in low:
<<<<<<< Updated upstream
                lines.append(f"• 🔴 {name}: *{qty}* (reorder at {reorder})")
            lines += ["", "*Full stock:*"]
            for name, qty, reorder in pkg:
                icon = "🔴" if reorder is not None and qty <= reorder else "🟢"
=======
                lines.append(f"• \U0001f534 {name}: *{qty}* (reorder at {reorder})")
            lines += ["", "*Full stock:*"]
            for name, qty, reorder in pkg:
                icon = "\U0001f534" if reorder is not None and qty <= reorder else "\U0001f7e2"
>>>>>>> Stashed changes
                lines.append(f"• {icon} {name}: {qty}")

        await self._send(chat_id, "\n".join(lines), msg_id)

    async def check_packaging_alert(self) -> None:
<<<<<<< Updated upstream
        """
        Called automatically from fulfillment.py after packaging movements.
        Sends an alert to the ops group only if something is below reorder level.
        """
=======
>>>>>>> Stashed changes
        pkg = await self._get_packaging_stock()
        low = [(n, q, r) for n, q, r in pkg if r is not None and q <= r]
        if low:
            lines = ["⚠️ *Packaging Low Stock Alert*", ""]
            for name, qty, reorder in low:
<<<<<<< Updated upstream
                lines.append(f"• 🔴 {name}: *{qty} remaining* (reorder at {reorder})")
=======
                lines.append(f"• \U0001f534 {name}: *{qty} remaining* (reorder at {reorder})")
>>>>>>> Stashed changes
            await self._send(OPS_CHAT_ID, "\n".join(lines))

    # ── Airtable data helpers ──────────────────────────────────────────────────
    async def _get_product_stock(self) -> tuple[int, int]:
        records = await self._at_list(
            INV_MOVEMENT_TABLE,
            fields=["fldESxOVa6nglAy0J", "fld2O5oOrRAaABCr9", "fld2uxP8aLheTQwQN"],
        )
        bb = gg = 0
        for r in records:
            f   = r.get("fields", {})
            mv  = f.get("fldESxOVa6nglAy0J", "")
            chk = int(f.get("fld2O5oOrRAaABCr9") or 0)
            slm = int(f.get("fld2uxP8aLheTQwQN") or 0)
            if mv == "In":
                bb += chk; gg += slm
            elif mv == "Out":
                bb -= chk; gg -= slm
        return bb, gg

    async def _get_packaging_stock(self) -> list[tuple[str, int, Optional[int]]]:
        mats = await self._at_list(PACKAGING_MAT_TABLE, fields=["fld6a4n4QQWt8Y9yi", "fldZdBtvuOvGEWvLk"])
        movs = await self._at_list(PACKAGING_MOV_TABLE, fields=["fldiUMD8gv9zPvmkT", "fldLQeLb2F89lTrNA", "fldmzsyBN25Swiuhb"])

        balances: dict[str, int] = {}
        for mov in movs:
            f   = mov.get("fields", {})
            ids = f.get("fldiUMD8gv9zPvmkT", [])
            mv  = f.get("fldLQeLb2F89lTrNA", "")
            qty = int(f.get("fldmzsyBN25Swiuhb") or 0)
            for mid in ids:
                balances.setdefault(mid, 0)
                if mv == "In":    balances[mid] += qty
                elif mv == "Out": balances[mid] -= qty

        return [
            (
                m.get("fields", {}).get("fld6a4n4QQWt8Y9yi", "Unknown"),
                balances.get(m["id"], 0),
                m.get("fields", {}).get("fldZdBtvuOvGEWvLk"),
            )
            for m in mats
        ]

    async def _find_planned_batch(self, date_iso: str) -> Optional[dict]:
        records = await self._at_list(
            PRODUCTION_TABLE,
<<<<<<< Updated upstream
            formula=f"AND({{Batch Date}}='{date_iso}',{{Status}}='Planned')",
=======
            formula=f"AND({{Batch Date}}=\'{date_iso}\',{{Status}}=\'Planned\')",
>>>>>>> Stashed changes
        )
        return records[0] if records else None

    # ── Airtable HTTP ──────────────────────────────────────────────────────────
    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {settings.AIRTABLE_TOKEN}",
            "Content-Type": "application/json",
        }

    async def _at_list(self, table_id: str, fields: list[str] = None, formula: str = None) -> list[dict]:
        url    = f"{AT_API}/{INV_BASE}/{table_id}"
<<<<<<< Updated upstream
        params: dict = {}
=======
        params: dict = {"returnFieldsByFieldId": "true"}
>>>>>>> Stashed changes
        if fields:  params["fields[]"] = fields
        if formula: params["filterByFormula"] = formula
        records, offset = [], None
        async with httpx.AsyncClient(timeout=20.0) as client:
            while True:
                if offset: params["offset"] = offset
                r = await client.get(url, headers=self._headers, params=params)
                r.raise_for_status()
                d = r.json()
                records += d.get("records", [])
                offset = d.get("offset")
                if not offset:
                    break
        return records

    async def _at_create(self, table_id: str, fields: dict) -> dict:
        url = f"{AT_API}/{INV_BASE}/{table_id}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(url, headers=self._headers, json={"fields": fields})
            r.raise_for_status()
            return r.json()

    async def _at_update(self, table_id: str, record_id: str, fields: dict) -> dict:
        url = f"{AT_API}/{INV_BASE}/{table_id}/{record_id}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.patch(url, headers=self._headers, json={"fields": fields})
            r.raise_for_status()
            return r.json()

    # ── Telegram ───────────────────────────────────────────────────────────────
    async def _send(self, chat_id: str, text: str, reply_to: Optional[int] = None) -> None:
        payload: dict = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(f"{TG_BASE}/sendMessage", json=payload)
            r.raise_for_status()

    # ── Parsing helpers ────────────────────────────────────────────────────────
    def _extract_bb_gg(self, text: str) -> tuple[int, int]:
        lower = text.lower()
        bb = gg = 0
        for pattern in [
            r"(\d+)\s+(?:bb|bawk(?:\s+bawk)?|chicken(?:\s+box(?:es)?)?)",
            r"(?:bb|bawk(?:\s+bawk)?|chicken(?:\s+box(?:es)?)?)[\s:]+(\d+)",
        ]:
            m = re.search(pattern, lower)
            if m: bb = int(m.group(1)); break
        for pattern in [
            r"(\d+)\s+(?:gg|gulu(?:\s+gulu)?|salmon(?:\s+box(?:es)?)?)",
            r"(?:gg|gulu(?:\s+gulu)?|salmon(?:\s+box(?:es)?)?)[\s:]+(\d+)",
        ]:
            m = re.search(pattern, lower)
            if m: gg = int(m.group(1)); break
        return bb, gg

    def _extract_date(self, text: str) -> Optional[date]:
        t     = text.lower()
        today = date.today()
        if "today" in t:     return today
        if "yesterday" in t: return today - timedelta(days=1)
        if "tomorrow" in t:  return today + timedelta(days=1)
        for month_name, month_num in MONTH_MAP.items():
            m = re.search(rf"\b(\d{{1,2}})\s+{month_name}(?:\s+(\d{{4}}))?\b", t)
            if m:
                try: return date(int(m.group(2) or today.year), month_num, int(m.group(1)))
                except ValueError: pass
            m = re.search(rf"\b{month_name}\s+(\d{{1,2}})(?:\s+(\d{{4}}))?\b", t)
            if m:
                try: return date(int(m.group(2) or today.year), month_num, int(m.group(1)))
                except ValueError: pass
        m = re.search(r"\b(\d{1,2})[\/\-](\d{1,2})(?:[\/\-](\d{4}))?\b", text)
        if m:
            try: return date(int(m.group(3) or today.year), int(m.group(2)), int(m.group(1)))
            except ValueError: pass
        return None


# ── Ingredient calculator ──────────────────────────────────────────────────────
def _calc_ingredients(bb: int, gg: int) -> dict:
    return {
        "chicken_breast_kg": math.ceil((bb * 147.72 + gg * 130.25) / 1000),
        "chicken_heart_kg":  math.ceil((bb * 29.16  + gg * 24.24)  / 1000),
        "chicken_liver_kg":  math.ceil((bb * 19.44  + gg * 18.19)  / 1000),
        "salmon_kg":         round(gg * 27.38 / 1000, 1),
        "egg_yolk_kg":       round((bb * 17.04 + gg * 15.77) / 1000, 1),
        "pumpkin_kg":        round((bb * 12.60 + gg * 10.20) / 1000, 1),
        "carrot_kg":         round((bb + gg) * 5.40 / 1000, 1),
        "salmon_oil_g":      round((bb + gg) * 2.40, 1),
        "multivitamin_g":    round(bb * 2.88 + gg * 2.81, 1),
        "eggshell_g":        round((bb + gg) * 2.88, 1),
        "taurine_g":         round((bb + gg) * 0.48, 1),
        "sleeve_labels":     (bb + gg) * 6,
        "packaging_boxes":   bb + gg,
    }
