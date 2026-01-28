# AI Coding Agent Instructions for desktop-order-system

## Project Overview
**desktop-order-system** is a Windows desktop application (Python 3.12 + Tkinter) for stock reordering management.

**Core philosophy**: Ledger-driven architecture where a transaction log (transactions.csv) is the source of truth. Stock state (on_hand, on_order) is **calculated** from the ledger "as-of" a specific date, not stored.

## Architecture

### Layer Design
- **Domain**: Models (SKU, Transaction, Stock) + pure business logic - NO I/O, deterministic, fully testable
- **Persistence**: CSV layer (auto-creates files with correct headers on first run)
- **Calculus**: Stock calculation engine (AsOf date logic, event aggregation)
- **GUI**: Tkinter-based desktop UI (tabs: Stock, Order Proposal, Order Confirmation, Receiving, Exceptions)
- **Workflows**: High-level orchestration (order proposal, confirmation, receiving closure, exception handling)

### Ledger Events (Critical: Define Impact Clearly)
**transactions.csv columns**: date (YYYY-MM-DD), sku, event, qty (signed), receipt_date, note

**Event Types & Impact**:
- **SNAPSHOT**: Base inventory reset (on_hand := qty)
- **ORDER**: Increase on_order (on_order += qty)
- **RECEIPT**: Decrease on_order, increase on_hand (on_order -= qty, on_hand += qty)
- **SALE**: Decrease on_hand (on_hand -= qty); consumed from daily sales.csv
- **WASTE**: Decrease on_hand (on_hand -= qty)
- **ADJUST**: Signed adjustment (on_hand ± qty)
- **UNFULFILLED**: Tracking only (no stock impact, but visible in reports)

**Stock Calculation Rule**: All events with date < AsOf_date are applied sequentially per SKU. Order of event application per day: deterministic (by event type priority or insertion order—document choice).

### CSV Files & Auto-Creation
Files created on first run with exact headers:
- **skus.csv**: sku, description, ean (empty EAN allowed)
- **transactions.csv**: date, sku, event, qty, receipt_date, note
- **sales.csv**: date, sku, qty_sold (daily aggregates)
- **order_logs.csv**: order_id, date, sku, qty_ordered, status
- **receiving_logs.csv**: receipt_id, date, sku, qty_received, receipt_date

If any file missing at startup → auto-create with headers, continue without error.

## Critical Patterns

### Stock State Calculation ("AsOf")
```python
# Pseudocode logic
def calculate_stock_asof(sku, asof_date):
    on_hand, on_order = 0, 0
    for event in transactions_sorted(sku, date < asof_date):
        apply_event(event, on_hand, on_order)  # Deterministic update
    return (on_hand, on_order)
```
- **No hidden state**: Stock derived from ledger always; old "inventory snapshot" CSV is **never** updated by app logic
- **Idempotency**: Recalculating same date twice yields same result

### Migrating Legacy Data
If `legacy_inventory.csv` exists but ledger is empty:
1. For each SKU in legacy: create **one** SNAPSHOT event (date: inferred or param)
2. Do NOT migrate if ledger already has events (avoid duplication)
3. Validate: post-migration stock AsOf ≥ first transaction date must match legacy value

### Order Confirmation (Vinculant)
1. User selects SKU+qty to order → generates proposal
2. System calculates receipt_date (e.g., today + lead_time) automatically
3. User confirms → create ORDER events in ledger
4. Write to order_logs.csv with order_id (deterministic key)
5. POST-confirmation: show receipt window (5 items/page, barcode/EAN display, Space to paginate)

**EAN Validation**: Invalid EAN → log warning, display "Invalid EAN", skip barcode render (never crash)

### Receiving Closure (Idempotent)
1. User provides receipt_id (or date-based key), SKU list, quantities
2. Deterministic key: `receipt_id = f"{receipt_date}_{origin_supplier}_{sku}"` (or user-provided)
3. Check if receipt already processed: if yes, skip (or show "already received")
4. Create RECEIPT events in ledger, update receiving_logs.csv
5. Stock state updates next day (date < asof_date rule)
6. Revert test: apply same receipt twice → state unchanged second time

### Exception Handling (Daily + Revertible)
Quick entry for WASTE, ADJUST, UNFULFILLED:
- **Idempotency key**: date + sku + event_type
- **Revertion**: For date D, find all exceptions of type X for SKU → option to "undo all today's X-type for SKU"
- **Storage**: Exceptions written to ledger as normal events; no separate table (single source of truth)

## Code Style
- **Naming**: `snake_case` for functions/vars; `PascalCase` for classes; `CAPS_LOCK` for constants
- **Determinism**: No `datetime.now()` in domain logic—pass date as param; only GUI/main calls system time
- **Testing**: All domain logic must be unit-testable without file I/O. Use dependency injection (pass CSV reader as param).
- **Validation**: Early returns with explicit error messages (not exceptions for business logic)
- **Comments**: Document "why" for non-obvious ledger rules or date handling

## Key Files (Reference)
- `src/domain/models.py` – SKU, Transaction, Stock domain objects
- `src/domain/ledger.py` – Stock calculation engine (AsOf logic)
- `src/persistence/csv_layer.py` – CSV read/write with auto-create
- `src/workflows/order.py` – Order proposal, confirmation logic
- `src/workflows/receiving.py` – Receiving closure, idempotency
- `src/gui/app.py` – Tkinter main window, tab orchestration
- `tests/test_stock_calculation.py` – Core ledger + AsOf math
- `tests/test_idempotency.py` – Receiving, exception revert scenarios

## When Implementing
1. **Isolate domain logic** from I/O; test without files
2. **Use explicit dates**: Never assume "today" in business logic
3. **Validate early**: Check SKU exists, EAN format, date format before applying events
4. **Log decisions**: If choosing default value, log it (e.g., lead_time=7 days, rationale: not provided)
5. **Handle missing data gracefully**: Empty files, unknown SKU, invalid EAN → informative message, never crash

## Testing Strategy
- **Domain tests**: Pure functions, mocked CSV data, parametrized test cases (empty ledger, multi-year history, edge dates)
- **Integration tests**: CSV layer + calculation (write, read, recalc)
- **GUI tests**: (Deferred) Verify tab updates on ledger change
- **Regression tests**: Known scenarios (legacy migration, idempotent receiving) with fixed datasets

## Development Workflow
```bash
python -m pytest tests/  # Run all tests
python src/gui/app.py   # Run GUI (Windows, Python 3.12)
```
Auto-create data files on first run → app boots without manual setup.

---
**Last updated**: January 2026  
**Status**: Full design—implementation in progress  
**Constraints**: Windows, Python 3.12, Tkinter, CSV storage, zero manual ledger initialization
