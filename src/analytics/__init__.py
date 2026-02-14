"""Analytics package for service level metrics and validation."""

from .service_level import (
    ServiceLevelMetric,
    validate_service_level_settings,
    DEFAULT_CSL,
    DEFAULT_FILL_RATE_TARGET,
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_OOS_MODE,
)
from .target_resolver import (
    TargetServiceLevelResolver,
    PERISHABLE_THRESHOLD_DAYS,
    MIN_CSL,
    MAX_CSL,
)
from .kpi import (
    compute_oos_kpi,
    estimate_lost_sales,
    compute_forecast_accuracy,
    compute_supplier_proxy_kpi,
)

__all__ = [
    "ServiceLevelMetric",
    "validate_service_level_settings",
    "DEFAULT_CSL",
    "DEFAULT_FILL_RATE_TARGET",
    "DEFAULT_LOOKBACK_DAYS",
    "DEFAULT_OOS_MODE",
    "TargetServiceLevelResolver",
    "PERISHABLE_THRESHOLD_DAYS",
    "MIN_CSL",
    "MAX_CSL",
    "compute_oos_kpi",
    "estimate_lost_sales",
    "compute_forecast_accuracy",
    "compute_supplier_proxy_kpi",
]
