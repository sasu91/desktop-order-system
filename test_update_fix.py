"""Test fix per update_sku - verifica che tutti gli SKU siano preservati dopo un update"""
import sys
sys.path.insert(0, '/workspaces/desktop-order-system/src')

from persistence.csv_layer import CSVLayer
from domain.models import DemandVariability

# Inizializza CSV layer
csv_layer = CSVLayer(data_dir="/workspaces/desktop-order-system/data")

# Leggi tutti gli SKU prima dell'update
print("SKU prima dell'update:")
skus_before = csv_layer.read_skus()
print(f"Numero totale SKU: {len(skus_before)}")
for sku in skus_before:
    print(f"  {sku.sku}: {sku.description}")

# Aggiorna uno SKU (SKU001)
print("\nAggiornamento SKU001...")
success = csv_layer.update_sku(
    old_sku_id="SKU001",
    new_sku_id="SKU001",
    new_description="Caffè Arabica 250g - AGGIORNATO",
    new_ean="8001234567890",
    moq=15,  # Modificato da 10 a 15
    pack_size=1,
    lead_time_days=7,
    review_period=7,
    safety_stock=0,
    shelf_life_days=0,
    max_stock=500,
    reorder_point=50,

    demand_variability=DemandVariability.STABLE
)

print(f"Update riuscito: {success}")

# Leggi tutti gli SKU dopo l'update
print("\nSKU dopo l'update:")
skus_after = csv_layer.read_skus()
print(f"Numero totale SKU: {len(skus_after)}")
for sku in skus_after:
    print(f"  {sku.sku}: {sku.description} (MOQ: {sku.moq})")

# Verifica
if len(skus_before) == len(skus_after):
    print("\n✓ SUCCESSO: Tutti gli SKU sono stati preservati!")
else:
    print(f"\n✗ ERRORE: Persi {len(skus_before) - len(skus_after)} SKU!")
