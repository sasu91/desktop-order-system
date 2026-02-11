# Desktop Order System

A Windows desktop application (Python 3.12 + Tkinter) for managing stock and generating reorder proposals.

## Architecture

**Ledger-driven design**: All stock state is calculated from a transaction ledger (CSV), not stored.

- **Domain Layer** (`src/domain/`): Pure business logic (models, stock calculation engine)
- **Persistence Layer** (`src/persistence/`): CSV I/O with auto-create functionality
- **Workflows** (`src/workflows/`): High-level operations (order, receiving, exceptions)
- **GUI** (`src/gui/`): Tkinter desktop interface with multiple tabs
- **Tests** (`tests/`): Comprehensive test suite for core logic

## Key Concepts

### Stock Calculation (AsOf Logic)

Stock at a given date is calculated by applying all ledger events with `date < AsOf_date`:

```python
from src.domain.ledger import StockCalculator
from datetime import date

stock = StockCalculator.calculate_asof(
    sku="SKU001",
    asof_date=date.today(),
    transactions=[...],
    sales_records=[...]
)
# Returns: Stock(sku="SKU001", on_hand=100, on_order=50)
```

### Ledger Events

- **SNAPSHOT**: Base inventory reset (on_hand := qty)
- **ORDER**: Increase on_order (on_order += qty)
- **RECEIPT**: Receipt closure (on_order -= qty, on_hand += qty)
- **SALE**: Reduce on_hand (from sales.csv)
- **WASTE**: Reduce on_hand
- **ADJUST**: Absolute set (on_hand := qty)
- **UNFULFILLED**: Tracking only (no stock impact)

### CSV Files (Auto-Created on First Run)

- `data/skus.csv`: SKU master data
- `data/transactions.csv`: Ledger of all events (source of truth)
- `data/sales.csv`: Daily sales records
- `data/order_logs.csv`: Order confirmation history
- `data/receiving_logs.csv`: Receiving closure history

### Holiday & Closure Management

**Effect-aware holiday system** blocking orders and/or receipts based on scope:

- **Italian public holidays** (12 total): automatic, including Easter calculation
- **Custom closures**: configurable via `data/holidays.json`
- **Granular effects**:
  - `no_order`: blocks orders only (supplier closed, can still receive)
  - `no_receipt`: blocks receipts only (inventory day, can still order)
  - `both`: blocks both (national holidays)

Example `holidays.json`:
```json
{
  "holidays": [
    {
      "name": "Chiusura estiva",
      "scope": "store",
      "effect": "both",
      "type": "range",
      "params": {"start": "2026-08-10", "end": "2026-08-20"}
    },
    {
      "name": "Inventario magazzino",
      "scope": "warehouse",
      "effect": "no_receipt",
      "type": "single",
      "params": {"date": "2026-12-31"}
    }
  ]
}
```

**Integration**: `next_receipt_date()` automatically skips holidays based on effect.

📖 **Full documentation**: See [HOLIDAY_SYSTEM.md](HOLIDAY_SYSTEM.md)

## Setup & Running

### Requirements

- Python 3.12
- Windows (Tkinter support)

### Install Dependencies

```bash
pip install -r requirements.txt
```

### Run Tests

```bash
python -m pytest tests/
```

### Run GUI

```bash
python main.py
```

App auto-creates all required CSV files on first run.

## Development Workflow

### Core Testing

```bash
# Run all tests
python -m pytest tests/

# Run specific test file
python -m pytest tests/test_stock_calculation.py -v

# Run with coverage
python -m pytest tests/ --cov=src
```

### Code Patterns

- **No hardcoded dates in business logic**: Pass date as parameter
- **Deterministic ordering**: Events on same day sorted by type priority
- **Idempotent operations**: Receiving closure, exception recording use idempotency keys
- **Graceful error handling**: Invalid EAN/CSV data → warning, not crash

## Project Structure

```
desktop-order-system/
├── src/
│   ├── domain/
│   │   ├── models.py          # SKU, Transaction, Stock
│   │   └── ledger.py          # Stock calculation engine
│   ├── persistence/
│   │   └── csv_layer.py       # CSV I/O with auto-create
│   ├── workflows/
│   │   ├── order.py           # Order proposal & confirmation
│   │   └── receiving.py       # Receiving closure & exceptions
│   └── gui/
│       └── app.py             # Tkinter main window
├── tests/
│   ├── test_stock_calculation.py
│   ├── test_workflows.py
│   └── test_persistence.py
├── data/                      # CSV files (auto-created)
├── main.py                    # Entry point
├── config.py                  # Configuration
├── requirements.txt           # Python dependencies
└── pytest.ini                 # Pytest configuration
```

## Key Design Decisions

1. **CSV-only storage**: No database, simple file-based persistence
2. **Ledger as source of truth**: Stock state is calculated, not stored
3. **Idempotent operations**: Multiple receipt closures with same ID don't duplicate events
4. **Deterministic ordering**: Events sorted consistently; recalculating same date yields same result
5. **No future-dated logic**: Date is always passed as parameter; no `datetime.now()` in domain logic

## Testing Philosophy

- **Domain logic**: Fully testable without I/O via pure functions
- **Persistence**: Tested with temporary directories (no real file system pollution)
- **Integration**: CSV layer + calculation tested together
- **Regression**: Known scenarios (legacy migration, idempotent receiving) with fixed test data

## Contributing

- Isolate business logic from I/O
- Use explicit dates; avoid `datetime.now()` in domain code
- Validate early; fail fast with clear error messages
- Write tests for new features (especially ledger-related changes)
- Document ledger event impacts clearly