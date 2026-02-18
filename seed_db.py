"""
Seed script: wipes current data and inserts 2 realistic SKUs
with 31 days of history (Jan 19 – Feb 18, 2026).

SKU-1  LATTE_UHT   – latte UHT 1L, vendite stabili ~15/giorno
SKU-2  SUCCO_ACE   – succo ACE 750ml, vendite variabili ~8/giorno

History includes:
  - SNAPSHOT day 0 (initial stock)
  - Daily SALE records
  - 1 ORDER + 1 RECEIPT mid-month (per SKU)
  - 1 ADJUST (inventory correction, week 3)
  - sales table (daily aggregates mirror)
  - order_logs + receiving_logs entries
"""

import sqlite3
from datetime import date, timedelta
from pathlib import Path
import random

DB_PATH = Path("data/app.db")
START = date(2026, 1, 19)   # 31 days back from today (Feb 18)
TODAY = date(2026, 2, 18)
DAYS = (TODAY - START).days + 1   # 31

random.seed(42)  # reproducible

# ---------------------------------------------------------------------------
# SKU definitions
# ---------------------------------------------------------------------------
SKUS = [
    {
        "sku": "LATTE_UHT",
        "description": "Latte UHT Intero 1L",
        "ean": "8001120812575",
        "moq": 12,
        "pack_size": 12,
        "lead_time_days": 3,
        "review_period": 7,
        "safety_stock": 24,
        "shelf_life_days": 90,
        "min_shelf_life_days": 30,
        "waste_penalty_mode": "soft",
        "waste_penalty_factor": 0.8,
        "waste_risk_threshold": 0.15,
        "max_stock": 300,
        "reorder_point": 50,
        "demand_variability": "STABLE",
        "category": "DAIRY",
        "department": "FOOD",
        "oos_boost_percent": 10.0,
        "oos_detection_mode": "strict",
        "oos_popup_preference": "ask",
        "forecast_method": "simple",
        "in_assortment": 1,
        "target_csl": 0.95,
    },
    {
        "sku": "SUCCO_ACE",
        "description": "Succo ACE Arancia Carota Limone 750ml",
        "ean": "8000735033068",
        "moq": 6,
        "pack_size": 6,
        "lead_time_days": 5,
        "review_period": 7,
        "safety_stock": 12,
        "shelf_life_days": 60,
        "min_shelf_life_days": 21,
        "waste_penalty_mode": "soft",
        "waste_penalty_factor": 0.6,
        "waste_risk_threshold": 20.0,
        "max_stock": 180,
        "reorder_point": 25,
        "demand_variability": "HIGH",
        "category": "BEVERAGES",
        "department": "FOOD",
        "oos_boost_percent": 15.0,
        "oos_detection_mode": "relaxed",
        "oos_popup_preference": "ask",
        "forecast_method": "simple",
        "in_assortment": 1,
        "target_csl": 0.90,
    },
]

# ---------------------------------------------------------------------------
# Demand profiles  (base qty/day + weekend multiplier + noise)
# ---------------------------------------------------------------------------
def daily_sales(sku_id: str, d: date) -> int:
    """Return realistic daily sales qty."""
    is_weekend = d.weekday() >= 5
    if sku_id == "LATTE_UHT":
        base = 18 if is_weekend else 14
        noise = random.randint(-3, 3)
    else:  # SUCCO_ACE
        base = 12 if is_weekend else 7
        noise = random.randint(-2, 4)
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
    # 3. Build transaction ledger + sales
    # -----------------------------------------------------------------------
    # Initial stock levels (day 0 = START)
    initial_stock = {"LATTE_UHT": 120, "SUCCO_ACE": 60}
    # Tracking on-hand for ORDER placement logic
    on_hand = dict(initial_stock)

    # Day map: ORDER placed on day 10, RECEIPT on day 13 (lead_time=3)
    #          ORDER placed on day 10, RECEIPT on day 15 (lead_time=5)
    order_qty   = {"LATTE_UHT": 96,  "SUCCO_ACE": 48}   # 8×12 and 8×6
    order_day   = {"LATTE_UHT": 10,  "SUCCO_ACE": 10}    # day offset from START
    receipt_day = {"LATTE_UHT": 13,  "SUCCO_ACE": 15}
    adjust_day  = {"LATTE_UHT": 20,  "SUCCO_ACE": 20}    # inventory count correction
    adjust_delta= {"LATTE_UHT": -3,  "SUCCO_ACE": +2}    # ADJUST: set on_hand to value

    order_ids   = {}  # sku -> order_id string

    # SNAPSHOT (day 0)
    for sku_id in ["LATTE_UHT", "SUCCO_ACE"]:
        cur.execute("""
            INSERT INTO transactions (date, sku, event, qty, receipt_date, note)
            VALUES (?, ?, 'SNAPSHOT', ?, NULL, 'Initial stock count')
        """, (START.isoformat(), sku_id, initial_stock[sku_id]))

    # Days 1..30 (START+1 .. TODAY-1), TODAY = day 31
    for offset in range(1, DAYS):
        d = START + timedelta(days=offset)
        d_str = d.isoformat()

        for sku_id in ["LATTE_UHT", "SUCCO_ACE"]:
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
                # order_logs
                cur.execute("""
                    INSERT INTO order_logs (order_id, date, sku, qty_ordered, qty_received, status, receipt_date)
                    VALUES (?, ?, ?, ?, ?, 'RECEIVED', ?)
                """, (order_id, d_str, sku_id, qty, qty, (START + timedelta(days=receipt_day[sku_id])).isoformat()))

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
                # receiving_logs
                cur.execute("""
                    INSERT INTO receiving_logs (document_id, receipt_id, date, sku, qty_received, receipt_date, order_ids)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (receipt_id, receipt_id, d_str, sku_id, qty, d_str, order_id))

            # ADJUST event (inventory count)
            if offset == adjust_day[sku_id]:
                delta = adjust_delta[sku_id]
                # ADJUST = absolute on_hand reset; we store corrected value
                corrected = max(0, on_hand.get(sku_id, 0) + delta)
                cur.execute("""
                    INSERT INTO transactions (date, sku, event, qty, receipt_date, note)
                    VALUES (?, ?, 'ADJUST', ?, NULL, 'Conteggio inventario settimanale')
                """, (d_str, sku_id, corrected))

            # SALE event + sales table
            sale_qty = daily_sales(sku_id, d)
            if sale_qty > 0:
                cur.execute("""
                    INSERT INTO transactions (date, sku, event, qty, receipt_date, note)
                    VALUES (?, ?, 'SALE', ?, NULL, '')
                """, (d_str, sku_id, -sale_qty))
                cur.execute("""
                    INSERT INTO sales (date, sku, qty_sold, promo_flag)
                    VALUES (?, ?, ?, 0)
                """, (d_str, sku_id, sale_qty))

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
    print("\n✓ Seed completato.")


if __name__ == "__main__":
    run()
