"""
Domain models for desktop-order-system.

Pure data classes + value objects. No I/O, no side effects.
Deterministic and fully testable.
"""
from dataclasses import dataclass, field
from enum import Enum
from datetime import date as Date
from typing import Optional


class EventType(Enum):
    """Event types that impact stock ledger."""
    SNAPSHOT = "SNAPSHOT"      # Base inventory reset: on_hand := qty
    ORDER = "ORDER"            # Increase on_order: on_order += qty
    RECEIPT = "RECEIPT"        # Receipt: on_order -= qty, on_hand += qty
    SALE = "SALE"              # Sale: on_hand -= qty
    WASTE = "WASTE"            # Waste: on_hand -= qty
    ADJUST = "ADJUST"          # Absolute set: on_hand := qty (replaces stock like SNAPSHOT)
    UNFULFILLED = "UNFULFILLED"  # Tracking only (no impact on stock)
    SKU_EDIT = "SKU_EDIT"      # SKU metadata change (description/EAN) - no stock impact
    EXPORT_LOG = "EXPORT_LOG"  # Export operation log - no stock impact
    ASSORTMENT_IN = "ASSORTMENT_IN"    # SKU back in assortment - no stock impact, affects forecast
    ASSORTMENT_OUT = "ASSORTMENT_OUT"  # SKU out of assortment - no stock impact, affects forecast


class DemandVariability(Enum):
    """Demand variability classification for SKU forecasting."""
    STABLE = "STABLE"      # Predictable, consistent demand
    LOW = "LOW"            # Low movement, sporadic
    HIGH = "HIGH"          # High volatility, unpredictable spikes
    SEASONAL = "SEASONAL"  # Seasonal patterns


@dataclass(frozen=True)
class SKU:
    """Stock Keeping Unit (inventory item) - immutable."""
    sku: str
    description: str
    ean: Optional[str] = None  # EAN/GTIN; can be empty or invalid
    
    # Order parameters
    moq: int = 1                    # Minimum Order Quantity
    pack_size: int = 1              # Pack size for order rounding (applied before MOQ)
    lead_time_days: int = 7         # Delivery lead time in days
    review_period: int = 7          # Review period in days (for forecast calculation)
    safety_stock: int = 0           # Safety stock quantity
    shelf_life_days: int = 0        # Shelf life in days (0 = no expiry/non-perishable)
    
    # Shelf life operational parameters (for reorder engine integration)
    min_shelf_life_days: int = 0    # Minimum residual shelf life for sale (days, 0 = no constraint)
    waste_penalty_mode: str = ""    # "soft", "hard", or "" (use global setting)
    waste_penalty_factor: float = 0.0  # Soft penalty multiplier 0.0-1.0 (0 = use global)
    waste_risk_threshold: float = 0.0  # Waste risk % threshold for penalty trigger (0-100, 0 = use global)
    
    max_stock: int = 999            # Maximum stock level
    reorder_point: int = 10         # Reorder trigger point
    demand_variability: DemandVariability = DemandVariability.STABLE
    oos_boost_percent: float = 0.0  # OOS boost percentage (0-100, 0 = use global setting)
    oos_detection_mode: str = ""  # OOS detection mode: "strict", "relaxed", or "" (use global)
    oos_popup_preference: str = "ask"  # OOS popup behavior: "ask", "always_yes", "always_no"
    
    # Forecast method selection
    forecast_method: str = ""  # "simple", "monte_carlo", or "" (use global default)
    
    # Monte Carlo override parameters (used only if forecast_method="monte_carlo")
    mc_distribution: str = ""  # "empirical", "normal", "lognormal", "residuals", or "" (use global)
    mc_n_simulations: int = 0  # Number of simulations (0 = use global)
    mc_random_seed: int = 0  # Random seed (0 = use global)
    mc_output_stat: str = ""  # "mean", "percentile", or "" (use global)
    mc_output_percentile: int = 0  # Percentile value 50-99 (0 = use global)
    mc_horizon_mode: str = ""  # "auto", "custom", or "" (use global)
    mc_horizon_days: int = 0  # Custom horizon days (0 = use global)
    
    # Assortment status
    in_assortment: bool = True  # True = in assortment (active), False = out of assortment (discontinued)
    
    def __post_init__(self):
        if not self.sku or not self.sku.strip():
            raise ValueError("SKU cannot be empty")
        if not self.description or not self.description.strip():
            raise ValueError("Description cannot be empty")
        if self.moq < 1:
            raise ValueError("MOQ must be >= 1")
        if self.pack_size < 1:
            raise ValueError("Pack size must be >= 1")
        if self.lead_time_days < 1 or self.lead_time_days > 365:
            raise ValueError("Lead time must be between 1 and 365 days")
        if self.review_period < 0:
            raise ValueError("Review period cannot be negative")
        if self.safety_stock < 0:
            raise ValueError("Safety stock cannot be negative")
        if self.shelf_life_days < 0:
            raise ValueError("Shelf life cannot be negative")
        if self.min_shelf_life_days < 0:
            raise ValueError("Min shelf life cannot be negative")
        if self.min_shelf_life_days > self.shelf_life_days and self.shelf_life_days > 0:
            raise ValueError("Min shelf life cannot exceed total shelf life")
        if self.waste_penalty_mode not in ["", "soft", "hard"]:
            raise ValueError("Waste penalty mode must be '', 'soft', or 'hard'")
        if self.waste_penalty_factor < 0.0 or self.waste_penalty_factor > 1.0:
            raise ValueError("Waste penalty factor must be 0.0-1.0")
        if self.waste_risk_threshold < 0.0 or self.waste_risk_threshold > 100.0:
            raise ValueError("Waste risk threshold must be 0.0-100.0")
        if self.max_stock < 1:
            raise ValueError("Max stock must be >= 1")
        if self.reorder_point < 0:
            raise ValueError("Reorder point cannot be negative")
        if self.oos_boost_percent < 0 or self.oos_boost_percent > 100:
            raise ValueError("OOS boost percent must be between 0 and 100")
        if self.oos_detection_mode not in ["", "strict", "relaxed"]:
            raise ValueError("OOS detection mode must be '', 'strict', or 'relaxed'")
        if self.oos_popup_preference not in ["ask", "always_yes", "always_no"]:
            raise ValueError("OOS popup preference must be 'ask', 'always_yes', or 'always_no'")
        if self.forecast_method not in ["", "simple", "monte_carlo"]:
            raise ValueError("Forecast method must be '', 'simple', or 'monte_carlo'")
        if self.mc_distribution not in ["", "empirical", "normal", "lognormal", "residuals"]:
            raise ValueError("MC distribution must be '', 'empirical', 'normal', 'lognormal', or 'residuals'")
        if self.mc_n_simulations < 0:
            raise ValueError("MC n_simulations cannot be negative")
        if self.mc_output_stat not in ["", "mean", "percentile"]:
            raise ValueError("MC output_stat must be '', 'mean', or 'percentile'")
        if self.mc_output_percentile < 0 or self.mc_output_percentile > 99:
            raise ValueError("MC output_percentile must be 0-99")
        if self.mc_horizon_mode not in ["", "auto", "custom"]:
            raise ValueError("MC horizon_mode must be '', 'auto', or 'custom'")
        if self.mc_horizon_days < 0:
            raise ValueError("MC horizon_days cannot be negative")


@dataclass(frozen=True)
class Transaction:
    """Ledger transaction event - immutable."""
    date: Date          # Event date (YYYY-MM-DD)
    sku: str            # Reference to SKU
    event: EventType    # Event type
    qty: int            # Signed quantity
    receipt_date: Optional[Date] = None  # For ORDER/RECEIPT events
    note: Optional[str] = None
    
    def __post_init__(self):
        if self.date > Date.today():
            raise ValueError("Transaction date cannot be in the future")
        if self.event == EventType.SNAPSHOT and self.qty < 0:
            raise ValueError("SNAPSHOT qty must be non-negative")


@dataclass(frozen=True)
class Lot:
    """Inventory lot with expiry tracking - immutable."""
    lot_id: str                      # Unique lot identifier (supplier lot or internal)
    sku: str                         # Reference to SKU
    expiry_date: Optional[Date]      # Expiry date (None = no expiry for this lot)
    qty_on_hand: int                 # Current quantity in this lot
    receipt_id: str                  # Reference to receiving_logs
    receipt_date: Date               # Date when lot was received
    
    def __post_init__(self):
        if not self.lot_id or not self.lot_id.strip():
            raise ValueError("Lot ID cannot be empty")
        if not self.sku or not self.sku.strip():
            raise ValueError("SKU cannot be empty")
        if self.qty_on_hand < 0:
            raise ValueError("Lot quantity cannot be negative")
        if self.expiry_date and self.expiry_date < self.receipt_date:
            raise ValueError("Expiry date cannot be before receipt date")
    
    def is_expired(self, check_date: Date) -> bool:
        """Check if lot is expired as of check_date."""
        if self.expiry_date is None:
            return False
        return check_date > self.expiry_date
    
    def days_until_expiry(self, check_date: Date) -> Optional[int]:
        """Days until expiry from check_date (None if no expiry)."""
        if self.expiry_date is None:
            return None
        delta = (self.expiry_date - check_date).days
        return delta


@dataclass(frozen=True)
class Stock:
    """Calculated stock state for a SKU at a given AsOf date."""
    sku: str
    on_hand: int
    on_order: int
    unfulfilled_qty: int = 0  # Backorder/cancellazioni (reduce availability)
    asof_date: Optional[Date] = None
    
    def __post_init__(self):
        if self.on_hand < 0:
            raise ValueError("on_hand cannot be negative")
        if self.on_order < 0:
            raise ValueError("on_order cannot be negative")
        if self.unfulfilled_qty < 0:
            raise ValueError("unfulfilled_qty cannot be negative")
    
    def available(self) -> int:
        """Total available inventory (on_hand + on_order)."""
        return self.on_hand + self.on_order
    
    def inventory_position(self) -> int:
        """Inventory Position = on_hand + on_order - unfulfilled_qty."""
        return max(0, self.on_hand + self.on_order - self.unfulfilled_qty)


@dataclass(frozen=True)
class AuditLog:
    """Audit trail entry for tracking operations."""
    timestamp: str      # ISO format with time: YYYY-MM-DD HH:MM:SS
    operation: str      # SKU_EDIT, EXPORT, etc.
    sku: Optional[str]  # Affected SKU (if applicable)
    details: str        # Human-readable description
    user: str = "system"  # User/operator (default: system)


@dataclass(frozen=True)
class SalesRecord:
    """Daily sales record from sales.csv."""
    date: Date
    sku: str
    qty_sold: int
    
    def __post_init__(self):
        if self.qty_sold < 0:
            raise ValueError("qty_sold cannot be negative")


@dataclass
class OrderProposal:
    """Order proposal for a SKU."""
    sku: str
    description: str
    current_on_hand: int
    current_on_order: int
    daily_sales_avg: float
    proposed_qty: int
    receipt_date: Optional[Date] = None
    notes: Optional[str] = None
    shelf_life_warning: bool = False  # True if proposed qty exceeds shelf life capacity
    mc_comparison_qty: Optional[int] = None  # Monte Carlo forecast qty (informativo)
    
    # Monte Carlo calculation details
    mc_method_used: str = ""  # "monte_carlo" se MC è il metodo principale, "" altrimenti
    mc_distribution: str = ""  # empirical, normal, lognormal, residuals
    mc_n_simulations: int = 0  # Numero simulazioni MC
    mc_random_seed: int = 0  # Seed per riproducibilità
    mc_output_stat: str = ""  # mean, percentile
    mc_output_percentile: int = 0  # es. 90 per P90
    mc_horizon_mode: str = ""  # auto (lead+review) o custom
    mc_horizon_days: int = 0  # Orizzonte forecast MC (giorni)
    mc_forecast_values_summary: str = ""  # Sintesi valori forecast (es. "min=5, max=25, avg=12")
    
    # Calculation details (for transparency in UI)
    forecast_period_days: int = 0  # lead_time + review_period
    forecast_qty: int = 0  # daily_sales_avg × forecast_period
    lead_time_demand: int = 0  # daily_sales_avg × lead_time
    safety_stock: int = 0
    target_S: int = 0  # forecast + safety_stock
    inventory_position: int = 0  # on_hand + on_order - unfulfilled_qty
    unfulfilled_qty: int = 0  # Backorder/cancellazioni
    proposed_qty_before_rounding: int = 0  # max(0, S - inventory_position)
    pack_size: int = 1
    moq: int = 1
    max_stock: int = 999
    shelf_life_days: int = 0
    capped_by_max_stock: bool = False
    capped_by_shelf_life: bool = False
    projected_stock_at_receipt: int = 0  # Stock previsto alla data di ricevimento
    oos_days_count: int = 0  # Giorni OOS nel periodo lookback
    oos_boost_applied: bool = False  # True se è stato applicato uplift OOS
    oos_boost_percent: float = 0.0  # Percentuale uplift applicata (es. 0.20 = 20%)
    simulation_used: bool = False  # True se è stata usata simulazione intermittente
    simulation_trigger_day: int = 0  # Giorno in cui IP scende sotto soglia (0 = oggi)
    simulation_notes: str = ""  # Note sulla simulazione
    
    # Shelf life integration (Fase 2)
    usable_stock: int = 0  # Stock utilizzabile (shelf life >= min_shelf_life_days)
    unusable_stock: int = 0  # Stock non utilizzabile (scaduto o shelf life insufficiente)
    waste_risk_percent: float = 0.0  # % stock a rischio spreco (oggi)
    waste_risk_forward_percent: float = 0.0  # % stock a rischio spreco al receipt_date (include ordine)
    shelf_life_penalty_applied: bool = False  # True se penalty applicato
    shelf_life_penalty_message: str = ""  # Messaggio penalty (es. "Reduced by 50%")


@dataclass
class OrderConfirmation:
    """Confirmed order details."""
    order_id: str
    date: Date
    sku: str
    qty_ordered: int
    receipt_date: Date
    status: str = "PENDING"  # PENDING, RECEIVED, PARTIAL


@dataclass
class ReceivingLog:
    """Receiving closure record."""
    receipt_id: str
    date: Date
    sku: str
    qty_received: int
    receipt_date: Date


# Helper function for auto-classification integration
def auto_classify_variability(
    sku: str,
    sales_records: list,
    settings: dict
) -> DemandVariability:
    """
    Auto-classify demand variability for a SKU using adaptive thresholds.
    
    This is a convenience wrapper around auto_variability module that:
    1. Loads settings parameters
    2. Calls classification logic
    3. Returns single SKU result
    
    Args:
        sku: SKU identifier
        sales_records: List of SalesRecord objects
        settings: Settings dict from JSON
    
    Returns:
        DemandVariability: Classified category
    """
    from .auto_variability import (
        compute_sku_metrics,
        compute_adaptive_thresholds,
        classify_demand_variability,
        classify_all_skus
    )
    
    # Extract settings
    auto_settings = settings.get("auto_variability", {})
    enabled = auto_settings.get("enabled", {}).get("value", True)
    
    if not enabled:
        # Return default if auto-classification disabled
        fallback = auto_settings.get("fallback_category", {}).get("value", "LOW")
        return DemandVariability[fallback]
    
    min_obs = auto_settings.get("min_observations", {}).get("value", 30)
    stable_pct = auto_settings.get("stable_percentile", {}).get("value", 25)
    high_pct = auto_settings.get("high_percentile", {}).get("value", 75)
    seasonal_thresh = auto_settings.get("seasonal_threshold", {}).get("value", 0.3)
    fallback = auto_settings.get("fallback_category", {}).get("value", "LOW")
    
    # Classify all SKUs to get adaptive thresholds
    all_classifications = classify_all_skus(
        sales_records=sales_records,
        min_observations=min_obs,
        stable_percentile=stable_pct,
        high_percentile=high_pct,
        seasonal_threshold=seasonal_thresh,
        fallback_category=DemandVariability[fallback]
    )
    
    # Return classification for requested SKU
    return all_classifications.get(sku, DemandVariability[fallback])
