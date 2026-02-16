"""
Test rapido per verificare integrazione strength in event uplift.
"""
from datetime import date, timedelta
from src.domain.models import SKU, EventUpliftRule, SalesRecord
from src.domain.event_uplift import apply_event_uplift_to_forecast

# Setup test data
sku_obj = SKU(
    sku="TEST001",
    description="Test SKU",
    pack_size=6,
    moq=6,
    lead_time_days=2,
    review_period=7,
    shelf_life_days=0,  # Non perishable
    department="DEPT_A",
    category="CAT_1",
)

# Sales history (simulated similar days)
delivery_date = date.today() + timedelta(days=3)
sales_records = []
for i in range(40):
    past_date = delivery_date - timedelta(days=i*7)  # Same weekday
    sales_records.append(SalesRecord(
        date=past_date,
        sku="TEST001",
        qty_sold=100.0 if i % 2 == 0 else 80.0,  # Variability for U estimation
    ))

# Event rule with strength=1.0 (full effect)
event_rule_full = EventUpliftRule(
    delivery_date=delivery_date,
    reason="holiday",
    strength=1.0,  # Full strength
    scope_type="ALL",
    scope_key="",
    notes="Natale - full strength test",
)

# Event rule with strength=0.5 (half effect)
event_rule_half = EventUpliftRule(
    delivery_date=delivery_date,
    reason="holiday",
    strength=0.5,  # Half strength
    scope_type="ALL",
    scope_key="",
    notes="Natale - half strength test",
)

# Build horizon
horizon_dates = [delivery_date + timedelta(days=i) for i in range(10)]
baseline_forecast = {d: 50.0 for d in horizon_dates}  # Constant baseline

# Settings (event uplift enabled)
settings = {
    "event_uplift": {
        "enabled": {"value": True},
        "default_quantile": {"value": 0.70},
        "min_factor": {"value": 1.0},
        "max_factor": {"value": 2.0},
        "perishables_policy_exclude_if_shelf_life_days_lte": {"value": 3},
        "perishables_policy_cap_extra_cover_days_per_sku": {"value": 1},
        "similar_days_seasonal_window": {"value": 30},
        "min_samples_u_estimation": {"value": 5},
        "min_samples_beta_estimation": {"value": 10},
        "beta_normalization_mode": {"value": "mean_one"},
    }
}

print("=== TEST 1: Event uplift con strength=1.0 (full) ===")
adjusted_fc_full, explain_full = apply_event_uplift_to_forecast(
    sku_obj=sku_obj,
    delivery_date=delivery_date,
    horizon_dates=horizon_dates,
    baseline_forecast=baseline_forecast,
    event_rules=[event_rule_full],
    all_skus=[sku_obj],
    sales_records=sales_records,
    settings=settings,
)

print(f"Rule matched: {explain_full.rule_matched is not None}")
print(f"U_store_day: {explain_full.u_store_day:.3f}")
print(f"beta_i: {explain_full.beta_i:.3f}")
print(f"m_i (moltiplicatore finale): {explain_full.m_i:.3f}")
print(f"Baseline total: {sum(baseline_forecast.values()):.0f}")
print(f"Adjusted total: {sum(adjusted_fc_full.values()):.0f}")
print(f"Uplift %: {(sum(adjusted_fc_full.values()) / sum(baseline_forecast.values()) - 1) * 100:.1f}%")
print()

print("=== TEST 2: Event uplift con strength=0.5 (half) ===")
adjusted_fc_half, explain_half = apply_event_uplift_to_forecast(
    sku_obj=sku_obj,
    delivery_date=delivery_date,
    horizon_dates=horizon_dates,
    baseline_forecast=baseline_forecast,
    event_rules=[event_rule_half],
    all_skus=[sku_obj],
    sales_records=sales_records,
    settings=settings,
)

print(f"Rule matched: {explain_half.rule_matched is not None}")
print(f"U_store_day: {explain_half.u_store_day:.3f}")
print(f"beta_i: {explain_half.beta_i:.3f}")
print(f"m_i (moltiplicatore finale): {explain_half.m_i:.3f}")
print(f"Baseline total: {sum(baseline_forecast.values()):.0f}")
print(f"Adjusted total: {sum(adjusted_fc_half.values()):.0f}")
print(f"Uplift %: {(sum(adjusted_fc_half.values()) / sum(baseline_forecast.values()) - 1) * 100:.1f}%")
print()

print("=== CONFRONTO ===")
print(f"m_i con strength=1.0: {explain_full.m_i:.3f}")
print(f"m_i con strength=0.5: {explain_half.m_i:.3f}")
print(f"Rapporto atteso: ~{1 + (explain_full.m_i - 1) * 0.5:.3f}")
print(f"Rapporto reale: {explain_half.m_i:.3f}")
print()

# Verifica formula: m_i_half dovrebbe essere ~1 + (m_i_full - 1) * 0.5
expected_m_i_half = 1.0 + (explain_full.m_i - 1.0) * 0.5
if abs(explain_half.m_i - expected_m_i_half) < 0.01:
    print("✓ SUCCESSO: strength integrato correttamente nella formula m_i")
else:
    print(f"✗ ERRORE: m_i con strength=0.5 è {explain_half.m_i:.3f}, atteso {expected_m_i_half:.3f}")

print("\nTest completato!")
