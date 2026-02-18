"""
FASE 3 Test Suite: Repository/DAL Validation

Tests:
1. SKURepository: get, upsert, list, toggle_assortment, delete
2. LedgerRepository: append_transaction, append_batch, list_transactions, delete_by_id
3. OrdersRepository: create_order_log, update_qty_received, get_unfulfilled_orders
4. ReceivingRepository: close_receipt_idempotent, get_linked_orders
5. Error handling: DuplicateKeyError, ForeignKeyError, BusinessRuleError
6. Atomicity: Rollback on batch errors
7. Idempotency: Duplicate document_id detection

Run: python test_repositories_fase3.py
"""

import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent / "src"))

from db import open_connection, initialize_database
from repositories import (
    RepositoryFactory,
    SKURepository,
    LedgerRepository,
    OrdersRepository,
    ReceivingRepository,
    DuplicateKeyError,
    ForeignKeyError,
    BusinessRuleError,
    NotFoundError
)


def setup_test_db():
    """Create fresh test database"""
    # Use in-memory database for tests
    import sqlite3
    from db import PRAGMA_CONFIG, apply_migrations
    
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    
    # Apply PRAGMAs
    cursor = conn.cursor()
    for pragma, value in PRAGMA_CONFIG.items():
        cursor.execute(f"PRAGMA {pragma}={value}")
    
    # Apply migrations
    apply_migrations(conn)
    
    return conn


def test_sku_repository():
    """Test 1: SKURepository CRUD operations"""
    print("\n" + "="*60)
    print("TEST 1: SKURepository")
    print("="*60)
    
    conn = setup_test_db()
    repo = SKURepository(conn)
    
    # Test 1a: Insert new SKU
    sku_data = {
        'sku': 'TEST001',
        'description': 'Test Product 1',
        'moq': 10,
        'pack_size': 5,
        'lead_time_days': 14
    }
    
    sku = repo.upsert(sku_data)
    assert sku == 'TEST001'
    print(f"✓ INSERT: Created SKU {sku}")
    
    # Test 1b: Get SKU
    retrieved = repo.get('TEST001')
    assert retrieved is not None
    assert retrieved['description'] == 'Test Product 1'
    assert retrieved['moq'] == 10
    print(f"✓ GET: Retrieved SKU with description '{retrieved['description']}'")
    
    # Test 1c: Update SKU (upsert existing)
    update_data = {
        'sku': 'TEST001',
        'description': 'Updated Product 1',
        'moq': 20  # Changed
    }
    
    sku = repo.upsert(update_data)
    updated = repo.get('TEST001')
    assert updated['description'] == 'Updated Product 1'
    assert updated['moq'] == 20
    print(f"✓ UPDATE: Modified SKU description and moq")
    
    # Test 1d: List SKUs
    repo.upsert({'sku': 'TEST002', 'description': 'Test Product 2'})
    repo.upsert({'sku': 'TEST003', 'description': 'Test Product 3', 'in_assortment': 0})
    
    all_skus = repo.list()
    assert len(all_skus) >= 3
    print(f"✓ LIST: Retrieved {len(all_skus)} SKUs")
    
    # Test 1e: List with filters
    active_skus = repo.list(filters={'in_assortment': 1})
    assert len(active_skus) == 2  # TEST001, TEST002 (TEST003 excluded)
    print(f"✓ LIST (filtered): {len(active_skus)} active SKUs")
    
    # Test 1f: Toggle assortment (soft delete)
    success = repo.toggle_assortment('TEST001', in_assortment=False)
    assert success
    
    sku = repo.get('TEST001')
    assert sku['in_assortment'] == 0
    print(f"✓ TOGGLE ASSORTMENT: Excluded TEST001 from assortment")
    
    # Test 1g: Exists check
    assert repo.exists('TEST001') == True
    assert repo.exists('NONEXISTENT') == False
    print(f"✓ EXISTS: Correctly identifies existing/non-existing SKUs")
    
    conn.close()
    return True


def test_ledger_repository():
    """Test 2: LedgerRepository append and query"""
    print("\n" + "="*60)
    print("TEST 2: LedgerRepository")
    print("="*60)
    
    conn = setup_test_db()
    sku_repo = SKURepository(conn)
    ledger_repo = LedgerRepository(conn)
    
    # Setup: Create SKU
    sku_repo.upsert({'sku': 'LEDGER001', 'description': 'Ledger Test'})
    
    # Test 2a: Append single transaction
    txn_id = ledger_repo.append_transaction(
        date='2026-02-17',
        sku='LEDGER001',
        event='SNAPSHOT',
        qty=100
    )
    assert txn_id > 0
    print(f"✓ APPEND: Created transaction_id={txn_id}")
    
    # Test 2b: Get by ID
    txn = ledger_repo.get_by_id(txn_id)
    assert txn is not None
    assert txn['sku'] == 'LEDGER001'
    assert txn['qty'] == 100
    print(f"✓ GET BY ID: Retrieved transaction {txn_id}")
    
    # Test 2c: Append batch
    batch = [
        {'date': '2026-02-18', 'sku': 'LEDGER001', 'event': 'SALE', 'qty': -10},
        {'date': '2026-02-19', 'sku': 'LEDGER001', 'event': 'SALE', 'qty': -15},
        {'date': '2026-02-20', 'sku': 'LEDGER001', 'event': 'RECEIPT', 'qty': 50, 'receipt_date': '2026-02-20'},
    ]
    
    txn_ids = ledger_repo.append_batch(batch)
    assert len(txn_ids) == 3
    print(f"✓ APPEND BATCH: Created {len(txn_ids)} transactions")
    
    # Test 2d: List transactions (all)
    all_txns = ledger_repo.list_transactions(sku='LEDGER001')
    assert len(all_txns) == 4  # 1 + 3
    print(f"✓ LIST: Retrieved {len(all_txns)} transactions for LEDGER001")
    
    # Test 2e: List transactions (filtered by date)
    recent_txns = ledger_repo.list_transactions(
        sku='LEDGER001',
        date_from='2026-02-19',
        date_to='2026-02-20'
    )
    assert len(recent_txns) == 2  # 2/19 SALE + 2/20 RECEIPT
    print(f"✓ LIST (date filter): {len(recent_txns)} transactions in date range")
    
    # Test 2f: List transactions (filtered by event)
    sales = ledger_repo.list_transactions(sku='LEDGER001', event='SALE')
    assert len(sales) == 2
    print(f"✓ LIST (event filter): {len(sales)} SALE transactions")
    
    # Test 2g: Delete by ID (Risk #1 resolution)
    deleted = ledger_repo.delete_by_id(txn_ids[0])
    assert deleted == True
    
    # Verify deletion
    remaining = ledger_repo.list_transactions(sku='LEDGER001')
    assert len(remaining) == 3  # 4 - 1
    print(f"✓ DELETE BY ID: Removed transaction {txn_ids[0]} (Risk #1 resolved)")
    
    # Test 2h: Count by SKU
    count = ledger_repo.count_by_sku('LEDGER001')
    assert count == 3
    print(f"✓ COUNT: {count} transactions for LEDGER001")
    
    conn.close()
    return True


def test_orders_repository():
    """Test 3: OrdersRepository lifecycle"""
    print("\n" + "="*60)
    print("TEST 3: OrdersRepository")
    print("="*60)
    
    conn = setup_test_db()
    sku_repo = SKURepository(conn)
    orders_repo = OrdersRepository(conn)
    
    # Setup: Create SKU
    sku_repo.upsert({'sku': 'ORDER001', 'description': 'Order Test'})
    
    # Test 3a: Create order log
    order_data = {
        'order_id': '20260217_001',
        'date': '2026-02-17',
        'sku': 'ORDER001',
        'qty_ordered': 100
    }
    
    order_id = orders_repo.create_order_log(order_data)
    assert order_id == '20260217_001'
    print(f"✓ CREATE: Order {order_id} created")
    
    # Test 3b: Get order
    order = orders_repo.get('20260217_001')
    assert order is not None
    assert order['qty_ordered'] == 100
    assert order['qty_received'] == 0
    assert order['status'] == 'PENDING'
    print(f"✓ GET: Retrieved order with qty_ordered={order['qty_ordered']}, status={order['status']}")
    
    # Test 3c: Update qty_received (partial receipt)
    success = orders_repo.update_qty_received('20260217_001', qty_received=30)
    assert success
    
    order = orders_repo.get('20260217_001')
    assert order['qty_received'] == 30
    assert order['status'] == 'PARTIAL'  # Auto-determined
    print(f"✓ UPDATE QTY: Partial receipt (30/100), status={order['status']}")
    
    # Test 3d: Update qty_received (full receipt)
    success = orders_repo.update_qty_received('20260217_001', qty_received=100, receipt_date='2026-02-20')
    assert success
    
    order = orders_repo.get('20260217_001')
    assert order['qty_received'] == 100
    assert order['status'] == 'RECEIVED'
    assert order['receipt_date'] == '2026-02-20'
    print(f"✓ UPDATE QTY: Full receipt (100/100), status={order['status']}")
    
    # Test 3e: Create multiple orders
    orders_repo.create_order_log({
        'order_id': '20260217_002',
        'date': '2026-02-17',
        'sku': 'ORDER001',
        'qty_ordered': 50
    })
    
    orders_repo.create_order_log({
        'order_id': '20260217_003',
        'date': '2026-02-18',
        'sku': 'ORDER001',
        'qty_ordered': 75
    })
    
    # Test 3f: Get unfulfilled orders
    unfulfilled = orders_repo.get_unfulfilled_orders(sku='ORDER001')
    assert len(unfulfilled) == 2  # 002 PENDING, 003 PENDING (001 RECEIVED)
    print(f"✓ GET UNFULFILLED: {len(unfulfilled)} pending/partial orders")
    
    # Test 3g: List orders (all)
    all_orders = orders_repo.list(sku='ORDER001')
    assert len(all_orders) == 3
    print(f"✓ LIST: {len(all_orders)} total orders")
    
    # Test 3h: List orders (filtered by status)
    received_orders = orders_repo.list(sku='ORDER001', status='RECEIVED')
    assert len(received_orders) == 1
    print(f"✓ LIST (status filter): {len(received_orders)} RECEIVED orders")
    
    conn.close()
    return True


def test_receiving_repository():
    """Test 4: ReceivingRepository with idempotency"""
    print("\n" + "="*60)
    print("TEST 4: ReceivingRepository (Idempotency)")
    print("="*60)
    
    conn = setup_test_db()
    sku_repo = SKURepository(conn)
    orders_repo = OrdersRepository(conn)
    receiving_repo = ReceivingRepository(conn)
    ledger_repo = LedgerRepository(conn)
    
    # Setup: Create SKU and order
    sku_repo.upsert({'sku': 'RECV001', 'description': 'Receiving Test'})
    orders_repo.create_order_log({
        'order_id': 'ORD001',
        'date': '2026-02-10',
        'sku': 'RECV001',
        'qty_ordered': 100
    })
    
    # Test 4a: Close receipt (first time)
    receipt_data = {
        'date': '2026-02-17',
        'sku': 'RECV001',
        'qty_received': 100,
        'receipt_date': '2026-02-20',
        'order_ids': 'ORD001',
        'receipt_id': 'REC001'
    }
    
    result = receiving_repo.close_receipt_idempotent('DOC001', receipt_data)
    assert result['status'] == 'success'
    assert result['document_id'] == 'DOC001'
    assert 'transaction_id' in result
    print(f"✓ CLOSE RECEIPT: Success (document_id=DOC001, transaction_id={result['transaction_id']})")
    
    # Test 4b: Close receipt (duplicate - idempotency)
    result2 = receiving_repo.close_receipt_idempotent('DOC001', receipt_data)
    assert result2['status'] == 'already_processed'
    assert result2['document_id'] == 'DOC001'
    print(f"✓ IDEMPOTENCY: Duplicate document_id detected (Risk #3 resolved)")
    
    # Test 4c: Verify ledger transaction created
    ledger_txns = ledger_repo.list_transactions(sku='RECV001', event='RECEIPT')
    assert len(ledger_txns) == 1
    assert ledger_txns[0]['qty'] == 100
    print(f"✓ LEDGER INTEGRATION: RECEIPT transaction created")
    
    # Test 4d: Verify order status updated
    order = orders_repo.get('ORD001')
    assert order['qty_received'] == 100
    assert order['status'] == 'RECEIVED'
    print(f"✓ ORDER UPDATE: qty_received={order['qty_received']}, status={order['status']}")
    
    # Test 4e: Get receiving log
    receipt = receiving_repo.get('DOC001')
    assert receipt is not None
    assert receipt['sku'] == 'RECV001'
    assert receipt['qty_received'] == 100
    print(f"✓ GET: Retrieved receiving log DOC001")
    
    # Test 4f: Get linked orders (Risk #6 resolution)
    linked_orders = receiving_repo.get_linked_orders('DOC001')
    assert len(linked_orders) == 1
    assert linked_orders[0] == 'ORD001'
    print(f"✓ GET LINKED ORDERS: Junction table links {len(linked_orders)} order(s) (Risk #6 resolved)")
    
    # Test 4g: List receiving logs
    receipts = receiving_repo.list(sku='RECV001')
    assert len(receipts) == 1
    print(f"✓ LIST: {len(receipts)} receiving log(s)")
    
    conn.close()
    return True


def test_error_handling():
    """Test 5: Error handling (FK, UNIQUE, CHECK)"""
    print("\n" + "="*60)
    print("TEST 5: Error Handling")
    print("="*60)
    
    conn = setup_test_db()
    sku_repo = SKURepository(conn)
    ledger_repo = LedgerRepository(conn)
    orders_repo = OrdersRepository(conn)
    
    # Setup
    sku_repo.upsert({'sku': 'ERROR001', 'description': 'Error Test'})
    
    # Test 5a: Foreign Key Error (ledger with non-existent SKU)
    try:
        ledger_repo.append_transaction(
            date='2026-02-17',
            sku='NONEXISTENT',
            event='SNAPSHOT',
            qty=100
        )
        assert False, "Should have raised ForeignKeyError"
    except ForeignKeyError as e:
        assert 'does not exist' in str(e)
        print(f"✓ ForeignKeyError: Invalid SKU rejected")
    
    # Test 5b: Business Rule Error (invalid event type)
    try:
        ledger_repo.append_transaction(
            date='2026-02-17',
            sku='ERROR001',
            event='INVALID_EVENT',
            qty=100
        )
        assert False, "Should have raised BusinessRuleError"
    except BusinessRuleError as e:
        assert 'Invalid event type' in str(e) or 'business rule' in str(e).lower()
        print(f"✓ BusinessRuleError: Invalid event type rejected")
    
    # Test 5c: Duplicate Key Error (order_id)
    orders_repo.create_order_log({
        'order_id': 'DUP001',
        'date': '2026-02-17',
        'sku': 'ERROR001',
        'qty_ordered': 50
    })
    
    try:
        orders_repo.create_order_log({
            'order_id': 'DUP001',  # Duplicate
            'date': '2026-02-18',
            'sku': 'ERROR001',
            'qty_ordered': 75
        })
        assert False, "Should have raised DuplicateKeyError"
    except DuplicateKeyError as e:
        assert 'already exists' in str(e)
        print(f"✓ DuplicateKeyError: Duplicate order_id rejected")
    
    # Test 5d: Business Rule Error (qty_received > qty_ordered)
    try:
        orders_repo.update_qty_received('DUP001', qty_received=999)
        assert False, "Should have raised BusinessRuleError"
    except BusinessRuleError as e:
        assert 'exceeds' in str(e)
        print(f"✓ BusinessRuleError: qty_received > qty_ordered rejected")
    
    # Test 5e: Delete SKU with transaction history (RESTRICT)
    ledger_repo.append_transaction(
        date='2026-02-17',
        sku='ERROR001',
        event='SNAPSHOT',
        qty=100
    )
    
    try:
        sku_repo.delete('ERROR001')
        assert False, "Should have raised ForeignKeyError"
    except ForeignKeyError as e:
        assert 'transaction history' in str(e)
        print(f"✓ ForeignKeyError: Cannot delete SKU with history (ON DELETE RESTRICT)")
    
    conn.close()
    return True


def test_atomicity():
    """Test 6: Transaction atomicity (batch rollback)"""
    print("\n" + "="*60)
    print("TEST 6: Transaction Atomicity")
    print("="*60)
    
    conn = setup_test_db()
    sku_repo = SKURepository(conn)
    ledger_repo = LedgerRepository(conn)
    
    # Setup
    sku_repo.upsert({'sku': 'ATOMIC001', 'description': 'Atomic Test'})
    
    # Test 6a: Batch append with error (should rollback entire batch)
    batch = [
        {'date': '2026-02-17', 'sku': 'ATOMIC001', 'event': 'SNAPSHOT', 'qty': 100},
        {'date': '2026-02-18', 'sku': 'ATOMIC001', 'event': 'SALE', 'qty': -10},
        {'date': '2026-02-19', 'sku': 'NONEXISTENT', 'event': 'SALE', 'qty': -5},  # ERROR: Invalid SKU
    ]
    
    try:
        ledger_repo.append_batch(batch)
        assert False, "Should have raised ForeignKeyError"
    except ForeignKeyError:
        pass  # Expected
    
    # Verify NO transactions inserted (rollback worked)
    txns = ledger_repo.list_transactions(sku='ATOMIC001')
    assert len(txns) == 0, f"Expected 0 transactions after rollback, got {len(txns)}"
    print(f"✓ ATOMICITY: Batch rollback on error (0 transactions inserted)")
    
    # Test 6b: Successful batch (all or nothing)
    batch_valid = [
        {'date': '2026-02-17', 'sku': 'ATOMIC001', 'event': 'SNAPSHOT', 'qty': 100},
        {'date': '2026-02-18', 'sku': 'ATOMIC001', 'event': 'SALE', 'qty': -10},
        {'date': '2026-02-19', 'sku': 'ATOMIC001', 'event': 'SALE', 'qty': -5},
    ]
    
    txn_ids = ledger_repo.append_batch(batch_valid)
    assert len(txn_ids) == 3
    
    txns = ledger_repo.list_transactions(sku='ATOMIC001')
    assert len(txns) == 3
    print(f"✓ ATOMICITY: All-or-nothing batch insert (3 transactions)")
    
    conn.close()
    return True


def test_repository_factory():
    """Test 7: RepositoryFactory convenience"""
    print("\n" + "="*60)
    print("TEST 7: RepositoryFactory")
    print("="*60)
    
    conn = setup_test_db()
    factory = RepositoryFactory(conn)
    
    # Get repository instances
    sku_repo = factory.skus()
    ledger_repo = factory.ledger()
    orders_repo = factory.orders()
    receiving_repo = factory.receiving()
    
    assert isinstance(sku_repo, SKURepository)
    assert isinstance(ledger_repo, LedgerRepository)
    assert isinstance(orders_repo, OrdersRepository)
    assert isinstance(receiving_repo, ReceivingRepository)
    
    print(f"✓ FACTORY: Created 4 repository instances")
    
    # Verify shared connection
    assert sku_repo.conn is conn
    assert ledger_repo.conn is conn
    print(f"✓ SHARED CONNECTION: All repositories use same connection")
    
    conn.close()
    return True


def run_all_tests():
    """Run all repository tests"""
    tests = [
        ("SKURepository", test_sku_repository),
        ("LedgerRepository", test_ledger_repository),
        ("OrdersRepository", test_orders_repository),
        ("ReceivingRepository (Idempotency)", test_receiving_repository),
        ("Error Handling", test_error_handling),
        ("Transaction Atomicity", test_atomicity),
        ("RepositoryFactory", test_repository_factory),
    ]
    
    print("\n" + "#"*60)
    print("# FASE 3 - Repository/DAL Validation Tests")
    print("#"*60)
    
    passed = 0
    failed = 0
    
    for test_name, test_func in tests:
        try:
            result = test_func()
            if result:
                passed += 1
        except AssertionError as e:
            print(f"\n✗ TEST FAILED: {test_name}")
            print(f"  Error: {e}")
            failed += 1
        except Exception as e:
            print(f"\n✗ TEST ERROR: {test_name}")
            print(f"  Unexpected error: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    
    print("\n" + "#"*60)
    print(f"# Test Summary: {passed} passed, {failed} failed")
    print("#"*60)
    
    if failed == 0:
        print("\n✓ ALL TESTS PASSED - FASE 3 COMPLETE")
        return 0
    else:
        print(f"\n✗ {failed} TESTS FAILED - REVIEW REQUIRED")
        return 1


if __name__ == "__main__":
    sys.exit(run_all_tests())
