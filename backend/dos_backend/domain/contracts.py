"""
Forecast → Policy Contract: typed dataclasses forming the single interface
between demand estimation and replenishment policy computation.

ARCHITECTURE RULE
-----------------
Every call chain that ends in a policy (legacy or CSL) MUST pass data through
these objects.  No policy is allowed to recalculate mu_P / sigma_P internally
once a DemandDistribution has been built externally.

Pipeline (single flow):
    build_demand_distribution()
        → apply_modifiers()
            → compute_order_v2()   (CSL) or legacy_order_qty()
                → apply_order_constraints()
                    → OrderExplain  (full audit trail)

Objects
-------
DemandDistribution   – mu_P, sigma_P, quantiles, metadata
InventoryPosition    – on_hand, on_order, unfulfilled, pipeline, IP property
AppliedModifier      – a single demand modifier with full provenance
OrderExplain         – complete audit record for one SKU proposal

Helper
------
DemandDistribution.with_modifiers_applied(modifiers)
    Returns a new (frozen) DemandDistribution with mu_P and sigma_P updated
    according to the cumulative multiplier of the supplied modifiers.

    Sigma scaling rule (conservative, statistically motivated):
        sigma_adj = sigma_base × clamp(mean_multiplier, 1.0, 2.5)
    Rationale: during promotional / event periods demand variability is
    higher than baseline.  We do not have enough promo-only observations to
    re-estimate sigma from scratch, so we apply a conservative upper-bounded
    scaling that prevents under-safety-stock without over-inflating sigma.

Author: Desktop Order System Team
Date: February 2026
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Date-basis constants
# ---------------------------------------------------------------------------

#: Modifier window evaluated against the ORDER date (when the order is placed).
DATE_BASIS_ORDER = "ORDER_DATE"

#: Modifier window evaluated against the DELIVERY / receipt date.
DATE_BASIS_DELIVERY = "DELIVERY_DATE"


# ---------------------------------------------------------------------------
# DemandDistribution
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DemandDistribution:
    """
    Aggregated demand forecast over the protection period P.

    Attributes
    ----------
    mu_P : float
        Expected total demand over P days (point forecast).
    sigma_P : float
        Demand uncertainty over P days (standard deviation).
    protection_period_days : int
        Length of period P (order-date to receipt-date).
    forecast_method : str
        Method used: "simple" | "monte_carlo".
    n_samples : int
        Number of historical days used in model fitting.
    n_censored : int
        Number of days excluded as censored (OOS / unavailable).
    quantiles : dict
        Quantile estimates over horizon P (not per-day).
        Keys are alpha values as strings: "0.50", "0.80", "0.90", "0.95", etc.
        For MC: computed from distribution D_P (sum over P days per trajectory).
        For simple: empty dict.
    sigma_adj_multiplier : float
        The multiplier applied to sigma_base when modifiers were combined.
        1.0 means no adjustment (no modifiers applied, or all multipliers ≈ 1).
    mc_n_simulations : int
        Number of Monte Carlo simulations (0 if not MC).
    mc_random_seed : int
        Random seed used (0 if not MC or random).
    mc_distribution : str
        Distribution type: "empirical" | "normal" | "lognormal" | "residuals" | "".
    mc_horizon_days : int
        Horizon days used in MC simulation (0 if not MC).
    mc_output_percentile : int
        Percentile used for mu_P if output_stat="percentile" (0 if mean).
    """

    mu_P: float
    sigma_P: float
    protection_period_days: int
    forecast_method: str
    n_samples: int = 0
    n_censored: int = 0
    quantiles: Dict[str, float] = field(default_factory=dict)
    sigma_adj_multiplier: float = 1.0
    mc_n_simulations: int = 0
    mc_random_seed: int = 0
    mc_distribution: str = ""
    mc_horizon_days: int = 0
    mc_output_percentile: int = 0
    
    # Intermittent forecast metadata
    intermittent_classification: bool = False  # True if classified as intermittent
    intermittent_adi: float = 0.0  # Average Demand Interval
    intermittent_cv2: float = 0.0  # Squared coefficient of variation
    intermittent_method: str = ""  # "croston", "sba", "tsb", or ""
    intermittent_alpha: float = 0.0  # Smoothing parameter used (0 if not intermittent)
    intermittent_p_t: float = 0.0  # Final smoothed interval (Croston/SBA) or 0
    intermittent_z_t: float = 0.0  # Final smoothed size
    intermittent_b_t: float = 0.0  # Final smoothed probability (TSB only)
    intermittent_backtest_wmape: float = 0.0  # Backtest WMAPE performance (0 if not tested)
    intermittent_backtest_bias: float = 0.0  # Backtest bias (mean error)
    intermittent_n_nonzero: int = 0  # Count of non-zero demands in lookback

    def __post_init__(self) -> None:
        if self.mu_P < 0:
            raise ValueError(f"mu_P must be >= 0, got {self.mu_P}")
        if self.sigma_P < 0:
            raise ValueError(f"sigma_P must be >= 0, got {self.sigma_P}")
        if self.protection_period_days < 0:
            raise ValueError(f"protection_period_days must be >= 0, got {self.protection_period_days}")

    # ------------------------------------------------------------------
    # Modifier application
    # ------------------------------------------------------------------

    def with_modifiers_applied(
        self,
        modifiers: List["AppliedModifier"],
    ) -> Tuple["DemandDistribution", float]:
        """
        Return a new DemandDistribution with mu_P and sigma_P adjusted by
        the supplied modifiers.

        Returns
        -------
        (adjusted_distribution, cumulative_multiplier)

        Modifier stacking rules
        -----------------------
        - "multiplicative" modifiers: multiply together → cum_mult = ∏ m_i
        - "additive" modifiers: treated as (1 + delta_i) and multiplied in.
        - scope=="mu_only"  → only mu_P is scaled; sigma unchanged.
        - scope=="qty_correction" → ignored here (applied post-policy).
        - scope in ("both", "sigma") → sigma also scaled.

        Sigma scaling:
            sigma_adj = sigma_base × clamp(cum_mult, 1.0, 2.5)
        The clamp lower-bound of 1.0 ensures sigma never decreases due to
        a downlift modifier (cannibalization), which would be overly optimistic.
        """
        if not modifiers:
            return self, 1.0

        # Compute cumulative multiplier
        cum_mult = 1.0
        for mod in modifiers:
            if mod.scope == "qty_correction":
                continue  # Post-policy adjustments: not applied here
            if mod.stacking == "multiplicative":
                cum_mult *= mod.multiplier
            else:  # "additive": treat `multiplier` as a delta (e.g., 0.3 → +30%)
                cum_mult *= (1.0 + mod.multiplier)

        new_mu = self.mu_P * cum_mult

        # Sigma: scale only upward (conservative – see module docstring)
        sigma_mult = max(1.0, min(cum_mult, 2.5))
        # Only apply sigma scaling if at least one modifier affects "both" or "sigma"
        any_sigma_scope = any(
            mod.scope in ("both", "sigma") for mod in modifiers
            if mod.scope != "qty_correction"
        )
        if any_sigma_scope:
            new_sigma = self.sigma_P * sigma_mult
        else:
            new_sigma = self.sigma_P

        # Build new object (frozen → use object.__new__ pattern via dataclass replace)
        from dataclasses import replace
        new_dist = replace(
            self,
            mu_P=max(0.0, new_mu),
            sigma_P=max(0.0, new_sigma),
            sigma_adj_multiplier=sigma_mult,
        )
        return new_dist, cum_mult


# ---------------------------------------------------------------------------
# Modifier – rule / definition template (resolved before apply)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Modifier:
    """
    Immutable definition of a single demand modifier rule.

    This represents the *template* loaded from promo_calendar, event_rules,
    holidays.json, or settings – before it is applied to a specific
    DemandDistribution.  The result of applying it is an ``AppliedModifier``.

    Fields
    ------
    id           : unique stable key (e.g. "promo_2026-02-18_SKU001")
    name         : human-readable display label
    scope_type   : "global" | "store" | "category" | "department" | "sku"
    scope_key    : value for scope_type; empty string = applies to all
    date_basis   : DATE_BASIS_ORDER | DATE_BASIS_DELIVERY
    kind         : "multiplicative" | "additive"
    value        : factor (1.2 = +20%) or additive delta
    precedence   : EVENT=1, PROMO=2, CANNIBALIZATION=3, HOLIDAY=4
    modifier_type: "event" | "promo" | "cannibalization" | "holiday"
    start / end  : window bounds (inclusive).  None = open-ended.
    """
    id: str
    name: str
    scope_type: str
    scope_key: str
    date_basis: str
    kind: str
    value: float
    precedence: int
    modifier_type: str
    start: Optional[date] = None
    end: Optional[date] = None

    def is_active_for_date(self, check_date: date) -> bool:
        """Return True if check_date falls within [start, end] (inclusive)."""
        if self.start and check_date < self.start:
            return False
        if self.end and check_date > self.end:
            return False
        return True


# ---------------------------------------------------------------------------
# ModifierContext – input to list_modifiers / apply_modifiers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ModifierContext:
    """
    All contextual information needed to resolve which Modifier rules apply.

    Fields
    ------
    sku_id        : target SKU identifier
    category      : SKU category (for scope filtering)
    department    : SKU department (for scope filtering)
    order_date    : the date the order is being computed (→ DATE_BASIS_ORDER)
    horizon_dates : list of dates in the forecast window [d+1 … d+P]
    promo_windows : raw PromoWindow objects from storage
    event_rules   : raw EventUpliftRule objects from storage
    holidays      : list of holiday dicts from holidays.json
    settings      : global settings dict
    delivery_date : expected delivery / receipt date (→ DATE_BASIS_DELIVERY)
    all_skus      : all SKU objects (for cannibalization group resolution)
    sales_records : pre-loaded sales records
    transactions  : pre-loaded transactions
    """
    sku_id: str
    category: str
    department: str
    order_date: date
    horizon_dates: List[date]
    promo_windows: List
    event_rules: List
    holidays: List
    settings: Dict
    delivery_date: Optional[date] = None
    all_skus: List = field(default_factory=list)
    sales_records: List = field(default_factory=list)
    transactions: List = field(default_factory=list)


# ---------------------------------------------------------------------------
# InventoryPosition
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class InventoryPosition:
    """
    Inventory snapshot used as input to the replenishment policy.

    Attributes
    ----------
    on_hand : float
        Physical stock available now (shelf-life adjusted if applicable).
    on_order : float
        Quantity already ordered and in-transit (from pipeline).
    unfulfilled : float
        Unfulfilled / back-order quantity (negative demand impact).
    pipeline : list
        Raw pipeline records: [{"receipt_date": date, "qty": int}, …]
        Used by compute_order_v2 for date-filtered IP calculation.
    """

    on_hand: float
    on_order: float
    unfulfilled: float = 0.0
    pipeline: List[Dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.on_hand < 0:
            raise ValueError(f"on_hand must be >= 0, got {self.on_hand}")
        if self.on_order < 0:
            raise ValueError(f"on_order must be >= 0, got {self.on_order}")

    @property
    def inventory_position(self) -> float:
        """IP = on_hand + on_order - unfulfilled (traditional formula)."""
        return self.on_hand + self.on_order - self.unfulfilled

    def ip_asof(self, asof_date: date) -> float:
        """
        IP filtered to orders arriving on or before *asof_date*.

        Uses the pipeline list to compute on_order_filtered, then:
            IP_asof = on_hand + on_order_filtered - unfulfilled
        """
        on_order_filtered = sum(
            item["qty"]
            for item in self.pipeline
            if item.get("receipt_date") and item["receipt_date"] <= asof_date
        )
        return self.on_hand + on_order_filtered - self.unfulfilled


# ---------------------------------------------------------------------------
# AppliedModifier
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AppliedModifier:
    """
    A single demand modifier with full provenance for explainability.

    Attributes
    ----------
    name : str
        Human-readable identifier, e.g. "promo_uplift", "event_christmas",
        "cannibalization_sku_x".
    modifier_type : str
        Category: "promo" | "event" | "cannibalization" | "holiday" | "qty_correction".
    scope : str
        What the modifier affects:
        - "mu_only"        → only mu_P is scaled
        - "sigma"          → only sigma_P is scaled (rare)
        - "both"           → mu_P and sigma_P are scaled
        - "qty_correction" → applied post-policy (prebuild, guardrail, etc.)
    multiplier : float
        Multiplicative factor (>1 uplift, <1 downlift) or additive delta.
    stacking : str
        "multiplicative" | "additive"
    date_range : tuple (start, end) or None
        Date window for which the modifier is active.
    source_sku : str or None
        Originating SKU (for cannibalization: the driver SKU).
    confidence : str
        Confidence level: "A" | "B" | "C" | "".
    note : str
        Free-form explanation for display / CSV export.
    """

    name: str
    modifier_type: str
    scope: str
    multiplier: float
    stacking: str = "multiplicative"
    date_range: Optional[Tuple[date, date]] = None
    source_sku: Optional[str] = None
    confidence: str = ""
    note: str = ""
    # Modifiers Engine (Feb 2026): provenance and step trace
    precedence: int = 0           # Fixed: EVENT=1, PROMO=2, CANNIBALIZATION=3, HOLIDAY=4
    date_basis: str = DATE_BASIS_DELIVERY  # DATE_BASIS_ORDER or DATE_BASIS_DELIVERY
    mu_before: float = 0.0        # mu_P immediately before this modifier was applied
    mu_after: float = 0.0         # mu_P immediately after this modifier was applied

    def __post_init__(self) -> None:
        valid_scopes = {"mu_only", "sigma", "both", "qty_correction"}
        if self.scope not in valid_scopes:
            raise ValueError(f"scope must be one of {valid_scopes}, got {self.scope!r}")
        valid_stacking = {"multiplicative", "additive"}
        if self.stacking not in valid_stacking:
            raise ValueError(f"stacking must be one of {valid_stacking}, got {self.stacking!r}")

    @property
    def impact_pct(self) -> float:
        """Percentage impact relative to 1.0 baseline."""
        if self.stacking == "additive":
            return self.multiplier * 100.0
        return (self.multiplier - 1.0) * 100.0


# ---------------------------------------------------------------------------
# OrderExplain
# ---------------------------------------------------------------------------

@dataclass
class OrderExplain:
    """
    Full audit record for a single SKU's order proposal.

    Serialisable to dict / JSON / CSV row.  Every field that influenced Q
    must appear here.

    Usage
    -----
    explain = OrderExplain(sku="SKU001", ...)
    as_dict = explain.to_dict()       # flat dict, CSV-friendly
    as_json = explain.to_json()       # JSON string

    CSV export columns (order_explain):
        sku, asof_date, forecast_method, policy_mode,
        mu_P, sigma_P, sigma_adj_multiplier,
        protection_period_days, n_samples, n_censored,
        on_hand, on_order, unfulfilled, inventory_position,
        alpha_target, z_score, reorder_point,
        modifiers_json, constraints_applied,
        order_raw, order_final
    """

    sku: str
    asof_date: date

    # ---- Demand ----
    demand: DemandDistribution

    # ---- Inventory ----
    position: InventoryPosition

    # ---- Modifiers ----
    modifiers: List[AppliedModifier] = field(default_factory=list)

    # ---- Policy inputs ----
    policy_mode: str = ""          # "legacy" | "csl"
    alpha_target: Optional[float] = None
    z_score: Optional[float] = None

    # ---- Policy outputs ----
    reorder_point: float = 0.0
    reorder_point_method: str = ""  # "quantile" | "z_score" | "z_score_fallback" | "legacy"
    quantile_used: Optional[float] = None  # Value of Q(alpha) if quantile method used
    order_raw: float = 0.0
    constraints_applied: List[str] = field(default_factory=list)
    order_final: int = 0

    # ---- Legacy-specific ----
    safety_stock: int = 0
    equivalent_csl_legacy: float = 0.0

    def _build_modifier_trace(self) -> Dict[str, float]:
        """
        Reconstruct step-by-step mu_P trace from applied modifiers.

        Uses mu_before / mu_after on each AppliedModifier.  Falls back to the
        final mu_P if those fields were not populated (backward-compat).

        Keys: mu_base, mu_after_event, mu_after_promo,
              mu_after_cannibalization, mu_after_holiday, mu_final.
        """
        actual = [m for m in self.modifiers if m.scope != "qty_correction"]

        # mu_base: mu_P before any modifier
        if actual and actual[0].mu_before > 0:
            mu_base = actual[0].mu_before
        else:
            # Fallback: back-calculate from the first modifier's multiplier
            if actual and actual[0].multiplier and actual[0].multiplier != 0:
                mu_base = self.demand.mu_P / actual[0].multiplier
            else:
                mu_base = self.demand.mu_P

        current_mu = mu_base
        trace: Dict[str, float] = {"mu_base": round(mu_base, 4)}

        for type_key in ("event", "promo", "cannibalization", "holiday"):
            mods = [m for m in actual if m.modifier_type == type_key]
            if mods:
                last = mods[-1]
                if last.mu_after > 0:
                    current_mu = last.mu_after
                # else: trust cumulative mu from demand
            trace[f"mu_after_{type_key}"] = round(current_mu, 4)

        trace["mu_final"] = round(self.demand.mu_P, 4)
        return trace

    def to_dict(self) -> Dict:
        """Flat dict representation – safe for CSV row or JSON export."""
        import json

        modifiers_summary = [
            {
                "name": m.name,
                "type": m.modifier_type,
                "scope": m.scope,
                "multiplier": round(m.multiplier, 4),
                "stacking": m.stacking,
                "confidence": m.confidence,
                "note": m.note,
                "date_range": (
                    [m.date_range[0].isoformat(), m.date_range[1].isoformat()]
                    if m.date_range else None
                ),
                "source_sku": m.source_sku,
            }
            for m in self.modifiers
        ]

        return {
            "sku": self.sku,
            "asof_date": self.asof_date.isoformat(),
            "forecast_method": self.demand.forecast_method,
            "policy_mode": self.policy_mode,
            "mu_P": round(self.demand.mu_P, 4),
            "sigma_P": round(self.demand.sigma_P, 4),
            "sigma_adj_multiplier": round(self.demand.sigma_adj_multiplier, 4),
            "protection_period_days": self.demand.protection_period_days,
            "n_samples": self.demand.n_samples,
            "n_censored": self.demand.n_censored,
            "quantiles_json": json.dumps(self.demand.quantiles) if self.demand.quantiles else "",
            "mc_n_simulations": self.demand.mc_n_simulations,
            "mc_random_seed": self.demand.mc_random_seed,
            "mc_distribution": self.demand.mc_distribution,
            "mc_horizon_days": self.demand.mc_horizon_days,
            "mc_output_percentile": self.demand.mc_output_percentile,
            "intermittent_classification": self.demand.intermittent_classification,
            "intermittent_adi": round(self.demand.intermittent_adi, 4),
            "intermittent_cv2": round(self.demand.intermittent_cv2, 4),
            "intermittent_method": self.demand.intermittent_method,
            "intermittent_alpha": round(self.demand.intermittent_alpha, 4),
            "intermittent_p_t": round(self.demand.intermittent_p_t, 4),
            "intermittent_z_t": round(self.demand.intermittent_z_t, 4),
            "intermittent_b_t": round(self.demand.intermittent_b_t, 4),
            "intermittent_backtest_wmape": round(self.demand.intermittent_backtest_wmape, 4),
            "intermittent_backtest_bias": round(self.demand.intermittent_backtest_bias, 4),
            "intermittent_n_nonzero": self.demand.intermittent_n_nonzero,
            "on_hand": self.position.on_hand,
            "on_order": self.position.on_order,
            "unfulfilled": self.position.unfulfilled,
            "inventory_position": round(self.position.inventory_position, 4),
            "alpha_target": self.alpha_target if self.alpha_target is not None else "",
            "z_score": round(self.z_score, 4) if self.z_score is not None else "",
            "reorder_point": round(self.reorder_point, 4),
            "reorder_point_method": self.reorder_point_method,
            "quantile_used": round(self.quantile_used, 4) if self.quantile_used is not None else "",
            "modifiers_json": json.dumps(modifiers_summary, ensure_ascii=False),
            "modifier_trace_json": json.dumps(self._build_modifier_trace(), ensure_ascii=False),
            "constraints_applied": "; ".join(self.constraints_applied),
            "order_raw": round(self.order_raw, 4),
            "order_final": self.order_final,
            "safety_stock": self.safety_stock,
            "equivalent_csl_legacy": round(self.equivalent_csl_legacy, 4),
        }

    def to_json(self) -> str:
        """JSON string representation."""
        import json
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    # CSV header for order_explain export
    CSV_COLUMNS: List[str] = field(default_factory=lambda: [
        "sku", "asof_date", "forecast_method", "policy_mode",
        "mu_P", "sigma_P", "sigma_adj_multiplier",
        "protection_period_days", "n_samples", "n_censored",
        "quantiles_json",
        "mc_n_simulations", "mc_random_seed", "mc_distribution", "mc_horizon_days", "mc_output_percentile",
        "intermittent_classification", "intermittent_adi", "intermittent_cv2",
        "intermittent_method", "intermittent_alpha", "intermittent_p_t", "intermittent_z_t", "intermittent_b_t",
        "intermittent_backtest_wmape", "intermittent_backtest_bias", "intermittent_n_nonzero",
        "on_hand", "on_order", "unfulfilled", "inventory_position",
        "alpha_target", "z_score", "reorder_point", "reorder_point_method", "quantile_used",
        "modifiers_json", "modifier_trace_json", "constraints_applied",
        "order_raw", "order_final",
        "safety_stock", "equivalent_csl_legacy",
    ])
