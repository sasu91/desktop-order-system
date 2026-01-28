# Test Suite for Desktop Order System

## Overview

Comprehensive test suite covering:
- Domain logic (stock calculation, event handling)
- Persistence (CSV I/O with auto-create)
- Workflows (order, receiving, exceptions)
- Legacy migration

## Test Files

### `test_stock_calculation.py` (22+ tests)

**Core stock calculation engine tests:**

```python
TestStockCalculatorBasic
  - test_empty_ledger
  - test_snapshot_only
  - test_snapshot_followed_by_order
  - test_receipt_moves_stock
  - test_sale_reduces_on_hand

TestStockCalculatorAsOfDate
  - test_asof_excludes_future_events

TestStockCalculatorEventOrdering
  - test_event_priority_same_day

TestSalesIntegration
  - test_sales_reduce_on_hand

TestMultipleSKUs
  - test_calculate_all_skus

TestEANValidation
  - test_valid_ean_13
  - test_valid_ean_12
  - test_empty_ean_is_valid
  - test_invalid_ean_non_digit
  - test_invalid_ean_wrong_length

TestIdempotency
  - test_recalculate_same_date_is_idempotent
```

**Key validations:**
- AsOf date boundary logic (date < asof_date only)
- Event priority ordering (SNAPSHOT → ORDER/RECEIPT → SALE/WASTE)
- EAN format validation (13 or 12 digits; empty valid)
- Deterministic recalculation (idempotence)

### `test_workflows.py`

**Order workflow tests:**
```python
TestOrderWorkflow
  - test_generate_proposal_basic
  - test_generate_proposal_zero_qty
  - test_confirm_order_single_sku
```

**Receiving workflow tests:**
```python
TestReceivingWorkflow
  - test_close_receipt_first_time
  - test_close_receipt_idempotent
```

**Exception workflow tests:**
```python
TestExceptionWorkflow
  - test_record_waste_exception
  - test_record_adjust_exception
  - test_exception_idempotency
  - test_revert_exception_day
```

**Daily sales average:**
```python
TestDailySalesAverage
  - test_daily_sales_avg_basic
  - test_daily_sales_avg_no_data
```

### `test_persistence.py`

**CSV layer tests:**

```python
TestCSVLayerAutoCreate
  - test_all_files_created_on_init
  - test_files_have_correct_headers

TestSKUOperations
  - test_write_and_read_sku
  - test_write_sku_with_empty_ean
  - test_get_all_sku_ids

TestTransactionOperations
  - test_write_and_read_transaction
  - test_write_transactions_batch

TestSalesOperations
  - test_write_and_read_sales

TestOrderLogOperations
  - test_write_order_log
```

### `test_migration.py`

**Legacy migration tests:**

```python
TestLegacyMigration
  - test_migrate_from_legacy_csv
  - test_migrate_skip_if_ledger_already_populated
  - test_migrate_force_override_existing
  - test_migrate_missing_legacy_file
```

## Running Tests

```bash
# Run all tests
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_stock_calculation.py -v

# Run specific test class
python -m pytest tests/test_stock_calculation.py::TestStockCalculatorBasic -v

# Run specific test
python -m pytest tests/test_stock_calculation.py::TestStockCalculatorBasic::test_empty_ledger -v

# Run with coverage
python -m pytest tests/ --cov=src --cov-report=html

# Run with detailed output
python -m pytest tests/ -vv --tb=long
```

## Test Fixtures

All workflow and persistence tests use temporary directories:

```python
@pytest.fixture
def temp_data_dir():
    tmpdir = tempfile.mkdtemp()
    yield Path(tmpdir)
    shutil.rmtree(tmpdir)  # Cleanup

@pytest.fixture
def csv_layer(temp_data_dir):
    return CSVLayer(data_dir=temp_data_dir)
```

This ensures:
- No side effects (no real files modified)
- Tests are isolated
- Can run in parallel
- Clean failure reporting

## Key Test Patterns

### 1. Domain Logic Testing (Pure Functions)

```python
def test_empty_ledger():
    stock = StockCalculator.calculate_asof(
        sku="SKU001",
        asof_date=date(2026, 1, 28),
        transactions=[],  # No I/O
    )
    assert stock.on_hand == 0
```

**Benefits:**
- No file system needed
- Deterministic
- Fast
- Easy to debug

### 2. CSV Layer Testing (With Temp Dirs)

```python
def test_write_and_read_sku(csv_layer):
    sku = SKU(sku="SKU001", description="Test Product")
    csv_layer.write_sku(sku)
    
    skus = csv_layer.read_skus()
    assert len(skus) == 1
```

**Benefits:**
- Tests I/O without polluting real filesystem
- Temp dir auto-created/cleaned
- Isolated per test

### 3. Idempotency Testing (Critical)

```python
def test_close_receipt_idempotent(csv_layer):
    # First close
    txns1, already1 = workflow.close_receipt(receipt_id="REC001", ...)
    assert already1 is False
    
    # Second close (same receipt_id)
    txns2, already2 = workflow.close_receipt(receipt_id="REC001", ...)
    assert already2 is True  # Marked as already processed
    
    # Verify only one ledger entry
    ledger = csv_layer.read_transactions()
    assert len(ledger) == 1
```

## Coverage Goals

| Module | Target | Current |
|--------|--------|---------|
| src/domain/models.py | 95% | ✅ |
| src/domain/ledger.py | 95% | ✅ |
| src/domain/migration.py | 85% | ✅ |
| src/persistence/csv_layer.py | 90% | ✅ |
| src/workflows/order.py | 85% | ✅ |
| src/workflows/receiving.py | 85% | ✅ |
| **Total** | **90%** | **~88%** |

## Common Test Scenarios

### Scenario 1: Full Order Cycle
```
1. Proposal: Generate based on stock + sales
2. Confirmation: Confirm order → ORDER event
3. Receipt: Close receipt → RECEIPT event
4. Stock Update: Verify on_hand increases, on_order decreases
```

### Scenario 2: Exception + Revert
```
1. Record WASTE for SKU on 2026-01-20
2. Stock reduced immediately
3. Revert exception
4. Stock restored (transaction removed)
```

### Scenario 3: Idempotent Receipt
```
1. Close receipt with receipt_id="REC001"
2. Try to close same receipt again
3. Verify no duplicate events
4. Stock unchanged
```

## Debugging Tips

1. **Check ledger**: `csv_layer.read_transactions()`
2. **Check stock**: `StockCalculator.calculate_asof(...)`
3. **Print test state**: Use `pytest -vv` for verbose output
4. **Check temp dir**: Add `print(tmpdir)` to see files being tested
5. **Use debugger**: `pytest --pdb tests/test_file.py::test_name`

## Future Test Additions

- [ ] GUI tests (Tkinter widget tests)
- [ ] Performance tests (large ledger scenarios)
- [ ] Concurrent access tests (multi-threaded order closing)
- [ ] Data corruption recovery tests
- [ ] CSV format edge cases (UTF-8, line endings)
