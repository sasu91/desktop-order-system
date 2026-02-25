"""
Service Level framework: metrics, validation, and settings normalization.

This module defines service level measurement approaches (CSL vs fill-rate proxy)
and provides validation/normalization for service_level settings section.
"""

from enum import Enum
from typing import Dict, Any


# Service level metric types
class ServiceLevelMetric(str, Enum):
    """Service level measurement approach."""
    CSL = "csl"  # Cycle Service Level (probability of no stockout per cycle)
    FILL_RATE_PROXY = "fill_rate_proxy"  # Fill rate estimation via OOS tracking


# Default constants
DEFAULT_CSL = 0.95
DEFAULT_FILL_RATE_TARGET = 0.98
DEFAULT_LOOKBACK_DAYS = 30
DEFAULT_OOS_MODE = "strict"

# Valid OOS modes
VALID_OOS_MODES = {"strict", "relaxed"}

# Validation bounds
MIN_SERVICE_LEVEL = 0.01  # 1%
MAX_SERVICE_LEVEL = 0.9999  # 99.99%
MIN_LOOKBACK_DAYS = 7


def validate_service_level_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate and normalize service_level settings section.
    
    Applies fallback defaults and clamps values to valid ranges.
    Returns a normalized settings dict (does not raise exceptions).
    
    Args:
        settings: Full settings dict (must contain "service_level" section)
    
    Returns:
        Normalized settings dict with valid service_level section
    """
    # Get service_level section or create with defaults
    sl_section = settings.get("service_level", {})
    
    # Extract and validate metric
    metric_raw = sl_section.get("metric", {}).get("value", ServiceLevelMetric.CSL.value)
    try:
        metric = ServiceLevelMetric(metric_raw)
    except ValueError:
        # Fallback to CSL if invalid
        metric = ServiceLevelMetric.CSL
    
    # Extract and clamp default_csl
    default_csl_raw = sl_section.get("default_csl", {}).get("value", DEFAULT_CSL)
    try:
        default_csl = float(default_csl_raw)
        default_csl = max(MIN_SERVICE_LEVEL, min(MAX_SERVICE_LEVEL, default_csl))
    except (ValueError, TypeError):
        default_csl = DEFAULT_CSL
    
    # Extract and clamp fill_rate_target
    fill_rate_raw = sl_section.get("fill_rate_target", {}).get("value", DEFAULT_FILL_RATE_TARGET)
    try:
        fill_rate_target = float(fill_rate_raw)
        fill_rate_target = max(MIN_SERVICE_LEVEL, min(MAX_SERVICE_LEVEL, fill_rate_target))
    except (ValueError, TypeError):
        fill_rate_target = DEFAULT_FILL_RATE_TARGET
    
    # Extract and clamp lookback_days
    lookback_raw = sl_section.get("lookback_days", {}).get("value", DEFAULT_LOOKBACK_DAYS)
    try:
        lookback_days = int(lookback_raw)
        lookback_days = max(MIN_LOOKBACK_DAYS, lookback_days)
    except (ValueError, TypeError):
        lookback_days = DEFAULT_LOOKBACK_DAYS
    
    # Extract and validate oos_mode
    oos_mode_raw = sl_section.get("oos_mode", {}).get("value", DEFAULT_OOS_MODE)
    if oos_mode_raw not in VALID_OOS_MODES:
        oos_mode = DEFAULT_OOS_MODE
    else:
        oos_mode = oos_mode_raw
    
    # Build normalized service_level section
    normalized_sl = {
        "metric": {
            "value": metric.value,
            "description": "Service level metric: 'csl' (Cycle Service Level) or 'fill_rate_proxy' (OOS-based estimation)",
        },
        "default_csl": {
            "value": default_csl,
            "min": MIN_SERVICE_LEVEL,
            "max": MAX_SERVICE_LEVEL,
            "description": "Default Cycle Service Level target (probability of no stockout per replenishment cycle)",
        },
        "fill_rate_target": {
            "value": fill_rate_target,
            "min": MIN_SERVICE_LEVEL,
            "max": MAX_SERVICE_LEVEL,
            "description": "Fill rate target when using fill_rate_proxy metric (% of demand met from stock)",
        },
        "lookback_days": {
            "value": lookback_days,
            "min": MIN_LOOKBACK_DAYS,
            "description": "Lookback period (days) for service level KPI calculations",
        },
        "oos_mode": {
            "value": oos_mode,
            "choices": list(VALID_OOS_MODES),
            "description": "OOS detection strictness for service level KPIs: 'strict' (IP=0) or 'relaxed' (sales=0 + low IP)",
        },
    }
    
    # Update settings with normalized section
    normalized_settings = settings.copy()
    normalized_settings["service_level"] = normalized_sl
    
    return normalized_settings
