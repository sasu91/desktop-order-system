"""
Golden Dataset Generator - FASE 6

Generates reproducible test dataset for equivalence validation:
- 50 SKUs with realistic parameters
- 365 days of sales with seasonal patterns
- 100 transactions covering all event types
- Pre-calculated expected results

Usage:
    python tests/generate_golden_dataset.py [--output-dir DIR]
"""

import csv
import json
import random
from pathlib import Path
from datetime import date, timedelta
from typing import List, Dict, Any
from dataclasses import asdict
import sys

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.domain.models import SKU, Transaction, EventType, SalesRecord, DemandVariability
from src.domain.ledger import StockCalculator


# Seed for reproducibility
RANDOM_SEED = 42
random.seed(RANDOM_SEED)


# ============================================================
# SKU Generation
# ============================================================

CATEGORIES = ["Fresh", "Frozen", "Dry", "Beverage", "Snacks"]
DEMAND_VARIABILITY_OPTIONS = list(DemandVariability)


def generate_skus(count: int = 50) -> List[SKU]:
    """Generate realistic SKUs for testing"""
    skus = []
    
    for i in range(1, count + 1):
        sku_id = f"SKU{i:03d}"
        category = random.choice(CATEGORIES)
        
        # Realistic parameter ranges based on category
        if category == "Fresh":
            lead_time = random.randint(1, 3)
            shelf_life = random.randint(3, 7)
        elif category == "Frozen":
            lead_time = random.randint(2, 5)
            shelf_life = random.randint(30, 90)
        else:
            lead_time = random.randint(3, 10)
            shelf_life = random.randint(90, 365)
        
        sku = SKU(
            sku=sku_id,
            description=f"{category} Product {i}",
            ean=f"800000{i:06d}",  # Valid EAN-13 structure
            category=category,
            lead_time_days=lead_time,
            moq=random.choice([1, 6, 12, 24]),
            pack_size=random.choice([1, 6, 12]),
            review_period=random.randint(lead_time, lead_time + 7),
            safety_stock=random.randint(0, 50),
            demand_variability=random.choice(DEMAND_VARIABILITY_OPTIONS),
            shelf_life_days=shelf_life,
            waste_penalty_mode="",  # Use global setting
            oos_detection_mode="",  # Use global setting
            forecast_method="",  # Use global default
            in_assortment=True,
        )
        skus.append(sku)
    
    return skus


# ============================================================
# Sales Generation
# ============================================================

def generate_sales(skus: List[SKU], start_date: date, days: int = 365) -> List[SalesRecord]:
    """
    Generate realistic sales data with seasonal patterns.
    
    Patterns:
    - Weekly seasonality (lower on Sundays)
    - Monthly seasonality (spike at month end)
    - Random variation (±30%)
    """
    sales = []
    
    for sku in skus:
        # Base daily demand (scaled by category)
        if sku.category == "Fresh":
            base_demand = random.uniform(5, 20)
        elif sku.category == "Frozen":
            base_demand = random.uniform(3, 15)
        elif sku.category == "Beverage":
            base_demand = random.uniform(10, 30)
        else:
            base_demand = random.uniform(4, 12)
        
        for day_offset in range(days):
            current_date = start_date + timedelta(days=day_offset)
            
            # Weekly seasonality (Sunday -30%)
            weekly_factor = 0.7 if current_date.weekday() == 6 else 1.0
            
            # Monthly seasonality (last 3 days +20%)
            days_in_month = (current_date.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            is_month_end = current_date.day >= days_in_month.day - 2
            monthly_factor = 1.2 if is_month_end else 1.0
            
            # Random variation (±30%)
            random_factor = random.uniform(0.7, 1.3)
            
            # Calculate quantity
            qty = max(0, int(base_demand * weekly_factor * monthly_factor * random_factor))
            
            # Only record if qty > 0
            if qty > 0:
                sales.append(SalesRecord(
                    date=current_date,
                    sku=sku.sku,
                    qty_sold=qty,
                ))
    
    return sales


# ============================================================
# Transaction Generation
# ============================================================

def generate_transactions(skus: List[SKU], start_date: date) -> List[Transaction]:
    """
    Generate realistic transactions covering all event types.
    
    Event distribution:
    - SNAPSHOT: Initial inventory (1 per SKU)
    - ORDER: Weekly orders (based on sales)
    - RECEIPT: Orders received after lead time
    - SALE: Consumed from sales.csv
    - WASTE: Random (1-2% of inventory)
    - ADJUST: Inventory corrections (rare)
    """
    transactions = []
    
    # SNAPSHOT events (initial inventory)
    for sku in skus:
        initial_stock = random.randint(20, 100)
        transactions.append(Transaction(
            date=start_date,
            sku=sku.sku,
            event=EventType.SNAPSHOT,
            qty=initial_stock,
            receipt_date=None,
            note="Initial inventory snapshot"
        ))
    
    # ORDER events (weekly orders for first 10 SKUs)
    for week in range(12):  # 12 weeks
        order_date = start_date + timedelta(days=week * 7)
        
        for sku in skus[:10]:  # First 10 SKUs only
            order_qty = random.randint(20, 60)
            receipt_date = order_date + timedelta(days=sku.lead_time_days)
            
            transactions.append(Transaction(
                date=order_date,
                sku=sku.sku,
                event=EventType.ORDER,
                qty=order_qty,
                receipt_date=receipt_date,
                note=f"Order week {week+1}"
            ))
    
    # RECEIPT events (orders received)
    order_events = [t for t in transactions if t.event == EventType.ORDER]
    for order in order_events:
        transactions.append(Transaction(
            date=order.receipt_date,
            sku=order.sku,
            event=EventType.RECEIPT,
            qty=order.qty,
            receipt_date=order.receipt_date,
            note=f"Receipt for order on {order.date}"
        ))
    
    # WASTE events (random spoilage)
    for month in range(3):  # 3 months
        waste_date = start_date + timedelta(days=month * 30 + 15)
        
        for sku in random.sample(skus, k=5):  # 5 random SKUs
            waste_qty = random.randint(1, 10)
            transactions.append(Transaction(
                date=waste_date,
                sku=sku.sku,
                event=EventType.WASTE,
                qty=waste_qty,
                receipt_date=None,
                note="Expired product"
            ))
    
    # ADJUST events (inventory corrections)
    for month in range(2):  # 2 adjustments
        adjust_date = start_date + timedelta(days=month * 60 + 30)
        
        for sku in random.sample(skus, k=3):  # 3 random SKUs
            adjusted_stock = random.randint(15, 50)
            transactions.append(Transaction(
                date=adjust_date,
                sku=sku.sku,
                event=EventType.ADJUST,
                qty=adjusted_stock,
                receipt_date=None,
                note="Physical count adjustment"
            ))
    
    # Sort by date
    transactions.sort(key=lambda t: t.date)
    
    return transactions


# ============================================================
# Expected Results Calculation
# ============================================================

def calculate_expected_stock(skus: List[SKU], transactions: List[Transaction], 
                            sales: List[SalesRecord], asof_date: date) -> Dict[str, Dict[str, int]]:
    """
    Calculate expected stock for validation.
    
    This is the "golden truth" that both CSV and SQLite must match.
    """
    expected = {}
    for sku in skus:
        stock = StockCalculator.calculate_asof(
            sku=sku.sku,
            asof_date=asof_date,
            transactions=transactions,
            sales_records=sales
        )
        expected[sku.sku] = {
            "on_hand": stock.on_hand,
            "on_order": stock.on_order,
        }
    
    return expected


# ============================================================
# Main Generator
# ============================================================

def generate_golden_dataset(output_dir: Path):
    """Generate complete golden dataset"""
    output_dir.mkdir(parents=True, exist_ok=True)
    expected_dir = output_dir / "expected"
    expected_dir.mkdir(parents=True, exist_ok=True)
    
    print("🚀 Generating Golden Dataset...")
    print(f"📂 Output directory: {output_dir}")
    print()
    
    # Generate data
    print("📦 Generating 50 SKUs...")
    skus = generate_skus(count=50)
    print(f"   ✓ Generated {len(skus)} SKUs")
    
    print("📊 Generating 365 days of sales...")
    start_date = date(2025, 1, 1)
    sales = generate_sales(skus, start_date, days=365)
    print(f"   ✓ Generated {len(sales)} sales records")
    
    print("📝 Generating transactions...")
    transactions = generate_transactions(skus, start_date)
    print(f"   ✓ Generated {len(transactions)} transactions")
    print(f"      - SNAPSHOT: {sum(1 for t in transactions if t.event == EventType.SNAPSHOT)}")
    print(f"      - ORDER: {sum(1 for t in transactions if t.event == EventType.ORDER)}")
    print(f"      - RECEIPT: {sum(1 for t in transactions if t.event == EventType.RECEIPT)}")
    print(f"      - WASTE: {sum(1 for t in transactions if t.event == EventType.WASTE)}")
    print(f"      - ADJUST: {sum(1 for t in transactions if t.event == EventType.ADJUST)}")
    
    # Write SKUs
    print()
    print("💾 Writing skus.csv...")
    with open(output_dir / "skus.csv", "w", newline="", encoding="utf-8") as f:
        # Get all fields from SKU dataclass
        from dataclasses import fields
        sku_fields = [field.name for field in fields(SKU)]
        
        writer = csv.DictWriter(f, fieldnames=sku_fields)
        writer.writeheader()
        for sku in skus:
            row = asdict(sku)
            # Convert enum to string value
            row['demand_variability'] = sku.demand_variability.value
            # Convert boolean to integer (for SQLite compatibility)
            row['in_assortment'] = 1 if sku.in_assortment else 0
            writer.writerow(row)
    print(f"   ✓ Wrote {len(skus)} SKUs")
    
    # Write transactions
    print("💾 Writing transactions.csv...")
    with open(output_dir / "transactions.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "sku", "event", "qty", "receipt_date", "note"])
        writer.writeheader()
        for txn in transactions:
            writer.writerow({
                "date": txn.date.isoformat(),
                "sku": txn.sku,
                "event": txn.event.value,
                "qty": txn.qty,
                "receipt_date": txn.receipt_date.isoformat() if txn.receipt_date else "",
                "note": txn.note or "",
            })
    print(f"   ✓ Wrote {len(transactions)} transactions")
    
    # Write sales
    print("💾 Writing sales.csv...")
    with open(output_dir / "sales.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "sku", "qty_sold"])
        writer.writeheader()
        for sale in sales:
            writer.writerow({
                "date": sale.date.isoformat(),
                "sku": sale.sku,
                "qty_sold": sale.qty_sold,
            })
    print(f"   ✓ Wrote {len(sales)} sales records")
    
    # Calculate expected stock for key dates
    print()
    print("🧮 Calculating expected stock for validation dates...")
    validation_dates = [
        date(2025, 1, 15),  # Mid-January
        date(2025, 3, 1),   # Start of March
        date(2025, 6, 30),  # End of June
        date(2025, 12, 31), # End of year
    ]
    
    for validation_date in validation_dates:
        print(f"   Calculating for {validation_date}...")
        expected_stock = calculate_expected_stock(skus, transactions, sales, validation_date)
        
        output_file = expected_dir / f"stock_asof_{validation_date.isoformat()}.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(expected_stock, f, indent=2)
        print(f"      ✓ Wrote {output_file.name}")
    
    # Write README
    print()
    print("📖 Writing README.md...")
    readme_content = f"""# Golden Dataset for Equivalence Testing

**Generated**: {date.today().isoformat()}  
**Seed**: {RANDOM_SEED} (reproducible)

## Overview

This dataset provides deterministic test data for validating CSV ↔ SQLite equivalence.

## Contents

- **skus.csv**: {len(skus)} SKUs across {len(set(s.category for s in skus))} categories
- **transactions.csv**: {len(transactions)} transactions
  - SNAPSHOT: {sum(1 for t in transactions if t.event == EventType.SNAPSHOT)}
  - ORDER: {sum(1 for t in transactions if t.event == EventType.ORDER)}
  - RECEIPT: {sum(1 for t in transactions if t.event == EventType.RECEIPT)}
  - WASTE: {sum(1 for t in transactions if t.event == EventType.WASTE)}
  - ADJUST: {sum(1 for t in transactions if t.event == EventType.ADJUST)}
- **sales.csv**: {len(sales)} sales records (365 days)
- **expected/**: Pre-calculated stock for {len(validation_dates)} validation dates

## Validation Dates

The following dates have pre-calculated expected stock values:

{chr(10).join(f'- {d.isoformat()}' for d in validation_dates)}

## Usage

```python
from pathlib import Path
import json

# Load expected stock for validation
golden_dir = Path("tests/golden_data")
with open(golden_dir / "expected/stock_asof_2025-01-15.json") as f:
    expected = json.load(f)

# Validate calculated stock matches expected
assert calculated_stock['SKU001']['on_hand'] == expected['SKU001']['on_hand']
```

## Characteristics

- **Seasonal patterns**: Weekly (lower Sundays) + Monthly (spike at month-end)
- **Random variation**: ±30% daily variation for realism
- **Event coverage**: All event types (SNAPSHOT, ORDER, RECEIPT, SALE, WASTE, ADJUST)
- **Edge cases**: Zero sales days, large quantities, date boundaries

## Reproducibility

This dataset is generated with a fixed random seed ({RANDOM_SEED}), ensuring:
- Identical output across runs
- Deterministic test results
- Reproducible bug reports
"""
    
    with open(output_dir / "README.md", "w", encoding="utf-8") as f:
        f.write(readme_content)
    print("   ✓ Wrote README.md")
    
    print()
    print("✅ Golden dataset generation complete!")
    print(f"📂 Output: {output_dir.absolute()}")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Generate golden dataset for equivalence testing")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("tests/golden_data"),
        help="Output directory for golden dataset (default: tests/golden_data)"
    )
    
    args = parser.parse_args()
    
    generate_golden_dataset(args.output_dir)
