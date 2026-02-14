"""
Closed-Loop KPI-Driven Parameter Tuning

This module implements a closed-loop system that uses KPI measurements to propose
or automatically apply controlled adjustments to SKU target service levels (CSL).

Architecture:
- Reads KPI metrics from kpi_daily.csv (OOS rate, forecast accuracy, waste rate)
- Evaluates decision rules with guardrails from settings (thresholds, step limits)
- Proposes or applies CSL adjustments to individual SKUs
- Logs all decisions with full audit trail

Key Features:
- Enabled/disabled via settings.closed_loop.enabled
- Two action modes: "suggest" (report only) or "apply" (auto-update SKU.target_csl)
- Guardrails: max step size, WMAPE reliability check, absolute CSL bounds
- Decision rules:
  * High OOS rate + reliable forecast → increase CSL
  * High WMAPE (unreliable forecast) → block adjustments
  * High waste rate (perishables) + sufficient events → decrease CSL
- Full audit trail for all suggest/apply operations
"""

from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
import math


@dataclass
class ClosedLoopDecision:
    """Decision output for a single SKU."""
    sku: str
    current_csl: float
    suggested_csl: float
    delta_csl: float
    action: str  # "increase", "decrease", "hold", "blocked"
    reason: str
    oos_rate: Optional[float] = None
    wmape: Optional[float] = None
    waste_rate: Optional[float] = None
    waste_events_count: int = 0
    guardrail_applied: Optional[str] = None  # Which guardrail was activated


@dataclass
class ClosedLoopReport:
    """Full report from closed-loop analysis."""
    asof_date: str
    enabled: bool
    action_mode: str  # "suggest" or "apply"
    decisions: List[ClosedLoopDecision]
    skus_processed: int
    skus_with_changes: int
    skus_blocked: int
    skus_applied: int = 0  # Only populated in apply mode
    guardrails: Optional[Dict[str, Any]] = None  # Settings snapshot
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert report to dict for serialization."""
        return {
            "asof_date": self.asof_date,
            "enabled": self.enabled,
            "action_mode": self.action_mode,
            "decisions": [
                {
                    "sku": d.sku,
                    "current_csl": d.current_csl,
                    "suggested_csl": d.suggested_csl,
                    "delta_csl": d.delta_csl,
                    "action": d.action,
                    "reason": d.reason,
                    "oos_rate": d.oos_rate,
                    "wmape": d.wmape,
                    "waste_rate": d.waste_rate,
                    "waste_events_count": d.waste_events_count,
                    "guardrail_applied": d.guardrail_applied
                }
                for d in self.decisions
            ],
            "summary": {
                "skus_processed": self.skus_processed,
                "skus_with_changes": self.skus_with_changes,
                "skus_blocked": self.skus_blocked,
                "skus_applied": self.skus_applied
            },
            "guardrails": self.guardrails
        }


def run_closed_loop(csv_layer, asof_date: datetime) -> ClosedLoopReport:
    """
    Execute closed-loop KPI-driven parameter tuning analysis.
    
    Workflow:
    1. Read closed_loop settings and validate guardrails
    2. Load all SKUs and their current target CSL
    3. Load latest KPI metrics for each SKU (from kpi_daily.csv)
    4. Evaluate decision rules per SKU with guardrails
    5. Generate decisions (increase/decrease/hold/blocked)
    6. If action_mode="apply", update SKU.target_csl and log audit
    7. Return structured report
    
    Args:
        csv_layer: CSVLayer instance for data access
        asof_date: Date for "as-of" analysis (typically today)
    
    Returns:
        ClosedLoopReport with decisions and summary statistics
    """
    from .target_resolver import TargetServiceLevelResolver
    
    # Read settings
    settings = csv_layer.read_settings()
    cl_settings = settings.get("closed_loop", {})
    
    # Extract configuration
    enabled = cl_settings.get("enabled", {}).get("value", False)
    action_mode = cl_settings.get("action_mode", {}).get("value", "suggest")
    max_step = cl_settings.get("max_alpha_step_per_review", {}).get("value", 0.02)
    oos_threshold = cl_settings.get("oos_rate_threshold", {}).get("value", 0.05)
    wmape_threshold = cl_settings.get("wmape_threshold", {}).get("value", 0.60)
    waste_threshold = cl_settings.get("waste_rate_threshold", {}).get("value", 0.10)
    min_waste_events = cl_settings.get("min_waste_events", {}).get("value", 3)
    min_csl = cl_settings.get("min_csl_absolute", {}).get("value", 0.50)
    max_csl = cl_settings.get("max_csl_absolute", {}).get("value", 0.999)
    
    guardrails = {
        "max_step": max_step,
        "oos_threshold": oos_threshold,
        "wmape_threshold": wmape_threshold,
        "waste_threshold": waste_threshold,
        "min_waste_events": min_waste_events,
        "min_csl": min_csl,
        "max_csl": max_csl
    }
    
    # Initialize report
    decisions = []
    skus_applied = 0
    
    # If disabled, return empty report
    if not enabled:
        return ClosedLoopReport(
            asof_date=asof_date.strftime("%Y-%m-%d"),
            enabled=False,
            action_mode=action_mode,
            decisions=[],
            skus_processed=0,
            skus_with_changes=0,
            skus_blocked=0,
            skus_applied=0,
            guardrails=guardrails
        )
    
    # Load SKUs and resolver
    skus = csv_layer.read_skus()
    resolver = TargetServiceLevelResolver(settings)
    
    # Process each SKU
    for sku_obj in skus:
        sku_id = sku_obj.sku
        
        # Get current target CSL
        current_csl = resolver.get_target_csl(sku_obj)
        
        # Load latest KPI metrics for this SKU
        kpi_metrics = _load_latest_kpi_metrics(csv_layer, sku_id, asof_date)
        
        # Evaluate decision based on KPI metrics
        decision = _evaluate_decision(
            sku_id=sku_id,
            sku_obj=sku_obj,
            current_csl=current_csl,
            kpi_metrics=kpi_metrics,
            guardrails=guardrails,
            csv_layer=csv_layer,
            asof_date=asof_date
        )
        
        decisions.append(decision)
        
        # Apply changes if in apply mode and action is not hold/blocked
        if action_mode == "apply" and decision.action in ["increase", "decrease"]:
            # Update SKU.target_csl
            # Get SKU details for update call
            sku = next(s for s in skus if s.sku == sku_id)
            csv_layer.update_sku(
                old_sku_id=sku_id,
                new_sku_id=sku_id,
                new_description=sku.description,
                new_ean=sku.ean,
                target_csl=decision.suggested_csl
            )
            
            # Log audit
            oos_str = f"{decision.oos_rate:.4f}" if decision.oos_rate is not None else "N/A"
            wmape_str = f"{decision.wmape:.4f}" if decision.wmape is not None else "N/A"
            waste_str = f"{decision.waste_rate:.4f}" if decision.waste_rate is not None else "N/A"
            
            audit_details = (
                f"sku={sku_id}, asof={asof_date.strftime('%Y-%m-%d')}, "
                f"mode=apply, old_csl={decision.current_csl:.4f}, "
                f"new_csl={decision.suggested_csl:.4f}, delta={decision.delta_csl:+.4f}, "
                f"oos_rate={oos_str}, wmape={wmape_str}, waste_rate={waste_str}, "
                f"reason={decision.reason}, guardrail={decision.guardrail_applied or 'none'}"
            )
            csv_layer.log_audit(
                operation="CLOSED_LOOP_APPLY",
                details=audit_details,
                sku=sku_id,
                user="system"
            )
            
            skus_applied += 1
        
        elif action_mode == "suggest" and decision.action in ["increase", "decrease"]:
            # Log suggestion only
            oos_str = f"{decision.oos_rate:.4f}" if decision.oos_rate is not None else "N/A"
            wmape_str = f"{decision.wmape:.4f}" if decision.wmape is not None else "N/A"
            waste_str = f"{decision.waste_rate:.4f}" if decision.waste_rate is not None else "N/A"
            
            audit_details = (
                f"sku={sku_id}, asof={asof_date.strftime('%Y-%m-%d')}, "
                f"mode=suggest, current_csl={decision.current_csl:.4f}, "
                f"suggested_csl={decision.suggested_csl:.4f}, delta={decision.delta_csl:+.4f}, "
                f"oos_rate={oos_str}, wmape={wmape_str}, waste_rate={waste_str}, "
                f"reason={decision.reason}, guardrail={decision.guardrail_applied or 'none'}"
            )
            csv_layer.log_audit(
                operation="CLOSED_LOOP_SUGGEST",
                details=audit_details,
                sku=sku_id,
                user="system"
            )
    
    # Compute summary stats
    skus_with_changes = sum(1 for d in decisions if d.action in ["increase", "decrease"])
    skus_blocked = sum(1 for d in decisions if d.action == "blocked")
    
    return ClosedLoopReport(
        asof_date=asof_date.strftime("%Y-%m-%d"),
        enabled=True,
        action_mode=action_mode,
        decisions=decisions,
        skus_processed=len(skus),
        skus_with_changes=skus_with_changes,
        skus_blocked=skus_blocked,
        skus_applied=skus_applied,
        guardrails=guardrails
    )


def _load_latest_kpi_metrics(csv_layer, sku_id: str, asof_date: datetime) -> Dict[str, Any]:
    """
    Load the most recent KPI metrics for a SKU from kpi_daily.csv.
    
    Args:
        csv_layer: CSVLayer instance
        sku_id: SKU identifier
        asof_date: Reference date
    
    Returns:
        Dict with keys: oos_rate, wmape, waste_rate, waste_events_count, kpi_date
        Values are None if no KPI data available
    """
    kpi_records = csv_layer.read_kpi_daily()
    
    # Filter for this SKU, sort by date descending
    sku_kpis = [r for r in kpi_records if r["sku"] == sku_id]
    
    if not sku_kpis:
        return {
            "oos_rate": None,
            "wmape": None,
            "waste_rate": None,
            "waste_events_count": 0,
            "kpi_date": None
        }
    
    # Sort by date (newest first)
    sku_kpis.sort(key=lambda x: x.get("date", ""), reverse=True)
    latest = sku_kpis[0]
    
    # Also compute waste rate from ledger for lookback period
    waste_rate, waste_count = _compute_waste_rate(csv_layer, sku_id, asof_date, lookback_days=30)
    
    return {
        "oos_rate": float(latest["oos_rate"]) if latest.get("oos_rate") not in [None, "", "N/A"] else None,
        "wmape": float(latest["wmape"]) if latest.get("wmape") not in [None, "", "N/A"] else None,
        "waste_rate": waste_rate,
        "waste_events_count": waste_count,
        "kpi_date": latest.get("date")
    }


def _compute_waste_rate(csv_layer, sku_id: str, asof_date: datetime, lookback_days: int = 30) -> Tuple[Optional[float], int]:
    """
    Compute waste rate as (total WASTE qty) / (total sales qty) over lookback period.
    
    Args:
        csv_layer: CSVLayer instance
        sku_id: SKU identifier
        asof_date: Reference date (datetime)
        lookback_days: Period to analyze
    
    Returns:
        (waste_rate, waste_events_count) tuple
        waste_rate is None if no sales or waste data available
    """
    from datetime import timedelta
    from datetime import date as Date
    
    start_date = asof_date - timedelta(days=lookback_days)
    
    # Convert to date objects for comparison
    start_date_val = start_date.date() if isinstance(start_date, datetime) else start_date
    asof_date_val = asof_date.date() if isinstance(asof_date, datetime) else asof_date
    
    # Load transactions for this SKU
    all_txns = csv_layer.read_transactions()
    sku_txns = [
        t for t in all_txns
        if t.sku == sku_id and start_date_val <= t.date < asof_date_val
    ]
    
    # Sum WASTE events
    waste_qty = sum(abs(t.qty) for t in sku_txns if t.event.value == "WASTE")
    waste_count = sum(1 for t in sku_txns if t.event.value == "WASTE")
    
    # Sum sales
    sales = csv_layer.read_sales()
    sku_sales = [
        s for s in sales
        if s.sku == sku_id and start_date_val <= s.date < asof_date_val
    ]
    total_sales = sum(s.qty_sold for s in sku_sales)
    
    if total_sales == 0 or waste_qty == 0:
        return None, waste_count
    
    waste_rate = waste_qty / total_sales
    return waste_rate, waste_count


def _evaluate_decision(
    sku_id: str,
    sku_obj,
    current_csl: float,
    kpi_metrics: Dict[str, Any],
    guardrails: Dict[str, Any],
    csv_layer,
    asof_date: datetime
) -> ClosedLoopDecision:
    """
    Evaluate decision rules for a single SKU.
    
    Decision Logic:
    1. If WMAPE > threshold → BLOCKED (unreliable forecast)
    2. If OOS rate > threshold AND WMAPE OK → INCREASE CSL
    3. If waste rate > threshold (perishables only) AND sufficient events → DECREASE CSL
    4. Otherwise → HOLD
    
    All changes clamped to max_step and absolute min/max CSL bounds.
    
    Args:
        sku_id: SKU identifier
        sku_obj: SKU domain object
        current_csl: Current target CSL for this SKU
        kpi_metrics: Dict with oos_rate, wmape, waste_rate, waste_events_count
        guardrails: Dict with thresholds and limits
        csv_layer: For perishability check
        asof_date: Reference date
    
    Returns:
        ClosedLoopDecision with suggested_csl and reason
    """
    oos_rate = kpi_metrics.get("oos_rate")
    wmape = kpi_metrics.get("wmape")
    waste_rate = kpi_metrics.get("waste_rate")
    waste_count = kpi_metrics.get("waste_events_count", 0)
    
    max_step = guardrails["max_step"]
    oos_threshold = guardrails["oos_threshold"]
    wmape_threshold = guardrails["wmape_threshold"]
    waste_threshold = guardrails["waste_threshold"]
    min_waste_events = guardrails["min_waste_events"]
    min_csl = guardrails["min_csl"]
    max_csl = guardrails["max_csl"]
    
    # Default: hold
    suggested_csl = current_csl
    action = "hold"
    reason = "no_change_needed"
    guardrail_applied = None
    
    # Rule 1: Block if forecast unreliable (high WMAPE)
    if wmape is not None and wmape > wmape_threshold:
        action = "blocked"
        reason = "dati_forecast_instabili"
        guardrail_applied = "wmape_threshold"
        
        return ClosedLoopDecision(
            sku=sku_id,
            current_csl=current_csl,
            suggested_csl=current_csl,
            delta_csl=0.0,
            action=action,
            reason=reason,
            oos_rate=oos_rate,
            wmape=wmape,
            waste_rate=waste_rate,
            waste_events_count=waste_count,
            guardrail_applied=guardrail_applied
        )
    
    # Rule 2: Increase CSL if high OOS rate
    if oos_rate is not None and oos_rate > oos_threshold:
        action = "increase"
        suggested_csl = current_csl + max_step
        reason = f"oos_rate_alto_{oos_rate:.2%}"
        
        # Clamp to max
        if suggested_csl > max_csl:
            suggested_csl = max_csl
            guardrail_applied = "max_csl_absolute"
    
    # Rule 3: Decrease CSL if high waste (perishables only)
    # Check if SKU is perishable (shelf_life <= 7 days or cluster perishable)
    is_perishable = (
        (sku_obj.shelf_life_days is not None and sku_obj.shelf_life_days <= 7)
        or sku_obj.demand_variability == "PERISHABLE"
    )
    
    if (is_perishable and 
        waste_rate is not None and 
        waste_rate > waste_threshold and 
        waste_count >= min_waste_events):
        # Only suggest decrease if not already suggesting increase
        if action != "increase":
            action = "decrease"
            suggested_csl = current_csl - max_step
            reason = f"waste_rate_alto_{waste_rate:.2%}_perishable"
            
            # Clamp to min
            if suggested_csl < min_csl:
                suggested_csl = min_csl
                guardrail_applied = "min_csl_absolute"
        else:
            # Conflict: high OOS and high waste - prioritize OOS reduction
            reason += "_waste_ignored_priorita_oos"
    
    # Compute delta
    delta_csl = suggested_csl - current_csl
    
    return ClosedLoopDecision(
        sku=sku_id,
        current_csl=current_csl,
        suggested_csl=suggested_csl,
        delta_csl=delta_csl,
        action=action,
        reason=reason,
        oos_rate=oos_rate,
        wmape=wmape,
        waste_rate=waste_rate,
        waste_events_count=waste_count,
        guardrail_applied=guardrail_applied
    )
