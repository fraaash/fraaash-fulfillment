"""
handlers/inventory.py

Handles inventory management queries from the Fraaash Operation Telegram group.

Trigger phrases Pawbot understands (case-insensitive):

  Stock query:         "how many bb left", "how many bawk bawk boxes left"
                       "how many packaging boxes left", "how many bb sleeve left"
                       "how many thank you card left", "check stock"

  Packaging check:     "check packaging stock", "packaging alert"

  Delivery out:        "19 BB and 18 GG delivered today", "sent 10 chicken 5 salmon"

  Production plan:     "plan 50 BB 50 GG on 9 July", "produce 70 bawk bawk 50 gulu gulu tomorrow"
    (logs a batch)     "I want to produce 70 BB 50 GG tomorrow"

  Production actual:   "produced 48 BB 47 GG today", "completed production 70 BB 50 GG"

  Fulfillment query:   "how many boxes to fulfill", "pending orders", "orders to fulfill"

  Production suggest:  "how much should I produce", "what should I produce for next batch"
    (no logging)       "suggest production"
"""

import logging
import math
import re
from datetime import date, datetime, timedelta
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

SALES_BASE          = "appqaeML2BR2aklix"
SALES_PO_TABLE      = "tblMK2nWUx0XQIVjK"

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
PLAN_WORDS      = {"plan", "produce", "production", "batch", "planning", "prepare",
                   "preparing", "log", "schedule", "create"}
PRODUCED_WORDS  = {"produced", "completed", "done", "finished", "actual"}
PKG_CHECK_WORDS = {"check", "alert", "low", "reorder"}
FULFILL_WORDS   = {"fulfill", "fulfil", "fulfillment", "fulfilment", "outstanding"}
SUGGEST_WORDS   = {"suggest", "recommend", "suggestion", "recommendation", "should",
                   "advice", "much", "many"}

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
        if self._is_production_actual(lower, words):         return "production_actual"
        if self._is_fulfillment_query(lower, words):         return "fulfillment_query"
        if self._is_production_suggest(lower, words):        return "production_suggest"
        if self._is_production_plan_suggest(lower, words):   return "production_plan_suggest"
        if self._is_inventory_out(lower, words):             return "inventory_out"
        if self._is_production_plan(lower, words):           return "production_plan"
        if self._is_packaging_check(lower, words):           return "packaging_check"
        if self._is_stock_query(lower, words):               return "stock_query"
        return None

    def _is_stock_query(self, lower: str, words: set) -> bool:
        has_stock   = bool(words & STOCK_WORDS)
        has_product = bool(words & PRODUCT_WORDS)
        has_pkg     = bool(words & PACKAGING_WORDS)
        has_how     = "how" in words
        return has_stock or (has_how and (has_product or has_pkg))

    def _is_inventory_out(self, lower: str, words: set) -> bool:
        OUT_WORDS = {"delivered", "deliver", "sent", "shipped", "dispatched", "courier"}
        # "log X out today" is a natural way to log items going out
        has_out     = bool(words & OUT_WORDS) or ("out" in words and "log" in words)
        has_product = bool(words & PRODUCT_WORDS)
        has_qty     = bool(re.search(r"\b\d+\b", lower))
        has_order   = bool(re.search(r"\b\d{4,6}\b", lower))
        return has_out and has_product and has_qty and not has_order

    def _is_production_plan(self, lower: str, words: set) -> bool:
        has_plan    = bool(words & PLAN_WORDS)
        has_product = bool(words & PRODUCT_WORDS)
        has_qty     = bool(re.search(r"\b\d+\b", lower))
        return has_plan and has_product and has_qty

    def _is_production_actual(self, lower: str, words: set) -> bool:
        return (bool(words & PRODUCED_WORDS)
                and bool(re.search(r"\b\d+\b", lower))
                and bool(words & PRODUCT_WORDS))

    def _is_packaging_check(self, lower: str, words: set) -> bool:
        return bool(words & PACKAGING_WORDS) and bool(words & PKG_CHECK_WORDS)

    def _is_fulfillment_query(self, lower: str, words: set) -> bool:
        has_fulfill = bool(words & FULFILL_WORDS)
        has_pending = ("pending" in lower and
                       bool(words & {"order", "orders", "box", "boxes", "stock"}))
        return has_fulfill or has_pending

    def _is_production_suggest(self, lower: str, words: set) -> bool:
        has_produce  = bool(words & {"produce", "production", "batch"})
        has_question = bool(words & SUGGEST_WORDS) or "how" in words or "what" in words
        has_qty      = bool(re.search(r"\b\d+\b", lower))
        return has_produce and has_question and not has_qty

    def _is_production_plan_suggest(self, lower: str, words: set) -> bool:
        """Has specific quantities but also a question — show ingredients without logging."""
        has_produce  = bool(words & PLAN_WORDS)
        has_product  = bool(words & PRODUCT_WORDS)
        has_qty      = bool(re.search(r"\b\d+\b", lower))
        has_question = bool(words & SUGGEST_WORDS) or "how" in words or "what" in words or "if" in words
        return has_produce and has_product and has_qty and has_question

    # ── Main dispatcher ────────────────────────────────────────────────────────
    async def handle(self, chat_id: str, msg_id: int, text: str) -> None:
        lower  = text.lower()
        words  = set(re.findall(r"[a-zA-Z]+", lower))
        intent = self._detect_intent(lower, words)
        try:
            if intent == "stock_query":                 await self._stock_query(chat_id, msg_id, text)
            elif intent == "inventory_out":             await self._inventory_out(chat_id, msg_id, text)
            elif intent == "production_plan":           await self._production_plan(chat_id, msg_id, text)
            elif intent == "production_plan_suggest":   await self._production_plan_suggest(chat_id, msg_id, text)
            elif intent == "production_actual":         await self._production_actual(chat_id, msg_id, text)
            elif intent == "packaging_check":           await self._packaging_check(chat_id, msg_id)
            elif intent == "fulfillment_query":         await self._fulfillment_query(chat_id, msg_id)
            elif intent == "production_suggest":        await self._production_suggest(chat_id, msg_id)
            else:
                await self._send(chat_id, (
                    "❓ I couldn't understand that. Try:\n"
                    "• *how many bb left* — product stock\n"
                    "• *how many bb sleeve left* — packaging stock\n"
                    "• *how many boxes to fulfill* — pending orders\n"
                    "• *how much should I produce* — production suggestion\n"
                    "• *plan 70 BB 50 GG tomorrow* — log production batch\n"
                    "• *produced 70 BB 50 GG today* — mark batch complete\n"
                    "• *19 BB 18 GG delivered today* — log delivery out\n"
                    "• *check packaging stock* — packaging alert"
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

        lines = ["📦 *Fraaash Inventory*\n"]

        if ask_product:
            bb, gg = await self._get_product_stock()
            # Filter to specific product if mentioned
            show_bb = not bool(words & {"gulu", "gg", "salmon"}) or bool(words & {"bawk", "bb", "chicken"})
            show_gg = not bool(words & {"bawk", "bb", "chicken"}) or bool(words & {"gulu", "gg", "salmon"})
            if show_bb or show_gg:
                lines.append("*🐔 Product Stock:*")
                if show_bb: lines.append(f"• Bawk Bawk (BB): *{bb} boxes*")
                if show_gg: lines.append(f"• Gulu Gulu (GG): *{gg} boxes*")
                lines.append("")

        if ask_pkg:
            pkg = await self._get_packaging_stock()
            # Filter to specific packaging item if mentioned
            filtered = _filter_packaging(pkg, lower, words)
            lines.append("*📦 Packaging Stock:*")
            for name, qty, reorder in filtered:
                warn = " ⚠️ LOW" if reorder is not None and qty <= reorder else ""
                lines.append(f"• {name}: *{qty}*{warn}")

        await self._send(chat_id, "\n".join(lines), msg_id)

    # ── 2. Inventory out (delivery) ────────────────────────────────────────────
    async def _inventory_out(self, chat_id: str, msg_id: int, text: str) -> None:
        bb, gg = self._extract_bb_gg(text)
        if bb == 0 and gg == 0:
            await self._send(chat_id,
                "❓ Couldn't find quantities. Try: *19 BB and 18 GG delivered today*", msg_id)
            return

        target_date = self._extract_date(text) or date.today()
        date_iso    = target_date.isoformat()
        date_label  = target_date.strftime("%-d %B %Y")

        # Boxes negative (Out), packs positive (magnitude only)
        fields: dict = {
            "fldnkV4GeBZmNe8Fy": date_iso,
            "fldESxOVa6nglAy0J": "Out",
            "fldkGMAzNKJzDBYCS": f"Courier delivery – {date_label}",
        }
        if bb:
            fields["fld2O5oOrRAaABCr9"] = -bb
            fields["fldUYRurduQ37qopd"] = bb * 6
        if gg:
            fields["fld2uxP8aLheTQwQN"] = -gg
            fields["fldROm2Yl2Le2W7Vn"] = gg * 6

        await self._at_create(INV_MOVEMENT_TABLE, fields)
        new_bb, new_gg = await self._get_product_stock()

        lines = [f"✅ *Inventory Out — {date_label}*", ""]
        if bb: lines.append(f"🐔 BB out: *{bb} boxes*")
        if gg: lines.append(f"🐟 GG out: *{gg} boxes*")
        lines += ["", "*Updated stock:*",
                  f"• BB: *{new_bb} boxes*", f"• GG: *{new_gg} boxes*"]
        await self._send(chat_id, "\n".join(lines), msg_id)

    # ── 3. Plan production batch ───────────────────────────────────────────────
    async def _production_plan(self, chat_id: str, msg_id: int, text: str) -> None:
        bb, gg = self._extract_bb_gg(text)
        if bb == 0 and gg == 0:
            await self._send(chat_id,
                "❓ Couldn't find quantities. Try: *plan 50 BB 50 GG on 9 July*", msg_id)
            return

        target_date = self._extract_date(text) or date.today()
        date_iso    = target_date.isoformat()
        date_label  = target_date.strftime("%-d %B %Y")
        batch_id    = f"BATCH-{target_date.strftime('%y%m%d')}"
        today_iso   = date.today().isoformat()

        # Create production batch record
        batch = await self._at_create(PRODUCTION_TABLE, {
            "fldyCdkRmFuFhUXX0": batch_id,
            "fldxjxVuLMBDNlMPP": date_iso,
            "fldKTZYSlr63HIOHf": "Planned",
            "fldx2AMZUKF7DNFCN": bb,
            "fldXt1jLSVSblAxTW": gg,
            "fldsprqFiFKq1ARFG": 14.20,
            "fld4LFzhfjQrkXxL7": 49.00,
            "fldusQdNlqaLWUvwU": 8.60,
            "fldVN6WtGJKWWZ1oW": 3.00,
            "fldcYI9ufszsQRFlI": 21.57,
            "fld9S9CN5oETzVPDk": 3.01,
            "fld3rstL473nf4Zzo": 5.00,
            "fldKXL3WOolEdE8eE": 1.30,
            "fldUBOJBF63nKxRVm": 2.57,
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
        ]
        await self._send(chat_id, "\n".join(lines), msg_id)

    # ── 4. Update actual production ────────────────────────────────────────────
    async def _production_actual(self, chat_id: str, msg_id: int, text: str) -> None:
        bb, gg = self._extract_bb_gg(text)
        if bb == 0 and gg == 0:
            await self._send(chat_id,
                "❓ Couldn't find quantities. Try: *produced 48 BB 47 GG today*", msg_id)
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

        await self._at_update(PRODUCTION_TABLE, batch_rec_id, {
            "fldexuMJ3JM5RAFTw": bb,
            "fldM1yMQ0h1Okxpa2": gg,
            "fldKTZYSlr63HIOHf": "Completed",
        })

        # Inventory IN — boxes positive, packs positive
        inv_fields: dict = {
            "fldnkV4GeBZmNe8Fy": date_iso,
            "fldESxOVa6nglAy0J": "In",
            "fldkGMAzNKJzDBYCS": f"Production {batch_id}",
            "fld0rN66vMWP6D33r": [batch_rec_id],
        }
        if bb:
            inv_fields["fld2O5oOrRAaABCr9"] = bb
            inv_fields["fldUYRurduQ37qopd"] = bb * 6
        if gg:
            inv_fields["fld2uxP8aLheTQwQN"] = gg
            inv_fields["fldROm2Yl2Le2W7Vn"] = gg * 6
        await self._at_create(INV_MOVEMENT_TABLE, inv_fields)

        new_bb, new_gg = await self._get_product_stock()
        lines = [f"✅ *{batch_id} completed — {date_label}*", ""]
        if bb: lines.append(f"🐔 BB produced: *{bb} boxes*")
        if gg: lines.append(f"🐟 GG produced: *{gg} boxes*")
        lines += ["", "*Updated stock:*",
                  f"• BB: *{new_bb} boxes*", f"• GG: *{new_gg} boxes*"]
        await self._send(chat_id, "\n".join(lines), msg_id)

    # ── 3b. Production plan suggestion (with quantities, no logging) ───────────
    async def _production_plan_suggest(self, chat_id: str, msg_id: int, text: str) -> None:
        bb, gg = self._extract_bb_gg(text)
        if bb == 0 and gg == 0:
            await self._send(chat_id,
                "❓ Couldn't find quantities. Try: *if I produce 70 BB and 50 GG, what do I need?*", msg_id)
            return

        pending = await self._get_pending_orders()
        stock_bb, stock_gg = await self._get_product_stock()
        ing = _calc_ingredients(bb, gg)
        eggs = math.ceil(ing["egg_yolk_kg"] * 1000 / 13)

        net_bb = pending["bb"] - stock_bb - bb
        net_gg = pending["gg"] - stock_gg - gg

        lines = [
            f"💡 *Production Preview — {bb} BB + {gg} GG* _(not logged)_",
            "",
            "*After production vs pending orders:*",
            f"  • BB: {stock_bb} stock + {bb} produced − {pending['bb']} orders = *{-net_bb:+d} boxes*" if net_bb <= 0
              else f"  • BB: still short *{net_bb} boxes* after production",
            f"  • GG: {stock_gg} stock + {gg} produced − {pending['gg']} orders = *{-net_gg:+d} boxes*" if net_gg <= 0
              else f"  • GG: still short *{net_gg} boxes* after production",
            "",
            "📋 *Ingredients needed:*",
            "",
            "🏭 *Han Kee Processing:*",
            f"  • Chicken Breast: *{ing['chicken_breast_kg']} kg*",
            f"  • Chicken Heart: *{ing['chicken_heart_kg']} kg*",
            f"  • Chicken Liver: *{ing['chicken_liver_kg']} kg*",
            "",
            "🛒 *Other:*",
            f"  • Salmon: *{ing['salmon_kg']} kg*",
            f"  • Egg Yolk: *{ing['egg_yolk_kg']} kg* (~{eggs} eggs)",
            f"  • Pumpkin: *{ing['pumpkin_kg']} kg*",
            f"  • Carrot: *{ing['carrot_kg']} kg*",
            "",
            "📦 *Packaging:*",
            f"  • Sleeve Labels: *{ing['sleeve_labels']} pcs*",
            f"  • Packaging Boxes: *{ing['packaging_boxes']} pcs*",
            "",
            f"_(Say *plan {bb} BB {gg} GG tomorrow* to log the batch)_",
        ]
        await self._send(chat_id, "\n".join(lines), msg_id)

    # ── 5. Packaging check ─────────────────────────────────────────────────────
    async def _packaging_check(self, chat_id: str, msg_id: Optional[int] = None) -> None:
        pkg = await self._get_packaging_stock()
        low = [(n, q, r) for n, q, r in pkg if r is not None and q <= r]

        if not low:
            lines = ["✅ *Packaging Stock — All Good*", ""]
            for name, qty, reorder in pkg:
                lines.append(f"• {name}: {qty}"
                              + (f"  _(reorder ≤ {reorder})_" if reorder else ""))
        else:
            lines = ["⚠️ *Packaging Alert — Low Stock!*", "", "*Need to reorder:*"]
            for name, qty, reorder in low:
                lines.append(f"• 🔴 {name}: *{qty}* (reorder at {reorder})")
            lines += ["", "*Full stock:*"]
            for name, qty, reorder in pkg:
                icon = "🔴" if reorder is not None and qty <= reorder else "🟢"
                lines.append(f"• {icon} {name}: {qty}")

        await self._send(chat_id, "\n".join(lines), msg_id)

    async def check_packaging_alert(self) -> None:
        """Called automatically from fulfillment.py after packaging movements."""
        pkg = await self._get_packaging_stock()
        low = [(n, q, r) for n, q, r in pkg if r is not None and q <= r]
        if low:
            lines = ["⚠️ *Packaging Low Stock Alert*", ""]
            for name, qty, reorder in low:
                lines.append(f"• 🔴 {name}: *{qty} remaining* (reorder at {reorder})")
            await self._send(OPS_CHAT_ID, "\n".join(lines))

    # ── 6. Fulfillment query ───────────────────────────────────────────────────
    async def _fulfillment_query(self, chat_id: str, msg_id: int) -> None:
        records = await self._at_list_sales(
            SALES_PO_TABLE,
            fields=["fldmU2FR9iN5QzBDP", "fldplB5HEpbt6rrBU",
                    "fldg2x7JWkHLcVLRC", "fld6WCGUNpodRhoYM"],
            formula="{Process Status}='Pending'",
        )

        total_bb = total_gg = 0
        order_lines: list[str] = []
        for r in records:
            f        = r.get("fields", {})
            order_id = f.get("fldmU2FR9iN5QzBDP", "?")
            bb       = int(f.get("fldplB5HEpbt6rrBU") or 0)
            gg       = int(f.get("fldg2x7JWkHLcVLRC") or 0)
            delivery = f.get("fld6WCGUNpodRhoYM", "")
            total_bb += bb
            total_gg += gg
            if bb or gg:
                date_str = ""
                if delivery:
                    try:
                        d = datetime.strptime(delivery, "%Y-%m-%d")
                        date_str = f" ({d.strftime('%-d %b')})"
                    except Exception:
                        date_str = f" ({delivery})"
                order_lines.append(
                    f"  • {order_id}{date_str}: "
                    + (f"{bb}BB " if bb else "") + (f"{gg}GG" if gg else "")
                )

        lines = [
            "📋 *Pending Orders to Fulfill*",
            f"Total: *{total_bb} BB*  |  *{total_gg} GG*  ({len(order_lines)} orders)",
            "",
        ]
        if order_lines:
            lines.append("*Orders:*")
            lines.extend(order_lines[:25])
            if len(order_lines) > 25:
                lines.append(f"  _...and {len(order_lines) - 25} more_")
        else:
            lines.append("🎉 No pending orders!")

        await self._send(chat_id, "\n".join(lines), msg_id)

    # ── 7. Production suggestion (no logging) ──────────────────────────────────
    async def _production_suggest(self, chat_id: str, msg_id: int) -> None:
        pending = await self._get_pending_orders()
        stock_bb, stock_gg = await self._get_product_stock()

        suggest_bb = max(0, pending["bb"] - stock_bb)
        suggest_gg = max(0, pending["gg"] - stock_gg)

        lines = [
            "💡 *Production Suggestion*",
            "",
            "*Pending orders:*",
            f"  • BB: {pending['bb']} boxes",
            f"  • GG: {pending['gg']} boxes",
            "",
            "*Current stock:*",
            f"  • BB: {stock_bb} boxes",
            f"  • GG: {stock_gg} boxes",
            "",
            "*Suggested to produce:*",
            f"  🐔 BB: *{suggest_bb} boxes*",
            f"  🐟 GG: *{suggest_gg} boxes*",
        ]

        if suggest_bb == 0 and suggest_gg == 0:
            lines.append(
                "\n✅ Current stock covers all pending orders — no production needed!"
            )
        else:
            lines.append(
                "\n_(Say *plan {bb} BB {gg} GG tomorrow* to log the batch)_"
                .format(bb=suggest_bb, gg=suggest_gg)
            )

        await self._send(chat_id, "\n".join(lines), msg_id)

    # ── Airtable data helpers ──────────────────────────────────────────────────
    async def _get_product_stock(self) -> tuple[int, int]:
        records = await self._at_list(
            INV_MOVEMENT_TABLE,
            fields=["fld2O5oOrRAaABCr9", "fld2uxP8aLheTQwQN"],
        )
        bb = gg = 0
        for r in records:
            f   = r.get("fields", {})
            bb += int(f.get("fld2O5oOrRAaABCr9") or 0)  # Out quantities stored as negative
            gg += int(f.get("fld2uxP8aLheTQwQN") or 0)
        return bb, gg

    async def _get_packaging_stock(self) -> list[tuple[str, int, Optional[int]]]:
        mats = await self._at_list(PACKAGING_MAT_TABLE,
                                   fields=["fld6a4n4QQWt8Y9yi", "fldZdBtvuOvGEWvLk"])
        movs = await self._at_list(PACKAGING_MOV_TABLE,
                                   fields=["fldiUMD8gv9zPvmkT", "fldmzsyBN25Swiuhb"])

        balances: dict[str, int] = {}
        for mov in movs:
            f   = mov.get("fields", {})
            ids = f.get("fldiUMD8gv9zPvmkT", [])
            qty = int(f.get("fldmzsyBN25Swiuhb") or 0)  # Out quantities stored as negative
            for mid in ids:
                balances.setdefault(mid, 0)
                balances[mid] += qty

        return [
            (
                m.get("fields", {}).get("fld6a4n4QQWt8Y9yi", "Unknown"),
                balances.get(m["id"], 0),
                m.get("fields", {}).get("fldZdBtvuOvGEWvLk"),
            )
            for m in mats
        ]

    async def _get_pending_orders(self) -> dict:
        records = await self._at_list_sales(
            SALES_PO_TABLE,
            fields=["fldplB5HEpbt6rrBU", "fldg2x7JWkHLcVLRC"],
            formula="{Process Status}='Pending'",
        )
        bb = sum(int(r.get("fields", {}).get("fldplB5HEpbt6rrBU") or 0) for r in records)
        gg = sum(int(r.get("fields", {}).get("fldg2x7JWkHLcVLRC") or 0) for r in records)
        return {"bb": bb, "gg": gg}

    async def _find_planned_batch(self, date_iso: str) -> Optional[dict]:
        records = await self._at_list(
            PRODUCTION_TABLE,
            formula=f"AND({{Batch Date}}='{date_iso}',{{Status}}='Planned')",
        )
        return records[0] if records else None

    # ── Airtable HTTP ──────────────────────────────────────────────────────────
    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {settings.AIRTABLE_TOKEN}",
            "Content-Type": "application/json",
        }

    async def _at_list(self, table_id: str,
                       fields: list[str] = None,
                       formula: str = None) -> list[dict]:
        return await self._at_list_base(INV_BASE, table_id, fields, formula)

    async def _at_list_sales(self, table_id: str,
                              fields: list[str] = None,
                              formula: str = None) -> list[dict]:
        return await self._at_list_base(SALES_BASE, table_id, fields, formula)

    async def _at_list_base(self, base_id: str, table_id: str,
                             fields: list[str] = None,
                             formula: str = None) -> list[dict]:
        url    = f"{AT_API}/{base_id}/{table_id}"
        params: dict = {"returnFieldsByFieldId": "true"}
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
    async def _send(self, chat_id: str, text: str,
                    reply_to: Optional[int] = None) -> None:
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
            r"(\d+)\s+(?:bb|bawk(?:\s+bawk)?|chicken(?:\s+box(?:es)?)?)\b",
            r"(\d+)\s+boxes?\s+of\s+(?:bb|bawk(?:\s+bawk)?|chicken)\b",
            r"(?:bb|bawk(?:\s+bawk)?|chicken(?:\s+box(?:es)?)?)[\s:]+(\d+)",
        ]:
            m = re.search(pattern, lower)
            if m: bb = int(m.group(1)); break
        for pattern in [
            r"(\d+)\s+(?:gg|gulu(?:\s+gulu)?|salmon(?:\s+box(?:es)?)?)\b",
            r"(\d+)\s+boxes?\s+of\s+(?:gg|gulu(?:\s+gulu)?|salmon)\b",
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


# ── Packaging filter helper ────────────────────────────────────────────────────
def _filter_packaging(
    pkg: list[tuple[str, int, Optional[int]]],
    lower: str,
    words: set,
) -> list[tuple[str, int, Optional[int]]]:
    """Filter packaging list to specific item(s) based on query keywords."""
    ask_bb_sleeve  = bool(words & {"bawk", "bb", "chicken"}) and bool(words & {"sleeve", "label"})
    ask_gg_sleeve  = bool(words & {"gulu", "gg", "salmon"})  and bool(words & {"sleeve", "label"})
    ask_any_sleeve = bool(words & {"sleeve", "label"}) and not ask_bb_sleeve and not ask_gg_sleeve
    ask_box        = bool(words & {"foam", "packaging"}) and "box" in lower
    ask_card       = "card" in lower or "thank" in lower

    if not any([ask_bb_sleeve, ask_gg_sleeve, ask_any_sleeve, ask_box, ask_card]):
        return pkg  # no specific filter — return all

    result = []
    for name, qty, reorder in pkg:
        n = name.lower()
        if ask_bb_sleeve  and ("bawk" in n or "bb" in n or "chicken" in n): result.append((name, qty, reorder))
        elif ask_gg_sleeve and ("gulu" in n or "gg" in n or "salmon" in n): result.append((name, qty, reorder))
        elif ask_any_sleeve and ("sleeve" in n or "label" in n):            result.append((name, qty, reorder))
        elif ask_box       and "box" in n:                                  result.append((name, qty, reorder))
        elif ask_card      and "card" in n:                                 result.append((name, qty, reorder))
    return result or pkg  # fallback to all if nothing matched


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
