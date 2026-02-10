"""
Test Shelf Life Integration in OrderWorkflow (Fase 2)
Verifica che il calcolo usable stock e penalty siano applicati correttamente.
"""
import os
import sys
from datetime import date, timedelta
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.workflows.order import OrderWorkflow
from src.persistence.csv_layer import CSVLayer
from src.domain.models import SKU, DemandVariability, Lot

def setup_test_environment():
    """Crea ambiente di test con dati shelf life."""
    csv_layer = CSVLayer(Path("data"))
    
    # 1. Verifica esistenza SKU con shelf life configurato
    skus = csv_layer.read_skus()
    test_sku = None
    for sku in skus:
        if sku.sku == "TEST_SHELF_LIFE":
            test_sku = sku
            break
    
    if not test_sku:
        # Crea SKU di test con shelf life
        test_sku = SKU(
            sku="TEST_SHELF_LIFE",
            description="Test SKU with shelf life",
            ean="1234567890123",
            pack_size=10,
            moq=20,
            lead_time_days=7,
            shelf_life_days=60,
            min_shelf_life_days=14,  # Voglio almeno 14 giorni di shelf life
            waste_penalty_mode="soft",
            waste_penalty_factor=0.3,  # Penalty 30%
            waste_risk_threshold=20.0,  # Attiva penalty se waste risk > 20%
            demand_variability=DemandVariability.LOW
        )
        csv_layer.write_sku(test_sku)
        print(f"✅ Creato SKU di test: {test_sku.sku}")
    else:
        print(f"✅ SKU di test esistente: {test_sku.sku}")
    
    # 2. Crea lotti con diversi stati di shelf life
    #    Setup: min_shelf_life=14, waste_horizon=21 (da settings)
    #    - Lot con 30 giorni: usable, no risk (> waste_horizon)
    #    - Lot con 18 giorni: usable BUT expiring soon (14 < 18 <= 21) → WASTE RISK
    #    - Lot con 10 giorni: unusable (< min_shelf_life)
    #    - Lot con 5 giorni: unusable + expired soon
    today = date.today()
    lots = [
        # Lot 1: Usable, no waste risk (30 giorni > waste_horizon=21)
        Lot(
            lot_id="LOT_USABLE_001",
            sku="TEST_SHELF_LIFE",
            qty_on_hand=50,
            expiry_date=today + timedelta(days=30),
            receipt_id="RCV_001",
            receipt_date=today - timedelta(days=30),
        ),
        # Lot 2: Usable BUT expiring soon (18 giorni: 14 < 18 <= 21) → WASTE RISK
        Lot(
            lot_id="LOT_EXPIRING_SOON_001",
            sku="TEST_SHELF_LIFE",
            qty_on_hand=25,
            expiry_date=today + timedelta(days=18),
            receipt_id="RCV_002",
            receipt_date=today - timedelta(days=42),
        ),
        # Lot 3: Unusable (10 giorni < min_shelf_life=14)
        Lot(
            lot_id="LOT_UNUSABLE_001",
            sku="TEST_SHELF_LIFE",
            qty_on_hand=15,
            expiry_date=today + timedelta(days=10),
            receipt_id="RCV_003",
            receipt_date=today - timedelta(days=50),
        ),
        # Lot 4: Unusable + very close to expiry (5 giorni)
        Lot(
            lot_id="LOT_NEARLY_EXPIRED_001",
            sku="TEST_SHELF_LIFE",
            qty_on_hand=10,
            expiry_date=today + timedelta(days=5),
            receipt_id="RCV_004",
            receipt_date=today - timedelta(days=55),
        ),
    ]
    
    # Scrivi lots
    for lot in lots:
        csv_layer.write_lot(lot)
    print(f"✅ Creati {len(lots)} lotti di test")
    
    # 4. Configura waste_horizon=21 in settings per test (assicurati che 18 giorni sia nel range waste)
    settings = csv_layer.read_settings()
    if "shelf_life_policy" not in settings:
        settings["shelf_life_policy"] = {}
    if "waste_horizon_days" not in settings["shelf_life_policy"]:
        settings["shelf_life_policy"]["waste_horizon_days"] = {}
    settings["shelf_life_policy"]["waste_horizon_days"]["value"] = 21
    csv_layer.write_settings(settings)
    print(f"✅ Configurato waste_horizon_days=21 in settings")
    
    # 3. Aggiungi transazioni per avere stock disponibile
    from src.domain.models import Transaction, EventType
    
    # Crea uno SNAPSHOT iniziale per dare stock
    snapshot_tx = Transaction(
        date=today - timedelta(days=60),
        sku="TEST_SHELF_LIFE",
        event=EventType.SNAPSHOT,
        qty=100,  # 100 pezzi iniziali
        receipt_date=None,
        note="Initial stock for shelf life test"
    )
    csv_layer.write_transaction(snapshot_tx)
    print(f"✅ Creato SNAPSHOT iniziale (100 pz)")
    
    return csv_layer, test_sku

def test_shelf_life_integration():
    """Test principale: verifica integrazione shelf life in OrderWorkflow."""
    print("\n" + "="*80)
    print("TEST SHELF LIFE INTEGRATION - FASE 2")
    print("="*80 + "\n")
    
    csv_layer, test_sku = setup_test_environment()
    workflow = OrderWorkflow(csv_layer)
    
    # Verifica settings shelf life enabled
    settings = csv_layer.read_settings()
    shelf_life_enabled = settings.get("shelf_life_policy", {}).get("enabled", {}).get("value", True)
    print(f"📋 Shelf life enabled in settings: {shelf_life_enabled}")
    
    if not shelf_life_enabled:
        print("⚠️  SHELF LIFE DISABILITATO! Abilita in settings.json per testare.")
        return
    
    # Genera proposta di ordine
    print(f"\n📦 Generazione proposta ordine per SKU: {test_sku.sku}")
    print(f"   - Shelf life: {test_sku.shelf_life_days} giorni")
    print(f"   - Min shelf life: {test_sku.min_shelf_life_days} giorni")
    print(f"   - Waste penalty mode: {test_sku.waste_penalty_mode}")
    print(f"   - Waste penalty factor: {test_sku.waste_penalty_factor}")
    print(f"   - Waste risk threshold: {test_sku.waste_risk_threshold}%")
    
    # Calcola current stock
    from src.domain.ledger import StockCalculator
    today = date.today()
    transactions = csv_layer.read_transactions()
    current_stock = StockCalculator.calculate_asof(
        sku=test_sku.sku,
        asof_date=today + timedelta(days=1),
        transactions=transactions,
        sales_records=None,
    )
    
    proposal = workflow.generate_proposal(
        sku=test_sku.sku,
        description=test_sku.description,
        current_stock=current_stock,
        daily_sales_avg=10.0,  # Increased sales → create reorder need (S will be higher)
        sku_obj=test_sku,
    )
    
    print(f"\n📊 RISULTATI PROPOSTA:")
    print(f"   - Current on_hand: {proposal.current_on_hand}")
    print(f"   - Usable stock: {proposal.usable_stock}")
    print(f"   - Unusable stock: {proposal.unusable_stock}")
    print(f"   - Waste risk: {proposal.waste_risk_percent:.1f}%")
    print(f"   - Inventory Position: {proposal.inventory_position}")
    print(f"   - Proposed qty (BEFORE penalty): {proposal.proposed_qty_before_rounding}")
    print(f"   - Proposed qty (FINAL): {proposal.proposed_qty}")
    print(f"   - Shelf life penalty applied: {proposal.shelf_life_penalty_applied}")
    if proposal.shelf_life_penalty_message:
        print(f"   - Penalty message: {proposal.shelf_life_penalty_message}")
    print(f"\n   Notes: {proposal.notes}")
    
    # Verifiche
    print(f"\n🔍 VERIFICHE:")
    
    # 1. Usable stock should be less than total on_hand (because of min_shelf_life)
    assert proposal.usable_stock < proposal.current_on_hand, \
        f"FAIL: Usable stock ({proposal.usable_stock}) should be < total on_hand ({proposal.current_on_hand})"
    print(f"   ✅ Usable stock ({proposal.usable_stock}) < Total on_hand ({proposal.current_on_hand})")
    
    # 2. Unusable stock should be > 0 (we have lots below min_shelf_life)
    assert proposal.unusable_stock > 0, \
        f"FAIL: Unusable stock should be > 0 (lots below min_shelf_life)"
    print(f"   ✅ Unusable stock > 0: {proposal.unusable_stock}")
    
    # 3. Waste risk should be calculated
    assert proposal.waste_risk_percent > 0, \
        f"FAIL: Waste risk should be > 0 (we have expiring lots)"
    print(f"   ✅ Waste risk calculated: {proposal.waste_risk_percent:.1f}%")
    
    # 4. If waste_risk > threshold, penalty should be applied
    if proposal.waste_risk_percent >= test_sku.waste_risk_threshold:
        assert proposal.shelf_life_penalty_applied, \
            f"FAIL: Penalty should be applied (waste_risk {proposal.waste_risk_percent:.1f}% > threshold {test_sku.waste_risk_threshold}%)"
        print(f"   ✅ Penalty applied (waste risk {proposal.waste_risk_percent:.1f}% > threshold {test_sku.waste_risk_threshold}%)")
        
        assert proposal.shelf_life_penalty_message != "", \
            f"FAIL: Penalty message should be set"
        print(f"   ✅ Penalty message: {proposal.shelf_life_penalty_message}")
    else:
        print(f"   ℹ️  No penalty (waste risk {proposal.waste_risk_percent:.1f}% < threshold {test_sku.waste_risk_threshold}%)")
    
    # 5. Inventory Position should use usable_stock (not total on_hand)
    # IP = usable_stock + on_order - unfulfilled
    expected_ip = proposal.usable_stock + proposal.current_on_order - proposal.unfulfilled_qty
    assert proposal.inventory_position == expected_ip, \
        f"FAIL: IP should use usable stock. Expected {expected_ip}, got {proposal.inventory_position}"
    print(f"   ✅ IP calculated with usable stock: {proposal.inventory_position} = {proposal.usable_stock} + {proposal.current_on_order} - {proposal.unfulfilled_qty}")
    
    print(f"\n" + "="*80)
    print("✅ TUTTI I TEST PASSATI - INTEGRAZIONE SHELF LIFE OK!")
    print("="*80 + "\n")

if __name__ == "__main__":
    test_shelf_life_integration()
