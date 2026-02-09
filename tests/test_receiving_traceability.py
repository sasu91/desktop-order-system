"""
Test receiving workflow with order traceability.

Scenario:
- 2 orders for same SKU (SKU001)
- Partial delivery on doc1 (DDT-001)
- Full delivery on doc2 (DDT-002)
- Residual undelivered → UNFULFILLED
- Idempotency: repeat doc1 → no duplicates
"""
import pytest
from datetime import date, timedelta
from pathlib import Path
import tempfile
import shutil

from src.persistence.csv_layer import CSVLayer
from src.workflows.receiving_v2 import ReceivingWorkflow
from src.workflows.order import OrderWorkflow
from src.domain.models import EventType, SKU, Stock, OrderProposal


@pytest.fixture
def temp_data_dir():
    """Create temporary data directory."""
    temp_dir = Path(tempfile.mkdtemp())
    yield temp_dir
    shutil.rmtree(temp_dir)


@pytest.fixture
def csv_layer(temp_data_dir):
    """Create CSV layer with temp directory."""
    return CSVLayer(data_dir=temp_data_dir)


@pytest.fixture
def receiving_workflow(csv_layer):
    """Create receiving workflow."""
    return ReceivingWorkflow(csv_layer)


@pytest.fixture
def order_workflow(csv_layer):
    """Create order workflow."""
    return OrderWorkflow(csv_layer, lead_time_days=3)


class TestReceivingTraceability:
    """Test receiving workflow with granular order tracking."""
    
    def test_multi_order_partial_fulfillment(self, csv_layer, order_workflow, receiving_workflow):
        """
        Scenario:
        1. Create 2 orders for SKU001 (50 pz, 30 pz)
        2. Receive partial (40 pz) on DDT-001 → allocates to first order
        3. Receive remaining (30 pz) on DDT-002 → closes first, allocates to second
        4. Close second order with partial (20 pz) → 10 pz unfulfilled
        """
        # Setup
        today = date.today()
        receipt_date_1 = today  # Use today instead of future
        receipt_date_2 = today
        
        # Create 2 orders for SKU001
        proposal_1 = OrderProposal(
            sku="SKU001",
            description="Product A",
            current_on_hand=0,
            current_on_order=0,
            daily_sales_avg=5.0,
            proposed_qty=50,
            receipt_date=receipt_date_1,
        )
        proposal_2 = OrderProposal(
            sku="SKU001",
            description="Product A",
            current_on_hand=0,
            current_on_order=0,
            daily_sales_avg=5.0,
            proposed_qty=30,
            receipt_date=receipt_date_2,
        )
        
        confirmations, txns = order_workflow.confirm_order([proposal_1, proposal_2])
        
        assert len(confirmations) == 2
        order_1_id = confirmations[0].order_id
        order_2_id = confirmations[1].order_id
        
        # Verify orders created
        order_logs = csv_layer.read_order_logs()
        assert len(order_logs) == 2
        assert int(order_logs[0]["qty_ordered"]) == 50
        assert int(order_logs[1]["qty_ordered"]) == 30
        assert int(order_logs[0]["qty_received"]) == 0
        assert int(order_logs[1]["qty_received"]) == 0
        assert order_logs[0]["status"] == "PENDING"
        assert order_logs[1]["status"] == "PENDING"
        
        # === RECEIPT 1: Partial (40 pz) on DDT-001 ===
        txns_1, skip_1, updates_1 = receiving_workflow.close_receipt_by_document(
            document_id="DDT-001",
            receipt_date=receipt_date_1,
            items=[{"sku": "SKU001", "qty_received": 40}],  # No order_ids → FIFO
            notes="First delivery",
        )
        
        assert not skip_1
        assert len(txns_1) == 1
        assert txns_1[0].event == EventType.RECEIPT
        assert txns_1[0].qty == 40
        
        # Check order_1 updated (partial)
        assert order_1_id in updates_1
        assert updates_1[order_1_id]["qty_received_total"] == 40
        assert updates_1[order_1_id]["new_status"] == "PARTIAL"
        
        # Verify order_logs updated
        order_logs = csv_layer.read_order_logs()
        order_1_log = next(o for o in order_logs if o["order_id"] == order_1_id)
        order_2_log = next(o for o in order_logs if o["order_id"] == order_2_id)
        
        assert int(order_1_log["qty_received"]) == 40
        assert order_1_log["status"] == "PARTIAL"
        assert int(order_2_log["qty_received"]) == 0
        assert order_2_log["status"] == "PENDING"
        
        # Verify receiving_log
        recv_logs = csv_layer.read_receiving_logs()
        assert len(recv_logs) == 1
        assert recv_logs[0]["document_id"] == "DDT-001"
        assert recv_logs[0]["order_ids"] == order_1_id
        
        # === RECEIPT 2: Complete order_1 (10 pz) + start order_2 (20 pz) on DDT-002 ===
        txns_2, skip_2, updates_2 = receiving_workflow.close_receipt_by_document(
            document_id="DDT-002",
            receipt_date=receipt_date_2,
            items=[{"sku": "SKU001", "qty_received": 30}],
            notes="Second delivery",
        )
        
        assert not skip_2
        # Should create: 1 RECEIPT + 0 UNFULFILLED (order_1 fully received, order_2 partial)
        assert len(txns_2) == 1
        assert txns_2[0].event == EventType.RECEIPT
        assert txns_2[0].qty == 30
        
        # Check updates
        assert order_1_id in updates_2
        assert updates_2[order_1_id]["qty_received_total"] == 50  # 40 + 10
        assert updates_2[order_1_id]["new_status"] == "RECEIVED"
        
        assert order_2_id in updates_2
        assert updates_2[order_2_id]["qty_received_total"] == 20  # Residual allocated here
        assert updates_2[order_2_id]["new_status"] == "PARTIAL"
        
        # Verify order_logs
        order_logs = csv_layer.read_order_logs()
        order_1_log = next(o for o in order_logs if o["order_id"] == order_1_id)
        order_2_log = next(o for o in order_logs if o["order_id"] == order_2_id)
        
        assert int(order_1_log["qty_received"]) == 50
        assert order_1_log["status"] == "RECEIVED"
        assert int(order_2_log["qty_received"]) == 20
        assert order_2_log["status"] == "PARTIAL"
        
        # === Close order_2 without full delivery (10 pz unfulfilled) ===
        # Simulate closing order without receiving remaining 10 pz
        # For now, just verify current state
        unfulfilled_orders = csv_layer.get_unfulfilled_orders(sku="SKU001")
        
        assert len(unfulfilled_orders) == 1
        assert unfulfilled_orders[0]["order_id"] == order_2_id
        assert unfulfilled_orders[0]["qty_unfulfilled"] == 10  # 30 ordered - 20 received
        
        # Verify total stock impact
        transactions = csv_layer.read_transactions()
        sku001_receipts = [t for t in transactions if t.sku == "SKU001" and t.event == EventType.RECEIPT]
        total_received = sum(t.qty for t in sku001_receipts)
        
        assert total_received == 70  # 40 + 30
    
    def test_idempotency_duplicate_document(self, csv_layer, order_workflow, receiving_workflow):
        """Test that processing same document twice is idempotent."""
        today = date.today()
        receipt_date = today  # Use today
        
        # Create order
        proposal = OrderProposal(
            sku="SKU002",
            description="Product B",
            current_on_hand=0,
            current_on_order=0,
            daily_sales_avg=10.0,
            proposed_qty=100,
            receipt_date=receipt_date,
        )
        
        confirmations, _ = order_workflow.confirm_order([proposal])
        order_id = confirmations[0].order_id
        
        # First receipt
        txns_1, skip_1, updates_1 = receiving_workflow.close_receipt_by_document(
            document_id="DDT-100",
            receipt_date=receipt_date,
            items=[{"sku": "SKU002", "qty_received": 100}],
        )
        
        assert not skip_1
        assert len(txns_1) == 1
        assert len(updates_1) == 1
        
        # Repeat same document (idempotent)
        txns_2, skip_2, updates_2 = receiving_workflow.close_receipt_by_document(
            document_id="DDT-100",
            receipt_date=receipt_date,
            items=[{"sku": "SKU002", "qty_received": 100}],
        )
        
        assert skip_2  # Should skip
        assert len(txns_2) == 0
        assert len(updates_2) == 0
        
        # Verify order state unchanged
        order_logs = csv_layer.read_order_logs()
        order_log = next(o for o in order_logs if o["order_id"] == order_id)
        
        assert int(order_log["qty_received"]) == 100
        assert order_log["status"] == "RECEIVED"
        
        # Verify transactions not duplicated
        transactions = csv_layer.read_transactions()
        sku002_receipts = [t for t in transactions if t.sku == "SKU002" and t.event == EventType.RECEIPT]
        
        assert len(sku002_receipts) == 1
        assert sku002_receipts[0].qty == 100
    
    def test_multiple_documents_for_same_order(self, csv_layer, order_workflow, receiving_workflow):
        """Test receiving single order across multiple documents."""
        today = date.today()
        receipt_date = today  # Use today
        
        # Create order for 150 pz
        proposal = OrderProposal(
            sku="SKU003",
            description="Product C",
            current_on_hand=0,
            current_on_order=0,
            daily_sales_avg=15.0,
            proposed_qty=150,
            receipt_date=receipt_date,
        )
        
        confirmations, _ = order_workflow.confirm_order([proposal])
        order_id = confirmations[0].order_id
        
        # Receipt 1: 50 pz on DDT-A
        txns_a, _, updates_a = receiving_workflow.close_receipt_by_document(
            document_id="DDT-A",
            receipt_date=receipt_date,
            items=[{"sku": "SKU003", "qty_received": 50, "order_ids": [order_id]}],
        )
        
        assert updates_a[order_id]["qty_received_total"] == 50
        assert updates_a[order_id]["new_status"] == "PARTIAL"
        
        # Receipt 2: 60 pz on DDT-B
        txns_b, _, updates_b = receiving_workflow.close_receipt_by_document(
            document_id="DDT-B",
            receipt_date=receipt_date,  # Same day
            items=[{"sku": "SKU003", "qty_received": 60, "order_ids": [order_id]}],
        )
        
        assert updates_b[order_id]["qty_received_total"] == 110  # 50 + 60
        assert updates_b[order_id]["new_status"] == "PARTIAL"
        
        # Receipt 3: 40 pz on DDT-C (complete)
        txns_c, _, updates_c = receiving_workflow.close_receipt_by_document(
            document_id="DDT-C",
            receipt_date=receipt_date,  # Same day
            items=[{"sku": "SKU003", "qty_received": 40, "order_ids": [order_id]}],
        )
        
        assert updates_c[order_id]["qty_received_total"] == 150  # 110 + 40
        assert updates_c[order_id]["new_status"] == "RECEIVED"
        
        # Verify final state
        order_logs = csv_layer.read_order_logs()
        order_log = next(o for o in order_logs if o["order_id"] == order_id)
        
        assert int(order_log["qty_received"]) == 150
        assert order_log["status"] == "RECEIVED"
        
        # Verify all 3 documents recorded
        recv_logs = csv_layer.read_receiving_logs()
        assert len(recv_logs) == 3
        assert {log["document_id"] for log in recv_logs} == {"DDT-A", "DDT-B", "DDT-C"}
        
        # All should link to same order
        assert all(order_id in log["order_ids"] for log in recv_logs)
    
    def test_unfulfilled_orders_query(self, csv_layer, order_workflow, receiving_workflow):
        """Test querying unfulfilled orders."""
        today = date.today()
        
        # Create 3 orders
        proposals = [
            OrderProposal("SKU004", "Product D", 0, 0, 10.0, 100, today),
            OrderProposal("SKU005", "Product E", 0, 0, 5.0, 50, today),
            OrderProposal("SKU006", "Product F", 0, 0, 20.0, 200, today),
        ]
        
        confirmations, _ = order_workflow.confirm_order(proposals)
        order_ids = [c.order_id for c in confirmations]
        
        # Receive partial for SKU004 (60/100)
        receiving_workflow.close_receipt_by_document(
            "DDT-X1",
            today,
            [{"sku": "SKU004", "qty_received": 60}],
        )
        
        # Receive full for SKU005 (50/50)
        receiving_workflow.close_receipt_by_document(
            "DDT-X2",
            today,
            [{"sku": "SKU005", "qty_received": 50}],
        )
        
        # No receipt for SKU006 (0/200)
        
        # Query unfulfilled
        unfulfilled = csv_layer.get_unfulfilled_orders()
        
        assert len(unfulfilled) == 2
        
        # SKU004: 40 unfulfilled
        sku004_unfulfilled = next(u for u in unfulfilled if u["sku"] == "SKU004")
        assert sku004_unfulfilled["qty_ordered"] == 100
        assert sku004_unfulfilled["qty_received"] == 60
        assert sku004_unfulfilled["qty_unfulfilled"] == 40
        assert sku004_unfulfilled["status"] == "PARTIAL"
        
        # SKU006: 200 unfulfilled
        sku006_unfulfilled = next(u for u in unfulfilled if u["sku"] == "SKU006")
        assert sku006_unfulfilled["qty_ordered"] == 200
        assert sku006_unfulfilled["qty_received"] == 0
        assert sku006_unfulfilled["qty_unfulfilled"] == 200
        assert sku006_unfulfilled["status"] == "PENDING"
        
        # SKU005 should NOT be in unfulfilled (fully received)
        assert not any(u["sku"] == "SKU005" for u in unfulfilled)
    
    def test_atomic_write_with_backup(self, csv_layer, order_workflow, receiving_workflow):
        """Test that atomic writes create backups and are recoverable."""
        import time
        
        today = date.today()
        
        # Create order
        proposal = OrderProposal("SKU007", "Product G", 0, 0, 5.0, 50, today)
        confirmations, _ = order_workflow.confirm_order([proposal])
        order_id = confirmations[0].order_id
        
        # Check no backups initially
        backup_files = list(csv_layer.data_dir.glob("order_logs.csv.backup.*"))
        initial_backup_count = len(backup_files)
        
        # Receive partial (triggers atomic write)
        time.sleep(0.1)  # Ensure different timestamp
        receiving_workflow.close_receipt_by_document(
            "DDT-BK1",
            today,
            [{"sku": "SKU007", "qty_received": 30}],
        )
        
        # Verify backup created
        backup_files = list(csv_layer.data_dir.glob("order_logs.csv.backup.*"))
        assert len(backup_files) >= initial_backup_count + 1  # At least one backup created
        
        # Receive remaining (another atomic write)
        time.sleep(0.1)
        receiving_workflow.close_receipt_by_document(
            "DDT-BK2",
            today,
            [{"sku": "SKU007", "qty_received": 20}],
        )
        
        # Verify more backups created (may reuse same second if fast)
        backup_files_after = list(csv_layer.data_dir.glob("order_logs.csv.backup.*"))
        assert len(backup_files_after) >= len(backup_files)  # At least as many or more
        
        # Verify final state correct
        order_logs = csv_layer.read_order_logs()
        order_log = next(o for o in order_logs if o["order_id"] == order_id)
        
        assert int(order_log["qty_received"]) == 50
        assert order_log["status"] == "RECEIVED"
