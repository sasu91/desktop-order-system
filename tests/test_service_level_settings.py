"""
Test for Service Level settings validation and defaults.
"""

import pytest
from src.analytics.service_level import (
    ServiceLevelMetric,
    validate_service_level_settings,
    DEFAULT_CSL,
    DEFAULT_FILL_RATE_TARGET,
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_OOS_MODE,
)


def test_validate_service_level_settings_missing_section():
    """Test validation with missing service_level section."""
    settings = {}
    
    normalized = validate_service_level_settings(settings)
    
    assert "service_level" in normalized
    sl = normalized["service_level"]
    
    # Check defaults applied
    assert sl["metric"]["value"] == ServiceLevelMetric.CSL.value
    assert sl["default_csl"]["value"] == DEFAULT_CSL
    assert sl["fill_rate_target"]["value"] == DEFAULT_FILL_RATE_TARGET
    assert sl["lookback_days"]["value"] == DEFAULT_LOOKBACK_DAYS
    assert sl["oos_mode"]["value"] == DEFAULT_OOS_MODE


def test_validate_service_level_settings_valid_csl():
    """Test validation with valid CSL metric."""
    settings = {
        "service_level": {
            "metric": {"value": "csl"},
            "default_csl": {"value": 0.99},
            "fill_rate_target": {"value": 0.95},
            "lookback_days": {"value": 60},
            "oos_mode": {"value": "relaxed"},
        }
    }
    
    normalized = validate_service_level_settings(settings)
    sl = normalized["service_level"]
    
    # Check values preserved
    assert sl["metric"]["value"] == "csl"
    assert sl["default_csl"]["value"] == 0.99
    assert sl["fill_rate_target"]["value"] == 0.95
    assert sl["lookback_days"]["value"] == 60
    assert sl["oos_mode"]["value"] == "relaxed"


def test_validate_service_level_settings_clamp_ranges():
    """Test validation clamps values to valid ranges."""
    settings = {
        "service_level": {
            "metric": {"value": "csl"},
            "default_csl": {"value": 1.5},  # > max
            "fill_rate_target": {"value": -0.1},  # < min
            "lookback_days": {"value": 3},  # < min
            "oos_mode": {"value": "strict"},
        }
    }
    
    normalized = validate_service_level_settings(settings)
    sl = normalized["service_level"]
    
    # Check clamping
    assert sl["default_csl"]["value"] == 0.9999  # Clamped to max
    assert sl["fill_rate_target"]["value"] == 0.01  # Clamped to min
    assert sl["lookback_days"]["value"] == 7  # Clamped to min


def test_validate_service_level_settings_invalid_metric():
    """Test validation with invalid metric (fallback to CSL)."""
    settings = {
        "service_level": {
            "metric": {"value": "invalid_metric"},
            "default_csl": {"value": 0.95},
            "fill_rate_target": {"value": 0.98},
            "lookback_days": {"value": 30},
            "oos_mode": {"value": "strict"},
        }
    }
    
    normalized = validate_service_level_settings(settings)
    sl = normalized["service_level"]
    
    # Check fallback to CSL
    assert sl["metric"]["value"] == ServiceLevelMetric.CSL.value


def test_validate_service_level_settings_invalid_oos_mode():
    """Test validation with invalid oos_mode (fallback to default)."""
    settings = {
        "service_level": {
            "metric": {"value": "csl"},
            "default_csl": {"value": 0.95},
            "fill_rate_target": {"value": 0.98},
            "lookback_days": {"value": 30},
            "oos_mode": {"value": "invalid_mode"},
        }
    }
    
    normalized = validate_service_level_settings(settings)
    sl = normalized["service_level"]
    
    # Check fallback to default
    assert sl["oos_mode"]["value"] == DEFAULT_OOS_MODE
