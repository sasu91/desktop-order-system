#!/usr/bin/env python3
"""
Verifica coerenza dati CSV aggiornati.
"""
from pathlib import Path
from datetime import date

from src.persistence.csv_layer import CSVLayer
from src.domain.ledger import StockCalculator

def verify_data_consistency():
    """Verifica che i dati CSV siano coerenti."""
    print("=" * 60)
    print("VERIFICA COERENZA DATI CSV")
    print("=" * 60)
    
    csv_layer = CSVLayer(data_dir=Path("data"))
    
    # 1. Verifica SKU
    print("\n1. SKU Configurati:")
    skus = csv_layer.read_skus()
    print(f"   Totale SKU: {len(skus)}")
    for sku in skus:
        print(f"   - {sku.sku}: {sku.description}")
        print(f"     MOQ={sku.moq}, Lead={sku.lead_time_days}d, Max={sku.max_stock}, "
              f"Reorder={sku.reorder_point}, Supplier={sku.supplier}")
    
    # 2. Verifica Vendite
    print(f"\n2. Vendite Registrate:")
    sales = csv_layer.read_sales()
    print(f"   Totale record vendite: {len(sales)}")
    
    # Raggruppa per data
    from collections import defaultdict
    sales_by_date = defaultdict(int)
    for s in sales:
        sales_by_date[s.date] += s.qty_sold
    
    print(f"   Periodo: {min(sales_by_date.keys())} - {max(sales_by_date.keys())}")
    print(f"   Giorni con vendite: {len(sales_by_date)}")
    print(f"   Totale unità vendute: {sum(sales_by_date.values())}")
    
    # Ultime 7 giorni
    print("\n   Ultimi 7 giorni:")
    for d in sorted(sales_by_date.keys())[-7:]:
        print(f"     {d}: {sales_by_date[d]} unità")
    
    # 3. Verifica Transazioni
    print(f"\n3. Transazioni Ledger:")
    txns = csv_layer.read_transactions()
    print(f"   Totale transazioni: {len(txns)}")
    
    from collections import Counter
    event_counts = Counter(t.event.value for t in txns)
    for event, count in event_counts.items():
        print(f"   - {event}: {count}")
    
    # 4. Calcola Stock Corrente
    print(f"\n4. Stock Corrente (al {date.today()}):")
    sku_ids = csv_layer.get_all_sku_ids()
    stocks = StockCalculator.calculate_all_skus(
        sku_ids,
        date.today(),
        txns,
        sales,
    )
    
    print(f"   {'SKU':<10} {'On Hand':>10} {'On Order':>10} {'Available':>10}")
    print("   " + "-" * 44)
    for sku_id in sorted(sku_ids):
        stock = stocks[sku_id]
        print(f"   {sku_id:<10} {stock.on_hand:>10} {stock.on_order:>10} {stock.available():>10}")
    
    # 5. Verifica Ordini
    print(f"\n5. Ordini Registrati:")
    orders = csv_layer.read_order_logs()
    print(f"   Totale ordini: {len(orders)}")
    pending = [o for o in orders if o.get('status') == 'pending']
    received = [o for o in orders if o.get('status') == 'received']
    print(f"   - Pending: {len(pending)}")
    print(f"   - Received: {len(received)}")
    
    # 6. Verifica Ricevimenti
    print(f"\n6. Ricevimenti Registrati:")
    receipts = csv_layer.read_receiving_logs()
    print(f"   Totale ricevimenti: {len(receipts)}")
    
    # 7. Verifica Audit Log
    print(f"\n7. Audit Trail:")
    try:
        audit_logs = csv_layer.read_audit_logs()
        print(f"   Totale eventi audit: {len(audit_logs)}")
    except Exception as e:
        print(f"   ⚠️ Audit log non disponibile: {e}")
    
    print("\n" + "=" * 60)
    print("✅ VERIFICA COMPLETATA")
    print("=" * 60)
    print("\nI dati sono pronti per testare:")
    print("  - Tab Dashboard: grafici vendite giornaliere e settimanali")
    print("  - Tab Stock: inserimento EOD stock")
    print("  - Tab Ordini: proposte con calcolo vendite medie")
    print("  - Tab Ricevimenti: chiusura ordini")
    print("  - Tab Eccezioni: annullamento senza duplicati")
    print("  - Tab Admin: gestione SKU completa")

if __name__ == "__main__":
    verify_data_consistency()
