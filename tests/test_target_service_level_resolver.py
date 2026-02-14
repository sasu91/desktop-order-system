"""
Tests for TargetServiceLevelResolver

Verifies priority chain, clamping, perishability detection, variability mapping, and fallback logic.
"""

import pytest
from src.analytics.target_resolver import TargetServiceLevelResolver, PERISHABLE_THRESHOLD_DAYS, MIN_CSL, MAX_CSL
from src.domain.models import SKU, DemandVariability


@pytest.fixture
def default_settings():
    """Default settings with full cluster configuration."""
    return {
        "service_level": {
            "default_csl": {"value": 0.95},
            "cluster_csl_high": {"value": 0.98},
            "cluster_csl_stable": {"value": 0.95},
            "cluster_csl_low": {"value": 0.90},
            "cluster_csl_seasonal": {"value": 0.95},
            "cluster_csl_perishable": {"value": 0.93},
        }
    }


@pytest.fixture
def minimal_settings():
    """Minimal settings with only default_csl (missing clusters)."""
    return {
        "service_level": {
            "default_csl": {"value": 0.95},
        }
    }


@pytest.fixture
def empty_settings():
    """Empty settings (no service_level section)."""
    return {}


def test_override_priority(default_settings):
    """Test that per-SKU override takes precedence over all clusters."""
    resolver = TargetServiceLevelResolver(default_settings)
    
    # Create SKU with HIGH variability and perishable shelf life, but with override
    sku = SKU(
        sku="TEST001",
        description="Test SKU with override",
        demand_variability=DemandVariability.HIGH,
        shelf_life_days=5,  # Perishable
        target_csl=0.99,  # Override
    )
    
    result = resolver.get_target_csl(sku)
    assert result == 0.99, "Override should take priority over cluster mappings"


def test_perishability_detection(default_settings):
    """Test that perishable SKUs (shelf_life <= 7) use PERISHABLE cluster."""
    resolver = TargetServiceLevelResolver(default_settings)
    
    # Perishable SKU (shelf_life = 5)
    sku_perishable = SKU(
        sku="TEST002",
        description="Perishable SKU",
        demand_variability=DemandVariability.STABLE,
        shelf_life_days=5,
        target_csl=0.0,  # No override
    )
    
    result = resolver.get_target_csl(sku_perishable)
    assert result == 0.93, f"Perishable SKU should use cluster_csl_perishable (0.93), got {result}"
    
    # Edge case: shelf_life = 7 (exactly at threshold)
    sku_edge = SKU(
        sku="TEST003",
        description="Edge perishable",
        demand_variability=DemandVariability.STABLE,
        shelf_life_days=7,
        target_csl=0.0,
    )
    
    result_edge = resolver.get_target_csl(sku_edge)
    assert result_edge == 0.93, f"SKU with shelf_life=7 should be perishable, got {result_edge}"
    
    # Non-perishable: shelf_life = 0 (no expiry)
    sku_no_expiry = SKU(
        sku="TEST004",
        description="No expiry",
        demand_variability=DemandVariability.STABLE,
        shelf_life_days=0,
        target_csl=0.0,
    )
    
    result_no_expiry = resolver.get_target_csl(sku_no_expiry)
    assert result_no_expiry == 0.95, f"Non-expiry SKU should use variability cluster (STABLE=0.95), got {result_no_expiry}"
    
    # Non-perishable: shelf_life = 30
    sku_long_shelf = SKU(
        sku="TEST005",
        description="Long shelf life",
        demand_variability=DemandVariability.STABLE,
        shelf_life_days=30,
        target_csl=0.0,
    )
    
    result_long = resolver.get_target_csl(sku_long_shelf)
    assert result_long == 0.95, f"SKU with shelf_life=30 should use variability cluster, got {result_long}"


def test_variability_mapping(default_settings):
    """Test that demand variability correctly maps to cluster CSL."""
    resolver = TargetServiceLevelResolver(default_settings)
    
    test_cases = [
        (DemandVariability.HIGH, 0.98),
        (DemandVariability.STABLE, 0.95),
        (DemandVariability.LOW, 0.90),
        (DemandVariability.SEASONAL, 0.95),
    ]
    
    for variability, expected_csl in test_cases:
        sku = SKU(
            sku=f"TEST_{variability.value}",
            description=f"SKU with {variability.value} variability",
            demand_variability=variability,
            shelf_life_days=0,  # No expiry (avoid perishable cluster)
            target_csl=0.0,  # No override
        )
        
        result = resolver.get_target_csl(sku)
        assert result == expected_csl, f"{variability.value} variability should map to {expected_csl}, got {result}"


def test_fallback_default(default_settings):
    """Test that resolver falls back to default CSL when no override or cluster match."""
    # Create settings with missing clusters
    settings_missing_cluster = {
        "service_level": {
            "default_csl": {"value": 0.92},
            # No cluster_csl_* keys
        }
    }
    
    resolver = TargetServiceLevelResolver(settings_missing_cluster)
    
    # SKU with STABLE variability (cluster missing → fallback to default)
    sku = SKU(
        sku="TEST_FALLBACK",
        description="SKU with missing cluster",
        demand_variability=DemandVariability.STABLE,
        shelf_life_days=0,
        target_csl=0.0,
    )
    
    result = resolver.get_target_csl(sku)
    assert result == 0.92, f"Missing cluster should fallback to default (0.92), got {result}"


def test_clamping(default_settings):
    """Test that resolver clamps CSL values to [MIN_CSL, MAX_CSL]."""
    # Test upper clamping via cluster settings (not SKU override, which is validated by model)
    settings_extreme_cluster = {
        "service_level": {
            "default_csl": {"value": 0.95},
            "cluster_csl_high": {"value": 0.999999},  # Extreme value in settings
        }
    }
    
    resolver = TargetServiceLevelResolver(settings_extreme_cluster)
    
    sku_high = SKU(
        sku="TEST_CLAMP_HIGH",
        description="SKU using extreme cluster CSL",
        demand_variability=DemandVariability.HIGH,
        shelf_life_days=0,
        target_csl=0.0,  # No override, use cluster
    )
    
    result_high = resolver.get_target_csl(sku_high)
    assert result_high == MAX_CSL, f"Extreme cluster CSL should clamp to MAX_CSL ({MAX_CSL}), got {result_high}"
    
    # Test lower clamping via settings
    settings_low_cluster = {
        "service_level": {
            "default_csl": {"value": 0.95},
            "cluster_csl_low": {"value": 0.01},  # Very low cluster value
        }
    }
    
    resolver_low = TargetServiceLevelResolver(settings_low_cluster)
    
    sku_low = SKU(
        sku="TEST_CLAMP_LOW",
        description="SKU using low cluster CSL",
        demand_variability=DemandVariability.LOW,
        shelf_life_days=0,
        target_csl=0.0,
    )
    
    result_low = resolver_low.get_target_csl(sku_low)
    assert result_low == MIN_CSL, f"Low cluster CSL (0.01) should clamp to MIN_CSL ({MIN_CSL}), got {result_low}"
    
    # Test that valid SKU override values are preserved (no clamping if in range)
    sku_valid_override = SKU(
        sku="TEST_NO_CLAMP",
        description="SKU with valid override",
        demand_variability=DemandVariability.STABLE,
        target_csl=0.97,  # Valid override in (0, 1)
    )
    
    result_valid = resolver.get_target_csl(sku_valid_override)
    assert result_valid == 0.97, f"Valid override should be preserved without clamping, got {result_valid}"


def test_missing_cluster_fallback(minimal_settings):
    """Test that missing cluster configurations gracefully fallback to default."""
    resolver = TargetServiceLevelResolver(minimal_settings)
    
    # Test HIGH variability (cluster missing)
    sku = SKU(
        sku="TEST_MISSING",
        description="SKU with missing cluster",
        demand_variability=DemandVariability.HIGH,
        shelf_life_days=0,
        target_csl=0.0,
    )
    
    result = resolver.get_target_csl(sku)
    assert result == 0.95, f"Missing HIGH cluster should fallback to default (0.95), got {result}"


def test_invalid_settings_robustness(empty_settings):
    """Test that resolver handles empty/invalid settings gracefully."""
    resolver = TargetServiceLevelResolver(empty_settings)
    
    sku = SKU(
        sku="TEST_EMPTY",
        description="SKU with empty settings",
        demand_variability=DemandVariability.STABLE,
        shelf_life_days=0,
        target_csl=0.0,
    )
    
    # Should fallback to hardcoded default (0.95) and clamp
    result = resolver.get_target_csl(sku)
    assert MIN_CSL <= result <= MAX_CSL, f"Empty settings should return valid clamped CSL, got {result}"
    # Default CSL extraction should use 0.95 when missing
    assert result == 0.95, f"Expected default 0.95 from empty settings, got {result}"


def test_perishable_threshold_constant():
    """Verify PERISHABLE_THRESHOLD_DAYS constant is 7."""
    assert PERISHABLE_THRESHOLD_DAYS == 7, "Perishable threshold should be 7 days"


def test_csl_bounds_constants():
    """Verify MIN_CSL and MAX_CSL constants."""
    assert MIN_CSL == 0.5, "MIN_CSL should be 0.5"
    assert MAX_CSL == 0.999, "MAX_CSL should be 0.999"


def test_priority_chain_order(default_settings):
    """Test complete priority chain: override → perishable → variability → default."""
    resolver = TargetServiceLevelResolver(default_settings)
    
    # Priority 1: Override (highest)
    sku_override = SKU(
        sku="PRIO1",
        description="Override",
        demand_variability=DemandVariability.HIGH,
        shelf_life_days=5,  # Perishable
        target_csl=0.97,  # Override
    )
    assert resolver.get_target_csl(sku_override) == 0.97
    
    # Priority 2: Perishable (no override)
    sku_perishable = SKU(
        sku="PRIO2",
        description="Perishable",
        demand_variability=DemandVariability.HIGH,
        shelf_life_days=5,
        target_csl=0.0,  # No override
    )
    assert resolver.get_target_csl(sku_perishable) == 0.93  # PERISHABLE cluster
    
    # Priority 3: Variability (no override, not perishable)
    sku_variability = SKU(
        sku="PRIO3",
        description="Variability",
        demand_variability=DemandVariability.HIGH,
        shelf_life_days=0,  # Not perishable
        target_csl=0.0,
    )
    assert resolver.get_target_csl(sku_variability) == 0.98  # HIGH cluster
    
    # Priority 4: Default (no override, not perishable, cluster missing)
    settings_no_clusters = {
        "service_level": {
            "default_csl": {"value": 0.96},
        }
    }
    resolver_fallback = TargetServiceLevelResolver(settings_no_clusters)
    sku_default = SKU(
        sku="PRIO4",
        description="Default fallback",
        demand_variability=DemandVariability.STABLE,
        shelf_life_days=0,
        target_csl=0.0,
    )
    assert resolver_fallback.get_target_csl(sku_default) == 0.96  # Default


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
