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
        Optional quantile estimates, e.g. {"p50": …, "p80": …, "p90": …, "p95": …}.
        Populated by Monte Carlo builds; empty for simple method.
    sigma_adj_multiplier : float
        The multiplier applied to sigma_base when modifiers were combined.
        1.0 means no adjustment (no modifiers applied, or all multipliers ≈ 1).
    """

    mu_P: float
    sigma_P: float
    protection_period_days: int
    forecast_method: str
    n_samples: int = 0
    n_censored: int = 0
    quantiles: Dict[str, float] = field(default_factory=dict)
    sigma_adj_multiplier: float = 1.0

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
    order_raw: float = 0.0
    constraints_applied: List[str] = field(default_factory=list)
    order_final: int = 0

    # ---- Legacy-specific ----
    safety_stock: int = 0
    equivalent_csl_legacy: float = 0.0

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
            "on_hand": self.position.on_hand,
            "on_order": self.position.on_order,
            "unfulfilled": self.position.unfulfilled,
            "inventory_position": round(self.position.inventory_position, 4),
            "alpha_target": self.alpha_target if self.alpha_target is not None else "",
            "z_score": round(self.z_score, 4) if self.z_score is not None else "",
            "reorder_point": round(self.reorder_point, 4),
            "modifiers_json": json.dumps(modifiers_summary, ensure_ascii=False),
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
        "on_hand", "on_order", "unfulfilled", "inventory_position",
        "alpha_target", "z_score", "reorder_point",
        "modifiers_json", "constraints_applied",
        "order_raw", "order_final",
        "safety_stock", "equivalent_csl_legacy",
    ])
