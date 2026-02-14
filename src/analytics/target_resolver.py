"""
Target Service Level Resolver

Determines the target Cycle Service Level (CSL) alpha for each SKU using a priority-based resolution chain:
1. Per-SKU override (if target_csl > 0)
2. Cluster-based mapping (perishability, then demand variability)
3. Global default fallback

Design:
- Perishable SKUs (shelf_life_days in (0, 7]) use "cluster_csl_perishable"
- Non-perishable SKUs use demand_variability mapping ("cluster_csl_{variability}")
- All results clamped to [MIN_CSL, MAX_CSL] for safety
- Missing settings sections gracefully degrade to global default
"""

from typing import Optional
from ..domain.models import SKU, DemandVariability


# Constants
PERISHABLE_THRESHOLD_DAYS = 7  # SKUs with shelf_life <= 7 days are perishable
MIN_CSL = 0.5  # Minimum allowed CSL (safety floor)
MAX_CSL = 0.999  # Maximum allowed CSL (avoid extreme z-scores)


class TargetServiceLevelResolver:
    """
    Resolves target CSL (alpha) for SKUs using priority chain:
    1. SKU-level override (target_csl field)
    2. Perishability cluster (shelf_life <= 7 days)
    3. Demand variability cluster (HIGH, STABLE, LOW, SEASONAL)
    4. Global default fallback
    """
    
    def __init__(self, settings: dict):
        """
        Initialize resolver with settings dictionary.
        
        Args:
            settings: Application settings dict with "service_level" section
        """
        self.settings = settings
        self.service_level_config = settings.get("service_level", {})
        
        # Extract default CSL
        default_csl_config = self.service_level_config.get("default_csl", {})
        self.default_csl = float(default_csl_config.get("value", 0.95))
        
        # Extract cluster CSL values (with fallback to default)
        self.cluster_csl = {
            "HIGH": self._get_cluster_value("cluster_csl_high"),
            "STABLE": self._get_cluster_value("cluster_csl_stable"),
            "LOW": self._get_cluster_value("cluster_csl_low"),
            "SEASONAL": self._get_cluster_value("cluster_csl_seasonal"),
            "PERISHABLE": self._get_cluster_value("cluster_csl_perishable"),
        }
    
    def _get_cluster_value(self, key: str) -> float:
        """Extract cluster CSL value from settings with fallback to default."""
        cluster_config = self.service_level_config.get(key, {})
        value = cluster_config.get("value", None)
        if value is None:
            # Fallback to default if cluster not configured
            return self.default_csl
        return float(value)
    
    def get_target_csl(self, sku_obj: SKU) -> float:
        """
        Determine target CSL (alpha) for a SKU using priority resolution.
        
        Priority chain:
        1. Per-SKU override (if sku.target_csl > 0)
        2. Perishable cluster (if shelf_life in (0, 7])
        3. Demand variability cluster (based on sku.demand_variability)
        4. Global default fallback
        
        Args:
            sku_obj: SKU domain object
        
        Returns:
            Target CSL alpha (clamped to [MIN_CSL, MAX_CSL])
        """
        try:
            # Priority 1: Per-SKU override
            if sku_obj.target_csl > 0:
                return self._clamp(sku_obj.target_csl)
            
            # Priority 2: Perishability cluster
            if self._is_perishable(sku_obj):
                perishable_csl = self.cluster_csl.get("PERISHABLE", self.default_csl)
                return self._clamp(perishable_csl)
            
            # Priority 3: Demand variability cluster
            variability_key = sku_obj.demand_variability.value  # Enum to string (e.g., "HIGH")
            variability_csl = self.cluster_csl.get(variability_key, None)
            if variability_csl is not None:
                return self._clamp(variability_csl)
            
            # Priority 4: Global default fallback
            return self._clamp(self.default_csl)
        
        except (AttributeError, KeyError, ValueError) as e:
            # Fail-safe: return clamped default on any error
            print(f"Warning: Error resolving CSL for SKU {sku_obj.sku}: {e}. Using default.")
            return self._clamp(self.default_csl)
    
    def _is_perishable(self, sku_obj: SKU) -> bool:
        """
        Check if SKU is perishable (shelf_life in (0, PERISHABLE_THRESHOLD_DAYS]).
        
        Args:
            sku_obj: SKU domain object
        
        Returns:
            True if perishable, False otherwise
        """
        return 0 < sku_obj.shelf_life_days <= PERISHABLE_THRESHOLD_DAYS
    
    def _clamp(self, value: float) -> float:
        """
        Clamp CSL value to safe bounds [MIN_CSL, MAX_CSL].
        
        Args:
            value: Raw CSL value
        
        Returns:
            Clamped CSL value
        """
        return max(MIN_CSL, min(value, MAX_CSL))
