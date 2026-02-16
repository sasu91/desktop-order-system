"""
Test integrazione event uplift nella proposta ordine (sganciato da promo_adjustment).
"""
from datetime import date, timedelta
import tempfile
import shutil
import os
from pathlib import Path

from src.domain.models import SKU, Stock, SalesRecord, EventUpliftRule
from src.persistence.csv_layer import CSVLayer
from src.workflows.order import OrderWorkflow

# Create temporary directory for CSV files
test_dir = Path(tempfile.mkdtemp())
print(f"Directory temporanea test: {test_dir}")

try:
    # Initialize CSV layer
    csv_layer = CSVLayer(test_dir)
    
    # Create test SKU
    test_sku = SKU(
        sku="SKU001",
        description="Test Product",
        pack_size=6,
        moq=6,
        lead_time_days=2,
        review_period=7,
        shelf_life_days=0,
        department="DEPT_A",
        category="CAT_1",
        reorder_point=50,
        safety_stock=20,
        max_stock=500,
    )
    csv_layer.write_sku(test_sku)
    
    # Create sales history
    sales_records = []
    for i in range(60):
        sales_date = date.today() - timedelta(days=i)
        sales_records.append(SalesRecord(
            date=sales_date,
            sku="SKU001",
            qty_sold=15.0,  # ~15 pz/day
        ))
    
    for sales in sales_records:
        csv_layer.write_sales_record(sales)
    
    # Target receipt date (3 days from now)
    target_receipt = date.today() + timedelta(days=3)
    
    # Create event uplift rule for target receipt date with strength=1.0
    event_rule = EventUpliftRule(
        delivery_date=target_receipt,
        reason="holiday",
        strength=1.0,  # Full strength
        scope_type="ALL",
        scope_key="",
        notes="Test event - full uplift",
    )
    csv_layer.write_event_uplift_rule(event_rule)
    
    # Read and update settings: PROMO DISABLED, EVENT UPLIFT ENABLED
    settings = csv_layer.read_settings()
    settings["promo_adjustment"]["enabled"]["value"] = False  # PROMO DISABILITATO
    settings["event_uplift"]["enabled"]["value"] = True  # EVENT UPLIFT ABILITATO
    csv_layer.write_settings(settings)
    
    # Generate order proposal
    workflow = OrderWorkflow(csv_layer)
    
    current_stock = Stock(
        sku="SKU001",
        on_hand=100,
        on_order=0,
        unfulfilled_qty=0,
        asof_date=date.today(),
    )
    
    print("\n=== GENERAZIONE PROPOSTA ORDINE ===")
    print(f"Promo adjustment: DISABILITATO")
    print(f"Event uplift: ABILITATO")
    print(f"Target receipt date: {target_receipt}")
    print(f"Event rule: delivery_date={target_receipt}, strength=1.0")
    print()
    
    proposal = workflow.generate_proposal(
        sku="SKU001",
        description="Test Product",
        current_stock=current_stock,
        daily_sales_avg=15.0,
        sku_obj=test_sku,
        target_receipt_date=target_receipt,
        protection_period_days=9,  # lead_time + review_period
        sales_records=sales_records,
    )
    
    print("=== RISULTATI ===")
    print(f"Baseline forecast qty: {proposal.baseline_forecast_qty}")
    print(f"Event uplift active: {proposal.event_uplift_active}")
    print(f"Event m_i (moltiplicatore): {proposal.event_m_i:.3f}")
    print(f"Event reason: {proposal.event_reason}")
    print(f"Event explain: {proposal.event_explain_short}")
    print(f"Forecast qty (dopo event): {proposal.forecast_qty}")
    print(f"Proposed qty: {proposal.proposed_qty}")
    print()
    
    if proposal.event_uplift_active:
        uplift_pct = (proposal.event_m_i - 1.0) * 100
        print(f"✓ SUCCESSO: Event uplift attivo con m_i={proposal.event_m_i:.3f} (+{uplift_pct:.1f}%)")
        print(f"✓ Event uplift funziona INDIPENDENTEMENTE da promo_adjustment")
    else:
        print("✗ ERRORE: Event uplift NON attivo (dovrebbe essere attivo)")
    
    # Cleanup
    print(f"\nPulizia directory temporanea: {test_dir}")
    
except Exception as e:
    print(f"\n✗ ERRORE durante il test: {e}")
    import traceback
    traceback.print_exc()

finally:
    # Cleanup temporary directory
    if os.path.exists(test_dir):
        shutil.rmtree(test_dir)
        print("Directory temporanea eliminata")

print("\nTest completato!")
