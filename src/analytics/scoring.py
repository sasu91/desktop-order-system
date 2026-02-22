"""
SKU Scoring Engine — Importance / Health / Priority

Produces three unit-based, price-free scores for each SKU:

  importance_score (0–100)
    Reflects transaction volume + selling frequency over a rolling window.
    Uses log1p + percentile rank to handle long-tail SKU distributions.

  health_score (0–100)
    Weighted composite of five operational sub-scores:
      - Availability     (OOS rate)       default weight 35 %
      - Waste/Freshness  (waste rate)      default weight 20 %  (perishables only)
      - Inventory Eff.   (days-of-supply)  default weight 15 %
      - Supplier Rel.    (fill/OTIF/delay) default weight 15 %
      - Forecast Quality (WMAPE + bias)    default weight 15 %
    Weights are renormalised when a block is not applicable (e.g. non-perishable).

  priority_score (0–100)
    priority = importance × (1 − health/100), then robust-scaled across the
    SKU population so the full 0–100 range is used each day.

Design constraints:
  - Pure functions: no file I/O, no datetime.now(), deterministic given inputs.
  - All scores are floats; never None or NaN — 0.0 is the safe fallback.
  - WMAPE is expected on the [0,100] percent scale (consistent with kpi.py).
  - waste_rate is a fraction [0,1+] produced by compute_waste_rate() in kpi.py.

Confidence / data-quality:
  - confidence_score in [0,1]: proportion of features that were actually observed.
  - data_quality_flag: "OK" | "LOW_DATA" | "MISSING_KPI" | "INCONSISTENT".

MVP version: v1_mvp
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date as Date, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Constants & defaults
# ---------------------------------------------------------------------------

SCORING_VERSION = "v1_mvp"

# Default Health weights (must sum to 1.0)
DEFAULT_WEIGHTS = {
    "availability": 0.35,
    "waste": 0.20,          # only for perishables (shelf_life_days > 0)
    "inventory_eff": 0.15,
    "supplier": 0.15,
    "forecast": 0.15,
}

# Minimum observed selling days required for a "high-confidence" Importance score.
MIN_SELLING_DAYS_HIGH_CONFIDENCE = 14

# Forecast-block parameters
WMAPE_CAP_PCT    = 100.0   # WMAPE at or above this value scores 0
BIAS_WINSOR      = 2.0     # winsorise |normalised bias| at this level
PI80_TARGET      = 0.80    # nominal coverage target for PI80 interval
MIN_PROMO_POINTS = 3       # minimum promo-day evaluation points for promo WMAPE
MIN_EVENT_POINTS = 3       # minimum event-day evaluation points for event WMAPE
# Weight of new probabilistic components vs legacy (WMAPE + bias) in forecast block
_FORECAST_LEGACY_WEIGHT = 0.60   # 60 % legacy (WMAPE + bias)
_FORECAST_NEW_WEIGHT    = 0.40   # 40 % new   (PI80 + promo-WMAPE + event-WMAPE)

# Target days-of-supply range for Inventory Efficiency "bell curve".
# SKUs inside [target_low, target_high] score 100; outside falls off linearly.
DOS_TARGET_LOW = 7    # days
DOS_TARGET_HIGH = 21  # days

# Delay cap for supplier normalisation (delays beyond this → 0 supplier subscore)
MAX_DELAY_DAYS = 30


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class FeatureRow:
    """
    All raw feature values extracted for one SKU / reference date.
    Missing / unavailable values are represented by None; callers must not
    pass NaN.  All distances / quantities are in *units* or *days* — no prices.
    """
    sku: str
    ref_date: Date
    lookback_days: int

    # --- Importance features ---
    units_sold: float = 0.0           # total qty sold in window
    selling_days: int = 0             # days with qty_sold > 0

    # --- Health: Availability ---
    oos_rate: Optional[float] = None  # fraction [0,1]

    # --- Health: Waste / Freshness ---
    waste_rate: Optional[float] = None   # fraction [0,1+] from compute_waste_rate
    shelf_life_days: Optional[int] = None  # None or 0 → non-perishable

    # --- Health: Inventory Efficiency ---
    days_of_supply: Optional[float] = None  # on_hand / avg_daily_sales

    # --- Health: Supplier ---
    fill_rate: Optional[float] = None   # fraction [0,1]
    otif_rate: Optional[float] = None   # fraction [0,1]
    avg_delay_days: Optional[float] = None  # days (positive = late)

    # --- Health: Forecast Quality (legacy) ---
    wmape: Optional[float] = None    # percent scale [0,100+]
    bias: Optional[float] = None     # raw bias in units (actual − forecast)
    avg_daily_sales_for_bias: float = 0.0  # used to normalise bias; 0 → skip

    # --- Health: Forecast Quality (probabilistic — schema v4) ---
    pi80_coverage: Optional[float] = None        # empirical PI80 coverage [0,1]
    pi80_coverage_error: Optional[float] = None  # coverage − 0.80 (signed)

    # --- Health: Forecast Quality (promo-segmented — schema v4) ---
    wmape_promo: Optional[float] = None   # WMAPE on promo days [0,100+]
    bias_promo: Optional[float] = None
    n_promo_points: int = 0

    # --- Health: Forecast Quality (event-segmented — schema v4) ---
    wmape_event: Optional[float] = None   # WMAPE on event days [0,100+]
    bias_event: Optional[float] = None
    n_event_points: int = 0

    # --- metadata ---
    n_observed_fields: int = 0       # filled automatically
    n_total_fields: int = 14         # total optional fields (for confidence)


@dataclass
class SKUScoringResult:
    """Full scoring output for one SKU / day."""

    sku: str
    date: str                        # ISO YYYY-MM-DD
    lookback_days: int
    scoring_version: str = SCORING_VERSION

    # --- Top-level scores ---
    importance_score: float = 0.0
    health_score: float = 0.0
    priority_score: float = 0.0

    # --- Importance breakdown ---
    importance_units_component: float = 0.0
    importance_freq_component: float = 0.0

    # --- Health sub-scores ---
    health_availability_score: float = 0.0
    health_waste_score: float = 0.0
    health_inventory_eff_score: float = 0.0
    health_supplier_score: float = 0.0
    health_forecast_score: float = 0.0

    # --- Active weights (post-renormalisation) ---
    weight_availability: float = 0.0
    weight_waste: float = 0.0
    weight_inventory_eff: float = 0.0
    weight_supplier: float = 0.0
    weight_forecast: float = 0.0

    # --- Priority internals ---
    raw_priority: float = 0.0

    # --- Metadata ---
    is_perishable: bool = False
    confidence_score: float = 0.0    # [0,1]
    data_quality_flag: str = "OK"    # OK | LOW_DATA | MISSING_KPI | INCONSISTENT
    missing_features_count: int = 0
    notes: str = ""


# ---------------------------------------------------------------------------
# Feature extraction helpers (pure, no I/O)
# ---------------------------------------------------------------------------

def build_feature_row(
    sku: str,
    ref_date: Date,
    lookback_days: int,
    sales_records: List[Any],        # List[SalesRecord]
    transactions: List[Any],         # List[Transaction]
    kpi_record: Optional[Dict[str, Any]],  # latest row from kpi_daily for this SKU
    sku_obj: Any,                    # SKU domain object (has .shelf_life_days etc.)
    stock_on_hand: float = 0.0,      # stock as-of ref_date
) -> FeatureRow:
    """
    Extract a FeatureRow for one SKU from pre-loaded in-memory collections.

    Args:
        sku:            SKU code
        ref_date:       Reference (as-of) date — must not use datetime.today()
        lookback_days:  Rolling window for sales / KPI aggregation
        sales_records:  Full list of SalesRecord objects (all SKUs, all dates)
        transactions:   Full list of Transaction objects
        kpi_record:     Latest kpi_daily row for this SKU (dict, may be None)
        sku_obj:        SKU domain object
        stock_on_hand:  Current on-hand units as calculated by StockCalculator

    Returns:
        FeatureRow with all available fields populated; None-safe.
    """
    start_date = ref_date - timedelta(days=lookback_days)

    # --- Sales window ---
    sku_sales = [
        s for s in sales_records
        if s.sku == sku and start_date <= s.date < ref_date
    ]
    units_sold = sum(s.qty_sold for s in sku_sales)
    selling_days = sum(1 for s in sku_sales if s.qty_sold > 0)

    # --- Average daily sales for bias normalisation ---
    avg_daily_sales = units_sold / lookback_days if lookback_days > 0 else 0.0

    # --- Days of supply (instantaneous, not time-averaged) ---
    # avg_daily_sales over window is the best proxy available without snapshots
    dos: Optional[float]
    if avg_daily_sales > 0:
        dos = stock_on_hand / avg_daily_sales
    elif stock_on_hand > 0:
        dos = None   # stock exists but no sales → undefined (slow mover flag)
    else:
        dos = 0.0    # no stock, no sales

    # --- KPI fields from cache (if available) ---
    def _float_kpi(key: str) -> Optional[float]:
        if kpi_record is None:
            return None
        val = kpi_record.get(key)
        if val is None or val == "" or val == "None":
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    oos_rate        = _float_kpi("oos_rate")
    waste_rate      = _float_kpi("waste_rate")
    fill_rate       = _float_kpi("fill_rate")
    otif_rate       = _float_kpi("otif_rate")
    avg_delay_days  = _float_kpi("avg_delay_days")
    wmape           = _float_kpi("wmape")
    bias_raw        = _float_kpi("bias")
    # --- schema v4 forecast extended ---
    pi80_coverage       = _float_kpi("pi80_coverage")
    pi80_coverage_error = _float_kpi("pi80_coverage_error")
    wmape_promo         = _float_kpi("wmape_promo")
    bias_promo          = _float_kpi("bias_promo")
    wmape_event         = _float_kpi("wmape_event")
    bias_event          = _float_kpi("bias_event")

    def _int_kpi(key: str) -> int:
        if kpi_record is None:
            return 0
        val = kpi_record.get(key)
        if val in (None, "", "None"):
            return 0
        try:
            return int(float(val))  # csv strings may be "3.0"
        except (ValueError, TypeError):
            return 0

    n_promo_points = _int_kpi("n_promo_points")
    n_event_points = _int_kpi("n_event_points")

    # --- Shelf life ---
    sl = getattr(sku_obj, "shelf_life_days", None)
    shelf_life_days: Optional[int] = int(sl) if sl and int(sl) > 0 else None

    row = FeatureRow(
        sku=sku,
        ref_date=ref_date,
        lookback_days=lookback_days,
        units_sold=units_sold,
        selling_days=selling_days,
        oos_rate=oos_rate,
        waste_rate=waste_rate,
        shelf_life_days=shelf_life_days,
        days_of_supply=dos,
        fill_rate=fill_rate,
        otif_rate=otif_rate,
        avg_delay_days=avg_delay_days,
        wmape=wmape,
        bias=bias_raw,
        avg_daily_sales_for_bias=avg_daily_sales,
        pi80_coverage=pi80_coverage,
        pi80_coverage_error=pi80_coverage_error,
        wmape_promo=wmape_promo,
        bias_promo=bias_promo,
        n_promo_points=n_promo_points,
        wmape_event=wmape_event,
        bias_event=bias_event,
        n_event_points=n_event_points,
    )

    # Count how many optional health features are present
    optional_vals = [
        oos_rate, waste_rate, dos, fill_rate, otif_rate, avg_delay_days,
        wmape, bias_raw,
        # v4 probabilistic/segmented
        pi80_coverage, pi80_coverage_error, wmape_promo, wmape_event,
        # int fields count separately: treat >0 as observed
        n_promo_points if n_promo_points > 0 else None,
        n_event_points if n_event_points > 0 else None,
    ]
    row.n_observed_fields = sum(1 for v in optional_vals if v is not None)
    row.n_total_fields = len(optional_vals)

    return row


# ---------------------------------------------------------------------------
# Normalisation utilities (pure, stateless per-function)
# ---------------------------------------------------------------------------

def _clamp(val: float, lo: float, hi: float) -> float:
    """Clamp val to [lo, hi]."""
    return max(lo, min(hi, val))


def _percentile_rank(value: float, population: Sequence[float]) -> float:
    """
    Return the percentile rank of `value` in `population` as a fraction [0,1].
    Handles ties by averaging.  Empty population → 0.5 (neutral).
    """
    if not population:
        return 0.5
    below = sum(1 for x in population if x < value)
    equal = sum(1 for x in population if x == value)
    n = len(population)
    return (below + 0.5 * equal) / n


def _robust_scale_list(values: List[float]) -> List[float]:
    """
    Scale a list to [0, 100] using percentile rank (robust, no outlier distortion).
    Zero-variance input → all 50.0.
    """
    if not values:
        return []
    ranks = []
    for v in values:
        ranks.append(_percentile_rank(v, values) * 100.0)
    return ranks


# ---------------------------------------------------------------------------
# Importance scoring (cross-SKU, takes full population)
# ---------------------------------------------------------------------------

def compute_importance_scores(
    feature_rows: List[FeatureRow],
) -> Dict[str, Tuple[float, float, float]]:
    """
    Compute Importance scores for a population of SKUs in one pass.

    Uses log1p + percentile rank (robust scaling) so that a handful of
    high-volume SKUs do not crush the rest of the distribution.

    Args:
        feature_rows: Full list of FeatureRow objects for the day.

    Returns:
        Dict  sku → (importance_score, units_component, freq_component)
        All values in [0, 100].
    """
    if not feature_rows:
        return {}

    # Log-transform volume
    log_units  = [math.log1p(r.units_sold) for r in feature_rows]
    freq_vals  = [float(r.selling_days)    for r in feature_rows]

    scaled_units = _robust_scale_list(log_units)
    scaled_freq  = _robust_scale_list(freq_vals)

    results: Dict[str, Tuple[float, float, float]] = {}
    for row, u_comp, f_comp in zip(feature_rows, scaled_units, scaled_freq):
        importance = 0.70 * u_comp + 0.30 * f_comp
        # Low-data penalty — soft, not a cliff
        if row.selling_days < MIN_SELLING_DAYS_HIGH_CONFIDENCE:
            penalty_factor = row.selling_days / MIN_SELLING_DAYS_HIGH_CONFIDENCE
            importance = importance * (0.5 + 0.5 * penalty_factor)  # at most -50 %
        results[row.sku] = (
            _clamp(importance, 0.0, 100.0),
            _clamp(u_comp, 0.0, 100.0),
            _clamp(f_comp, 0.0, 100.0),
        )
    return results


# ---------------------------------------------------------------------------
# Health sub-scores (per-SKU, pure)
# ---------------------------------------------------------------------------

def _availability_subscore(oos_rate: Optional[float]) -> Tuple[float, bool]:
    """
    Returns (subscore 0-100, is_observed).
    Missing oos_rate → neutral 50.0 with is_observed=False.
    """
    if oos_rate is None:
        return 50.0, False
    return _clamp((1.0 - oos_rate) * 100.0, 0.0, 100.0), True


def _waste_subscore(
    waste_rate: Optional[float],
    is_perishable: bool,
) -> Tuple[float, bool]:
    """
    Returns (subscore 0-100, is_applicable).
    Non-perishable → (0.0, False) so caller knows to zero the weight.
    Missing for perishable → neutral 50.0.
    """
    if not is_perishable:
        return 0.0, False   # block disabled
    if waste_rate is None:
        return 50.0, True   # perishable but no data → neutral
    return _clamp((1.0 - waste_rate) * 100.0, 0.0, 100.0), True


def _inventory_eff_subscore(
    days_of_supply: Optional[float],
) -> Tuple[float, bool]:
    """
    Bell-curve scoring around target DOS range [DOS_TARGET_LOW, DOS_TARGET_HIGH].
    Outside the range the score falls off linearly to 0 at 2× the range boundary.
    """
    if days_of_supply is None:
        return 50.0, False
    dos = days_of_supply
    if dos < 0:
        dos = 0.0
    if DOS_TARGET_LOW <= dos <= DOS_TARGET_HIGH:
        return 100.0, True
    elif dos < DOS_TARGET_LOW:
        # Falls from 100 at target_low to 0 at dos=0
        frac = dos / DOS_TARGET_LOW if DOS_TARGET_LOW > 0 else 0.0
        return _clamp(frac * 100.0, 0.0, 100.0), True
    else:
        # Falls from 100 at target_high to 0 at 2×target_high
        overshoot = dos - DOS_TARGET_HIGH
        max_over  = DOS_TARGET_HIGH  # 0 at 2×target_high
        frac = 1.0 - (overshoot / max_over if max_over > 0 else 1.0)
        return _clamp(frac * 100.0, 0.0, 100.0), True


def _supplier_subscore(
    fill_rate: Optional[float],
    otif_rate: Optional[float],
    avg_delay_days: Optional[float],
) -> Tuple[float, bool]:
    """
    Composite of fill_rate (40%), OTIF (40%), delay score (20%).
    Missing components get a neutral value and reduce confidence.
    """
    components = []
    observed = 0

    if fill_rate is not None:
        components.append((_clamp(fill_rate, 0.0, 1.0) * 100.0, 0.40))
        observed += 1
    else:
        components.append((50.0, 0.40))   # neutral

    if otif_rate is not None:
        components.append((_clamp(otif_rate, 0.0, 1.0) * 100.0, 0.40))
        observed += 1
    else:
        components.append((50.0, 0.40))   # neutral

    if avg_delay_days is not None:
        delay_score = _clamp(
            (1.0 - avg_delay_days / MAX_DELAY_DAYS) * 100.0, 0.0, 100.0
        )
        components.append((delay_score, 0.20))
        observed += 1
    else:
        components.append((50.0, 0.20))   # neutral

    score = sum(s * w for s, w in components)  # weights already sum to 1
    is_observed = (observed > 0)
    return _clamp(score, 0.0, 100.0), is_observed


def _forecast_subscore(
    wmape: Optional[float],
    bias: Optional[float],
    avg_daily_sales: float,
    pi80_coverage_error: Optional[float] = None,
    wmape_promo: Optional[float] = None,
    n_promo_points: int = 0,
    wmape_event: Optional[float] = None,
    n_event_points: int = 0,
) -> Tuple[float, bool]:
    """
    Composite forecast quality sub-score (0-100).

    Blends two components:
      - Legacy (60 %): WMAPE 70 % + bias 30 %   (always available when data exists)
      - New    (40 %): average of whichever probabilistic/segmented metrics are
                       present and have sufficient data:
          * PI80 coverage error  → perfect when error=0, score=100; fully wrong at ±0.5
          * Promo WMAPE          → only when n_promo_points ≥ MIN_PROMO_POINTS
          * Event WMAPE          → only when n_event_points ≥ MIN_EVENT_POINTS

    Fallback: if no new-component metrics are available, the function degrades
    gracefully to 100 % legacy behaviour (no scoring change for existing data).

    Args:
        wmape:               Legacy WMAPE on [0, 100] percent scale.
        bias:                Raw bias in units (actual − forecast).
        avg_daily_sales:     Used to normalise bias; 0 → bias component neutral.
        pi80_coverage_error: coverage − 0.80 (signed); None if not computed.
        wmape_promo:         WMAPE on promo days [0, 100+]; None if insufficient.
        n_promo_points:      Number of promo-day evaluation points.
        wmape_event:         WMAPE on event days [0, 100+]; None if insufficient.
        n_event_points:      Number of event-day evaluation points.
    """
    # --- Legacy component (WMAPE + bias) ---
    if wmape is not None:
        wmape_score = _clamp((1.0 - wmape / WMAPE_CAP_PCT) * 100.0, 0.0, 100.0)
        legacy_observed = True
    else:
        wmape_score = 50.0   # neutral
        legacy_observed = False

    if bias is not None and avg_daily_sales > 0:
        normalised_bias = min(abs(bias) / avg_daily_sales, BIAS_WINSOR)
        bias_score = _clamp((1.0 - normalised_bias / BIAS_WINSOR) * 100.0, 0.0, 100.0)
    else:
        bias_score = 50.0   # neutral

    legacy_score = 0.70 * wmape_score + 0.30 * bias_score

    # --- New component (probabilistic + segmented) ---
    new_scores: List[float] = []

    # PI80 coverage: score = 100 − |coverage_error| * 200 (clamped)
    # At coverage_error=0 → 100; at |±0.5| → 0
    if pi80_coverage_error is not None:
        pi80_score = _clamp(100.0 - abs(pi80_coverage_error) * 200.0, 0.0, 100.0)
        new_scores.append(pi80_score)

    # Promo-aware WMAPE (only when enough promo points)
    if wmape_promo is not None and n_promo_points >= MIN_PROMO_POINTS:
        promo_score = _clamp((1.0 - wmape_promo / WMAPE_CAP_PCT) * 100.0, 0.0, 100.0)
        new_scores.append(promo_score)

    # Event-aware WMAPE (only when enough event points)
    if wmape_event is not None and n_event_points >= MIN_EVENT_POINTS:
        event_score = _clamp((1.0 - wmape_event / WMAPE_CAP_PCT) * 100.0, 0.0, 100.0)
        new_scores.append(event_score)

    # --- Blend ---
    if new_scores:
        new_component = sum(new_scores) / len(new_scores)
        final_score = _FORECAST_LEGACY_WEIGHT * legacy_score + _FORECAST_NEW_WEIGHT * new_component
    else:
        # No new metrics available → pure legacy (backward-compatible)
        final_score = legacy_score

    observed = legacy_observed or bool(new_scores)
    return _clamp(final_score, 0.0, 100.0), observed


# ---------------------------------------------------------------------------
# Health scoring (per-SKU, applies weight renormalisation)
# ---------------------------------------------------------------------------

def compute_health_score(
    row: FeatureRow,
    weights: Dict[str, float] = None,
) -> Tuple[float, Dict[str, Any]]:
    """
    Compute Health score for one SKU, renormalising weights when blocks are
    not applicable.

    Args:
        row:     FeatureRow for this SKU.
        weights: Custom weight dict (keys: availability, waste, inventory_eff,
                 supplier, forecast).  Defaults to DEFAULT_WEIGHTS.

    Returns:
        (health_score 0–100, detail_dict with all sub-scores and active weights)
    """
    w = dict(DEFAULT_WEIGHTS) if weights is None else dict(weights)

    is_perishable = (row.shelf_life_days is not None and row.shelf_life_days > 0)

    # --- Compute sub-scores ---
    avail_score, avail_obs   = _availability_subscore(row.oos_rate)
    waste_score, waste_app   = _waste_subscore(row.waste_rate, is_perishable)
    inv_score,   inv_obs     = _inventory_eff_subscore(row.days_of_supply)
    supp_score,  supp_obs    = _supplier_subscore(
        row.fill_rate, row.otif_rate, row.avg_delay_days
    )
    fc_score,    fc_obs      = _forecast_subscore(
        row.wmape, row.bias, row.avg_daily_sales_for_bias,
        pi80_coverage_error=row.pi80_coverage_error,
        wmape_promo=row.wmape_promo,
        n_promo_points=row.n_promo_points,
        wmape_event=row.wmape_event,
        n_event_points=row.n_event_points,
    )

    # --- Zero non-applicable weights ---
    if not waste_app:
        w["waste"] = 0.0

    # --- Renormalise remaining weights to sum to 1.0 ---
    total_w = sum(w.values())
    if total_w > 0:
        w = {k: v / total_w for k, v in w.items()}
    else:
        # Degenerate: no blocks active → neutral health
        return 50.0, {
            "health_availability_score": avail_score,
            "health_waste_score": 0.0,
            "health_inventory_eff_score": inv_score,
            "health_supplier_score": supp_score,
            "health_forecast_score": fc_score,
            "weight_availability": 0.0,
            "weight_waste": 0.0,
            "weight_inventory_eff": 0.0,
            "weight_supplier": 0.0,
            "weight_forecast": 0.0,
            "is_perishable": is_perishable,
        }

    health = (
        w["availability"]    * avail_score +
        w["waste"]           * waste_score +
        w["inventory_eff"]   * inv_score   +
        w["supplier"]        * supp_score  +
        w["forecast"]        * fc_score
    )

    detail: Dict[str, Any] = {
        "health_availability_score":   _clamp(avail_score, 0.0, 100.0),
        "health_waste_score":          _clamp(waste_score, 0.0, 100.0),
        "health_inventory_eff_score":  _clamp(inv_score,   0.0, 100.0),
        "health_supplier_score":       _clamp(supp_score,  0.0, 100.0),
        "health_forecast_score":       _clamp(fc_score,    0.0, 100.0),
        "weight_availability":         round(w["availability"],  4),
        "weight_waste":                round(w["waste"],         4),
        "weight_inventory_eff":        round(w["inventory_eff"], 4),
        "weight_supplier":             round(w["supplier"],      4),
        "weight_forecast":             round(w["forecast"],      4),
        "is_perishable":               is_perishable,
    }
    return _clamp(health, 0.0, 100.0), detail


# ---------------------------------------------------------------------------
# Priority scoring (cross-SKU for robust rescaling)
# ---------------------------------------------------------------------------

def compute_priority_scores(
    results: List[SKUScoringResult],
) -> None:
    """
    Compute priority_score for each SKUScoringResult **in-place**.

    Formula:
        raw_priority = importance_score × (1 − health_score / 100)
    Then robust-scale raw_priority values across the population to [0, 100].

    Args:
        results: List of partially-populated SKUScoringResult objects
                 (importance_score and health_score must already be set).
    """
    if not results:
        return

    # Compute raw priorities
    for r in results:
        r.raw_priority = r.importance_score * (1.0 - r.health_score / 100.0)

    # Robust-scale to [0, 100]
    raw_vals = [r.raw_priority for r in results]
    scaled   = _robust_scale_list(raw_vals)
    for r, scaled_val in zip(results, scaled):
        r.priority_score = _clamp(scaled_val, 0.0, 100.0)


# ---------------------------------------------------------------------------
# Confidence & data-quality helpers
# ---------------------------------------------------------------------------

def _compute_confidence(row: FeatureRow, importance_score: float) -> Tuple[float, str, int]:
    """
    Returns (confidence_score [0,1], data_quality_flag, missing_features_count).

    confidence_score:
      - Starts at 1.0.
      - Penalised proportionally for missing optional features.
      - Penalised for low selling_days (cold-start / intermittent).
    """
    n_missing = row.n_total_fields - row.n_observed_fields
    feature_coverage = row.n_observed_fields / row.n_total_fields if row.n_total_fields > 0 else 0.0
    selling_coverage = min(row.selling_days / MIN_SELLING_DAYS_HIGH_CONFIDENCE, 1.0)

    confidence = 0.70 * feature_coverage + 0.30 * selling_coverage
    confidence = _clamp(confidence, 0.0, 1.0)

    if confidence >= 0.80:
        flag = "OK"
    elif confidence >= 0.50 or row.selling_days < 7:
        flag = "LOW_DATA"
    else:
        flag = "MISSING_KPI"

    return round(confidence, 4), flag, n_missing


# ---------------------------------------------------------------------------
# Batch scoring: one function to score the full population for a day
# ---------------------------------------------------------------------------

def score_all_skus(
    feature_rows: List[FeatureRow],
    weights: Optional[Dict[str, float]] = None,
) -> List[SKUScoringResult]:
    """
    Score the full SKU population for a given reference date.

    Steps:
      1. Compute Importance (cross-SKU, robust scaling)
      2. Compute Health (per-SKU, renormalised weights)
      3. Compute Priority (cross-SKU, robust scaling of raw product)
      4. Set confidence / data-quality

    Args:
        feature_rows: One FeatureRow per SKU, all for the same ref_date.
        weights:      Custom health weights (optional).

    Returns:
        List of SKUScoringResult, one per FeatureRow, same order.
    """
    if not feature_rows:
        return []

    # --- Step 1: Importance ---
    importance_map = compute_importance_scores(feature_rows)

    # --- Step 2: Health (per-SKU) ---
    results: List[SKUScoringResult] = []
    for row in feature_rows:
        imp, u_comp, f_comp = importance_map.get(row.sku, (50.0, 50.0, 50.0))
        health, detail = compute_health_score(row, weights)

        confidence, flag, n_missing = _compute_confidence(row, imp)

        r = SKUScoringResult(
            sku=row.sku,
            date=row.ref_date.isoformat(),
            lookback_days=row.lookback_days,
            scoring_version=SCORING_VERSION,
            importance_score=imp,
            health_score=health,
            importance_units_component=u_comp,
            importance_freq_component=f_comp,
            health_availability_score=detail["health_availability_score"],
            health_waste_score=detail["health_waste_score"],
            health_inventory_eff_score=detail["health_inventory_eff_score"],
            health_supplier_score=detail["health_supplier_score"],
            health_forecast_score=detail["health_forecast_score"],
            weight_availability=detail["weight_availability"],
            weight_waste=detail["weight_waste"],
            weight_inventory_eff=detail["weight_inventory_eff"],
            weight_supplier=detail["weight_supplier"],
            weight_forecast=detail["weight_forecast"],
            is_perishable=detail["is_perishable"],
            confidence_score=confidence,
            data_quality_flag=flag,
            missing_features_count=n_missing,
        )
        results.append(r)

    # --- Step 3: Priority (cross-SKU, in-place) ---
    compute_priority_scores(results)

    return results
