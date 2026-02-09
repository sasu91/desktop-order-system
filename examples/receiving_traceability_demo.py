"""
Demo: Enhanced receiving workflow with order traceability.

Demonstrates:
- Multiple orders for same SKU
- Partial deliveries across multiple documents
- Order status tracking (PENDING → PARTIAL → RECEIVED)
- Idempotent document processing
- Unfulfilled order queries
"""
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import date, timedelta
from pathlib import Path
import tempfile
import shutil

from src.persistence.csv_layer import CSVLayer
from src.workflows.receiving_v2 import ReceivingWorkflow
from src.workflows.order import OrderWorkflow
from src.domain.models import OrderProposal


def main():
    # Setup temporary environment
    temp_dir = Path(tempfile.mkdtemp())
    print(f"📁 Using temp directory: {temp_dir}\n")
    
    try:
        csv_layer = CSVLayer(data_dir=temp_dir)
        order_workflow = OrderWorkflow(csv_layer, lead_time_days=3)
        receiving_workflow = ReceivingWorkflow(csv_layer)
        
        today = date.today()
        
        # ===== SCENARIO: 2 orders for WIDGET-A =====
        print("=" * 60)
        print("SCENARIO: Multiple orders for same SKU")
        print("=" * 60)
        
        # Create 2 orders for WIDGET-A
        print("\n📦 Creating 2 orders for WIDGET-A...")
        proposals = [
            OrderProposal(
                sku="WIDGET-A",
                description="Premium Widget",
                current_on_hand=0,
                current_on_order=0,
                daily_sales_avg=10.0,
                proposed_qty=100,
                receipt_date=today + timedelta(days=3),
            ),
            OrderProposal(
                sku="WIDGET-A",
                description="Premium Widget",
                current_on_hand=0,
                current_on_order=0,
                daily_sales_avg=10.0,
                proposed_qty=50,
                receipt_date=today + timedelta(days=5),
            ),
        ]
        
        confirmations, _ = order_workflow.confirm_order(proposals)
        order_1_id = confirmations[0].order_id
        order_2_id = confirmations[1].order_id
        
        print(f"   ✅ Order 1: {order_1_id} (100 pz)")
        print(f"   ✅ Order 2: {order_2_id} (50 pz)")
        
        # Show initial state
        print("\n📊 Initial order status:")
        for order in csv_layer.read_order_logs():
            print(f"   {order['order_id']}: {order['qty_received']}/{order['qty_ordered']} ({order['status']})")
        
        # ===== DELIVERY 1: Partial (70 pz) on DDT-2026-001 =====
        print("\n🚚 Delivery 1: DDT-2026-001 (70 pz)")
        txns, skip, updates = receiving_workflow.close_receipt_by_document(
            document_id="DDT-2026-001",
            receipt_date=today,  # Use today to avoid future date error
            items=[{"sku": "WIDGET-A", "qty_received": 70}],  # FIFO: allocates to order 1
            notes="First shipment",
        )
        
        print(f"   Transactions created: {len(txns)}")
        print(f"   Orders updated:")
        for order_id, update in updates.items():
            print(f"      {order_id}: {update['qty_received_total']}/{update['qty_ordered']} → {update['new_status']}")
        
        # ===== DELIVERY 2: Complete order 1 + start order 2 (50 pz) on DDT-2026-002 =====
        print("\n🚚 Delivery 2: DDT-2026-002 (50 pz)")
        txns, skip, updates = receiving_workflow.close_receipt_by_document(
            document_id="DDT-2026-002",
            receipt_date=today,
            items=[{"sku": "WIDGET-A", "qty_received": 50}],
            notes="Second shipment",
        )
        
        print(f"   Transactions created: {len(txns)}")
        print(f"   Orders updated:")
        for order_id, update in updates.items():
            print(f"      {order_id}: {update['qty_received_total']}/{update['qty_ordered']} → {update['new_status']}")
        
        # ===== IDEMPOTENCY TEST: Repeat DDT-2026-001 =====
        print("\n🔁 Idempotency test: Re-process DDT-2026-001...")
        txns, skip, updates = receiving_workflow.close_receipt_by_document(
            document_id="DDT-2026-001",
            receipt_date=today,
            items=[{"sku": "WIDGET-A", "qty_received": 70}],
        )
        
        print(f"   Skipped: {skip} (expected True)")
        print(f"   Transactions created: {len(txns)} (expected 0)")
        
        # ===== PARTIAL ORDER 2: Close with unfulfilled (30 pz only) =====
        print("\n🚚 Delivery 3: DDT-2026-003 (30 pz) - Partial fulfillment")
        txns, skip, updates = receiving_workflow.close_receipt_by_document(
            document_id="DDT-2026-003",
            receipt_date=today,
            items=[{"sku": "WIDGET-A", "qty_received": 30, "order_ids": [order_2_id]}],
            notes="Final partial delivery",
        )
        
        print(f"   Transactions created: {len(txns)}")
        print(f"   Orders updated:")
        for order_id, update in updates.items():
            print(f"      {order_id}: {update['qty_received_total']}/{update['qty_ordered']} → {update['new_status']}")
        
        # ===== QUERY UNFULFILLED ORDERS =====
        print("\n📋 Unfulfilled orders:")
        unfulfilled = csv_layer.get_unfulfilled_orders()
        
        if unfulfilled:
            for order in unfulfilled:
                print(f"   🔴 {order['order_id']} ({order['sku']}): "
                      f"{order['qty_unfulfilled']} pz missing "
                      f"({order['qty_received']}/{order['qty_ordered']} received)")
        else:
            print("   ✅ All orders fully received")
        
        # ===== FINAL STATE =====
        print("\n📊 Final order status:")
        for order in csv_layer.read_order_logs():
            status_icon = "✅" if order['status'] == "RECEIVED" else "⏳" if order['status'] == "PARTIAL" else "⚠️"
            print(f"   {status_icon} {order['order_id']}: "
                  f"{order['qty_received']}/{order['qty_ordered']} ({order['status']})")
        
        # ===== RECEIVING LOGS =====
        print("\n📜 Receiving logs (documents processed):")
        for log in csv_layer.read_receiving_logs():
            print(f"   📄 {log['document_id']}: {log['qty_received']} pz {log['sku']} "
                  f"(orders: {log['order_ids']})")
        
        # ===== BACKUP FILES =====
        print("\n💾 Backup files created:")
        backups = list(temp_dir.glob("*.backup.*"))
        print(f"   {len(backups)} backup file(s)")
        for backup in backups[:3]:  # Show first 3
            print(f"      {backup.name}")
        
        print("\n" + "=" * 60)
        print("✅ Demo completed successfully!")
        print("=" * 60)
        
    finally:
        # Cleanup
        print(f"\n🧹 Cleaning up {temp_dir}...")
        shutil.rmtree(temp_dir)


if __name__ == "__main__":
    main()
