"""
Seed script: wipes current data and inserts 2 realistic SKUs
with 14 days of complete history (2 full weeks ending yesterday).

SKU-1  BIRRA_LAGER     – birra lager 33cl, vendite stabili ~20/giorno
SKU-2  ACQUA_FRIZZANTE – acqua frizzante 1.5L, vendite variabili ~30/giorno

History includes:
  - SNAPSHOT day 0 (initial stock)
  - Daily SALE records for all 14 days
  - 1 ORDER + 1 RECEIPT mid-week (per SKU)
  - 1 ADJUST (inventory correction, day 10)
  - sales table (daily aggregates mirror)
  - order_logs + receiving_logs entries
"""

import sqlite3
from datetime import date, timedelta
from pathlib import Path
import random

DB_PATH = Path("data/app.db")
TODAY = date(2026, 3, 31)
START = TODAY - timedelta(days=14)   # 14 days of history; day 0 = START
DAYS = 14                             # days 1..14 (START+1 .. TODAY-1 + TODAY)

random.seed(42)  # reproducible

# ---------------------------------------------------------------------------
# SKU definitions
# ---------------------------------------------------------------------------
SKUS = [
    {
        "sku": "BIRRA_LAGER",
        "description": "Birra Lager 33cl Lattina",
        "ean": "8002270013061",
        "moq": 24,
        "pack_size": 24,
        "lead_time_days": 3,
        "review_period": 7,
        "safety_stock": 48,
        "shelf_life_days": 180,
        "min_shelf_life_days": 60,
        "waste_penalty_mode": "soft",
        "waste_penalty_factor": 0.5,
        "waste_risk_threshold": 0.10,
        "max_stock": 600,
        "reorder_point": 80,
        "demand_variability": "STABLE",
        "category": "BEER",
        "department": "BEVERAGES",
        "oos_boost_percent": 10.0,
        "oos_detection_mode": "strict",
        "oos_popup_preference": "ask",
        "forecast_method": "simple",
        "in_assortment": 1,
        "target_csl": 0.95,
    },
    {
        "sku": "ACQUA_FRIZZANTE",
        "description": "Acqua Frizzante 1.5L",
        "ean": "8001235002018",
        "moq": 6,
        "pack_size": 6,
        "lead_time_days": 2,
        "review_period": 7,
        "safety_stock": 30,
        "shelf_life_days": 365,
        "min_shelf_life_days": 90,
        "waste_penalty_mode": "soft",
        "waste_penalty_factor": 0.3,
        "waste_risk_threshold": 0.05,
        "max_stock": 500,
        "reorder_point": 60,
        "demand_variability": "MEDIUM",
        "category": "WATER",
        "department": "BEVERAGES",
        "oos_boost_percent": 5.0,
        "oos_detection_mode": "relaxed",
        "oos_popup_preference": "ask",
        "forecast_method": "simple",
        "in_assortment": 1,
        "target_csl": 0.92,
    },
]

# ---------------------------------------------------------------------------
# Demand profiles  (base qty/day + weekend multiplier + noise)
# ---------------------------------------------------------------------------
def daily_sales(sku_id: str, d: date) -> int:
    """Return realistic daily sales qty."""
    is_weekend = d.weekday() >= 5
    if sku_id == "BIRRA_LAGER":
        base = 28 if is_weekend else 18
        noise = random.randint(-4, 4)
    else:  # ACQUA_FRIZZANTE
        base = 38 if is_weekend else 26
        noise = random.randint(-5, 6)
    return max(0, base + noise)


def run():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA foreign_keys = OFF")   # allow clean wipe
    cur = conn.cursor()

    # -----------------------------------------------------------------------
    # 1. Wipe data tables (preserve schema_version, settings, holidays)
    # -----------------------------------------------------------------------
    tables_to_clear = [
        "transactions", "sales", "order_logs", "order_receipts",
        "receiving_logs", "lots", "kpi_daily", "audit_log",
        "skus", "sqlite_sequence",
    ]
    for t in tables_to_clear:
        cur.execute(f"DELETE FROM {t}")
    print("✓ Tabelle svuotate")

    conn.execute("PRAGMA foreign_keys = ON")

    # -----------------------------------------------------------------------
    # 2. Insert SKUs
    # -----------------------------------------------------------------------
    for s in SKUS:
        cur.execute("""
            INSERT INTO skus (
                sku, description, ean, moq, pack_size, lead_time_days, review_period,
                safety_stock, shelf_life_days, min_shelf_life_days,
                waste_penalty_mode, waste_penalty_factor, waste_risk_threshold,
                max_stock, reorder_point, demand_variability, category, department,
                oos_boost_percent, oos_detection_mode, oos_popup_preference,
                forecast_method, in_assortment, target_csl,
                mc_distribution, mc_n_simulations, mc_random_seed,
                mc_output_stat, mc_output_percentile, mc_horizon_mode, mc_horizon_days
            ) VALUES (
                :sku, :description, :ean, :moq, :pack_size, :lead_time_days, :review_period,
                :safety_stock, :shelf_life_days, :min_shelf_life_days,
                :waste_penalty_mode, :waste_penalty_factor, :waste_risk_threshold,
                :max_stock, :reorder_point, :demand_variability, :category, :department,
                :oos_boost_percent, :oos_detection_mode, :oos_popup_preference,
                :forecast_method, :in_assortment, :target_csl,
                'normal', 1000, 0, 'mean', 50, 'auto', 30
            )
        """, s)
    print("✓ 2 SKU inseriti")

    # -----------------------------------------------------------------------
    # 3. Build transaction ledger + sales (14 days)
    # -----------------------------------------------------------------------
    SKU_IDS = ["BIRRA_LAGER", "ACQUA_FRIZZANTE"]

    # Initial stock levels (day 0 = START)
    initial_stock = {"BIRRA_LAGER": 240, "ACQUA_FRIZZANTE": 180}
    on_hand       = dict(initial_stock)

    # ORDER placed on day 5, RECEIPT on day 8 (lead_time=3) / day 7 (lead_time=2)
    order_qty   = {"BIRRA_LAGER": 192, "ACQUA_FRIZZANTE": 120}  # 8×24 and 20×6
    order_day   = {"BIRRA_LAGER": 5,   "ACQUA_FRIZZANTE": 5}
    receipt_day = {"BIRRA_LAGER": 8,   "ACQUA_FRIZZANTE": 7}
    adjust_day  = {"BIRRA_LAGER": 10,  "ACQUA_FRIZZANTE": 10}
    adjust_delta= {"BIRRA_LAGER": -4,  "ACQUA_FRIZZANTE": +3}

    order_ids = {}

    # SNAPSHOT (day 0 = START)
    for sku_id in SKU_IDS:
        cur.execute("""
            INSERT INTO transactions (date, sku, event, qty, receipt_date, note)
            VALUES (?, ?, 'SNAPSHOT', ?, '', 'Conteggio iniziale')
        """, (START.isoformat(), sku_id, initial_stock[sku_id]))

    # Days 1..14
    for offset in range(1, DAYS + 1):
        d = START + timedelta(days=offset)
        d_str = d.isoformat()

        for sku_id in SKU_IDS:
            # ORDER event
            if offset == order_day[sku_id]:
                qty = order_qty[sku_id]
                r_date = (START + timedelta(days=receipt_day[sku_id])).isoformat()
                order_id = f"ORD-{d_str}-{sku_id}"
                order_ids[sku_id] = order_id
                cur.execute("""
                    INSERT INTO transactions (date, sku, event, qty, receipt_date, note)
                    VALUES (?, ?, 'ORDER', ?, ?, ?)
                """, (d_str, sku_id, qty, r_date, f"Ordine settimanale {order_id}"))
                cur.execute("""
                    INSERT INTO order_logs (order_id, date, sku, qty_ordered, qty_received, status, receipt_date)
                    VALUES (?, ?, ?, ?, ?, 'RECEIVED', ?)
                """, (order_id, d_str, sku_id, qty, qty, r_date))

            # RECEIPT event
            if offset == receipt_day[sku_id]:
                qty = order_qty[sku_id]
                order_id = order_ids.get(sku_id, f"ORD-UNKNOWN-{sku_id}")
                receipt_id = f"REC-{d_str}-{sku_id}"
                cur.execute("""
                    INSERT INTO transactions (date, sku, event, qty, receipt_date, note)
                    VALUES (?, ?, 'RECEIPT', ?, ?, ?)
                """, (d_str, sku_id, qty, d_str, f"Ricevimento {receipt_id}"))
                on_hand[sku_id] = on_hand.get(sku_id, 0) + qty
                cur.execute("""
                    INSERT INTO receiving_logs (document_id, receipt_id, date, sku, qty_received, receipt_date, order_ids)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (receipt_id, receipt_id, d_str, sku_id, qty, d_str, order_id))

            # ADJUST event (inventory count correction)
            if offset == adjust_day[sku_id]:
                delta = adjust_delta[sku_id]
                corrected = max(0, on_hand.get(sku_id, 0) + delta)
                cur.execute("""
                    INSERT INTO transactions (date, sku, event, qty, receipt_date, note)
                    VALUES (?, ?, 'ADJUST', ?, '', 'Conteggio inventario settimanale')
                """, (d_str, sku_id, corrected))
                on_hand[sku_id] = corrected

            # SALE event + sales table
            sale_qty = daily_sales(sku_id, d)
            if sale_qty > 0:
                cur.execute("""
                    INSERT INTO transactions (date, sku, event, qty, receipt_date, note)
                    VALUES (?, ?, 'SALE', ?, '', '')
                """, (d_str, sku_id, -sale_qty))
                cur.execute("""
                    INSERT INTO sales (date, sku, qty_sold, promo_flag)
                    VALUES (?, ?, ?, 0)
                """, (d_str, sku_id, sale_qty))
                on_hand[sku_id] = max(0, on_hand.get(sku_id, 0) - sale_qty)

    conn.commit()
    conn.close()

    # -----------------------------------------------------------------------
    # 4. Summary
    # -----------------------------------------------------------------------
    conn2 = sqlite3.connect(str(DB_PATH))
    cur2 = conn2.cursor()
    for t in ["skus", "transactions", "sales", "order_logs", "receiving_logs"]:
        cur2.execute(f"SELECT COUNT(*) FROM {t}")
        print(f"  {t}: {cur2.fetchone()[0]} righe")
    conn2.close()
    print(f"\n✓ Seed completato. Storico: {START} → {START + timedelta(days=DAYS)}")


if __name__ == "__main__":
    run()
