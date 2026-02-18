# Golden Dataset for Equivalence Testing

**Generated**: 2026-02-17  
**Seed**: 42 (reproducible)

## Overview

This dataset provides deterministic test data for validating CSV ↔ SQLite equivalence.

## Contents

- **skus.csv**: 50 SKUs across 5 categories
- **transactions.csv**: 311 transactions
  - SNAPSHOT: 50
  - ORDER: 120
  - RECEIPT: 120
  - WASTE: 15
  - ADJUST: 6
- **sales.csv**: 18250 sales records (365 days)
- **expected/**: Pre-calculated stock for 4 validation dates

## Validation Dates

The following dates have pre-calculated expected stock values:

- 2025-01-15
- 2025-03-01
- 2025-06-30
- 2025-12-31

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

This dataset is generated with a fixed random seed (42), ensuring:
- Identical output across runs
- Deterministic test results
- Reproducible bug reports
