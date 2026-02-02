"""Test delle modifiche: ADJUST set assoluto, receipt_date come data evento, status uppercase"""
import sys
from pathlib import Path
from datetime import date

sys.path.insert(0, '/workspaces/desktop-order-system')

from src.persistence.csv_layer import CSVLayer
from src.domain.ledger import StockCalculator
from src.domain.models import Transaction, EventType, Stock
from src.workflows.receiving import ReceivingWorkflow

print("=" * 70)
print("TEST 1: ADJUST è set assoluto (non delta)")
print("=" * 70)

# Scenario: SKU con on_hand=100, applica ADJUST con qty=50
transactions = [
    Transaction(date=date(2026, 1, 1), sku="TEST001", event=EventType.SNAPSHOT, qty=100),
    Transaction(date=date(2026, 1, 10), sku="TEST001", event=EventType.ADJUST, qty=50),
]

stock = StockCalculator.calculate_asof(
    sku="TEST001",
    asof_date=date(2026, 1, 15),
    transactions=transactions,
)

print(f"Snapshot iniziale: on_hand=100")
print(f"ADJUST con qty=50")
print(f"Risultato atteso: on_hand=50 (set assoluto)")
print(f"Risultato effettivo: on_hand={stock.on_hand}")
assert stock.on_hand == 50, f"ERRORE: ADJUST non funziona come set assoluto! on_hand={stock.on_hand}"
print("✓ Test ADJUST set assoluto: PASSATO\n")

print("=" * 70)
print("TEST 2: RECEIPT usa receipt_date come data evento")
print("=" * 70)

csv_layer = CSVLayer(data_dir=Path("/workspaces/desktop-order-system/data"))
workflow = ReceivingWorkflow(csv_layer)

# Simula chiusura ricevimento con receipt_date nel passato
receipt_date_past = date(2026, 1, 20)
today = date.today()

print(f"Receipt_date: {receipt_date_past}")
print(f"Today: {today}")

# Crea transazioni per un ricevimento fittizio
from unittest.mock import MagicMock
workflow.csv_layer = MagicMock()
workflow.csv_layer.read_receiving_logs.return_value = []
workflow.csv_layer.read_order_logs.return_value = [
    {"sku": "SKU001", "qty_ordered": "100", "status": "PENDING"}
]
workflow.csv_layer.write_transactions_batch = MagicMock()
workflow.csv_layer.write_receiving_log = MagicMock()

transactions_created, already_processed = workflow.close_receipt(
    receipt_id="TEST_RECEIPT",
    receipt_date=receipt_date_past,
    sku_quantities={"SKU001": 80},
    notes="Test receipt"
)

print(f"\nTransazioni create: {len(transactions_created)}")
for txn in transactions_created:
    print(f"  - {txn.event.value}: date={txn.date}, qty={txn.qty}")
    if txn.event == EventType.RECEIPT:
        assert txn.date == receipt_date_past, f"ERRORE: RECEIPT.date={txn.date}, atteso={receipt_date_past}"
        print(f"    ✓ RECEIPT usa receipt_date come data evento")
    elif txn.event == EventType.UNFULFILLED:
        assert txn.date == receipt_date_past, f"ERRORE: UNFULFILLED.date={txn.date}, atteso={receipt_date_past}"
        print(f"    ✓ UNFULFILLED usa receipt_date come data evento")

print("\n✓ Test receipt_date: PASSATO")

print("\n" + "=" * 70)
print("TEST 3: Status PENDING uppercase coerente")
print("=" * 70)

# Verifica che il filtro in receiving.py usi PENDING uppercase
import inspect
source = inspect.getsource(ReceivingWorkflow.close_receipt)
assert 'status == "PENDING"' in source, "ERRORE: Filtro status non usa PENDING uppercase"
print("✓ ReceivingWorkflow.close_receipt filtra status == 'PENDING' (uppercase)")

# Verifica modelli
from src.domain.models import EventType
print(f"✓ EventType.ADJUST definito come: {EventType.ADJUST.value}")

print("\n" + "=" * 70)
print("TUTTI I TEST COMPLETATI CON SUCCESSO!")
print("=" * 70)
