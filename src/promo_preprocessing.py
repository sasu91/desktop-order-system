"""
Promo data preprocessing module.

Prepares sales data for promo uplift estimation by:
1. Censoring non-informative days (stock-outs, assortment gaps)
2. Separating promo vs non-promo observations
3. Providing clean training datasets for promo effect analysis

Design philosophy:
- Uses only promo_flag (binary: 0=no promo, 1=promo)
- NO price, promo type, or visibility data
- Does NOT modify core reorder logic
- Transparent logging of exclusionsExcluded days:
- Stock-out days (OH=0 and sales=0)
- Assortment-out periods (ASSORTMENT_OUT events)
- Days with UNFULFILLED events (recent inevasi)
"""
import logging
from datetime import date, timedelta
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass

from .domain.models import SalesRecord, Transaction, EventType
from .domain.ledger import is_day_censored


logger = logging.getLogger(__name__)


@dataclass
class PromoDataset:
    """Clean dataset for promo uplift analysis."""
    sku: str
    promo_observations: List[SalesRecord]      # promo_flag=1, not censored
    non_promo_observations: List[SalesRecord]  # promo_flag=0, not censored
    total_days_available: int                  # Total days in raw data
    censored_days_count: int                   # Days excluded (OOS, assortment gaps, etc.)
    censored_reasons: Dict[str, int]           # Breakdown by reason


def prepare_promo_training_data(
    sku: str,
    sales_records: List[SalesRecord],
    transactions: List[Transaction],
    lookback_days: int = 365,
    oos_lookback_days: int = 3,
    asof_date: Optional[date] = None,
) -> PromoDataset:
    """
    Prepare clean training dataset for promo uplift estimation.
    
    Filters out non-informative days and separates promo vs non-promo observations.
    
    Process:
    1. Filter sales_records for SKU within lookback window
    2. For each day, check if censored (OOS, assortment gaps, inevasi)
    3. Separate valid days into promo (promo_flag=1) and non-promo (promo_flag=0)
    4. Log censoring statistics
    
    Args:
        sku: SKU identifier
        sales_records: All sales records (with promo_flag)
        transactions: All ledger transactions (for censoring logic)
        lookback_days: Historical window for training data (default 365 days)
        oos_lookback_days: Lookback for UNFULFILLED detection (default 3 days)
        asof_date: Reference date (default: today)
    
    Returns:
        PromoDataset with separated promo/non-promo observations and censoring stats
    
    Example:
        >>> dataset = prepare_promo_training_data(
        ...     sku="SKU001",
        ...     sales_records=sales,
        ...     transactions=txns,
        ...     lookback_days=180
        ... )
        >>> print(f"Promo days: {len(dataset.promo_observations)}")
        >>> print(f"Non-promo days: {len(dataset.non_promo_observations)}")
        >>> print(f"Censored: {dataset.censored_days_count} ({dataset.censored_reasons})")
    """
    if asof_date is None:
        asof_date = date.today()
    
    # Calculate lookback window
    start_date = asof_date - timedelta(days=lookback_days)
    
    # Filter sales for this SKU within window
    sku_sales = [
        s for s in sales_records
        if s.sku == sku and start_date <= s.date < asof_date
    ]
    
    # Sort by date for consistent processing
    sku_sales.sort(key=lambda s: s.date)
    
    # Initialize containers
    promo_obs = []
    non_promo_obs = []
    censored_reasons: Dict[str, int] = {}
    
    # Process each sales record
    for sale in sku_sales:
        # Check if day is censored (non-informative)
        is_censored, reason = is_day_censored(
            sku=sku,
            check_date=sale.date,
            transactions=transactions,
            sales_records=sales_records,
            lookback_days=oos_lookback_days,
        )
        
        if is_censored:
            # Count censored days by reason
            reason_key = reason.split(" on ")[0]  # Extract reason type
            censored_reasons[reason_key] = censored_reasons.get(reason_key, 0) + 1
            logger.debug(f"[{sku}] Censored day {sale.date}: {reason}")
            continue
        
        # Valid observation - classify by promo_flag
        if sale.promo_flag == 1:
            promo_obs.append(sale)
        else:
            non_promo_obs.append(sale)
    
    # Calculate statistics
    total_days = len(sku_sales)
    censored_count = total_days - len(promo_obs) - len(non_promo_obs)
    
    # Log summary
    logger.info(
        f"[{sku}] Promo preprocessing complete: "
        f"{len(promo_obs)} promo days, "
        f"{len(non_promo_obs)} non-promo days, "
        f"{censored_count} censored days ({censored_count/total_days*100:.1f}%)"
    )
    
    if censored_reasons:
        logger.info(f"[{sku}] Censored breakdown: {censored_reasons}")
    
    return PromoDataset(
        sku=sku,
        promo_observations=promo_obs,
        non_promo_observations=non_promo_obs,
        total_days_available=total_days,
        censored_days_count=censored_count,
        censored_reasons=censored_reasons,
    )


def estimate_promo_uplift_simple(
    dataset: PromoDataset,
    min_promo_days: int = 10,
    min_non_promo_days: int = 30,
) -> Optional[Dict[str, float]]:
    """
    Estimate promo uplift using simple average comparison.
    
    Uplift = (avg_promo_sales - avg_non_promo_sales) / avg_non_promo_sales
    
    Args:
        dataset: PromoDataset with separated observations
        min_promo_days: Minimum promo observations required (default 10)
        min_non_promo_days: Minimum non-promo observations required (default 30)
    
    Returns:
        Dict with uplift metrics or None if insufficient data:
        {
            "avg_promo_sales": float,
            "avg_non_promo_sales": float,
            "uplift_percent": float,  # % increase during promo
            "n_promo_days": int,
            "n_non_promo_days": int,
        }
    
    Example:
        >>> uplift = estimate_promo_uplift_simple(dataset)
        >>> if uplift:
        ...     print(f"Promo uplift: +{uplift['uplift_percent']:.1f}%")
    """
    # Validate sufficient data
    n_promo = len(dataset.promo_observations)
    n_non_promo = len(dataset.non_promo_observations)
    
    if n_promo < min_promo_days or n_non_promo < min_non_promo_days:
        logger.warning(
            f"[{dataset.sku}] Insufficient data for uplift estimation: "
            f"{n_promo} promo days (need {min_promo_days}), "
            f"{n_non_promo} non-promo days (need {min_non_promo_days})"
        )
        return None
    
    # Calculate averages
    avg_promo = sum(s.qty_sold for s in dataset.promo_observations) / n_promo
    avg_non_promo = sum(s.qty_sold for s in dataset.non_promo_observations) / n_non_promo
    
    # Calculate uplift (avoid division by zero)
    if avg_non_promo == 0:
        logger.warning(f"[{dataset.sku}] Cannot calculate uplift: avg_non_promo_sales = 0")
        return None
    
    uplift_percent = (avg_promo - avg_non_promo) / avg_non_promo * 100
    
    logger.info(
        f"[{dataset.sku}] Promo uplift: +{uplift_percent:.1f}% "
        f"({avg_promo:.1f} vs {avg_non_promo:.1f} units/day, "
        f"n_promo={n_promo}, n_non_promo={n_non_promo})"
    )
    
    return {
        "avg_promo_sales": avg_promo,
        "avg_non_promo_sales": avg_non_promo,
        "uplift_percent": uplift_percent,
        "n_promo_days": n_promo,
        "n_non_promo_days": n_non_promo,
    }


def get_promo_summary_stats(
    sku: str,
    sales_records: List[SalesRecord],
    lookback_days: int = 90,
    asof_date: Optional[date] = None,
) -> Dict[str, any]:
    """
    Get summary statistics for promo activity (no censoring, quick overview).
    
    Args:
        sku: SKU identifier
        sales_records: All sales records
        lookback_days: Historical window (default 90 days)
        asof_date: Reference date (default: today)
    
    Returns:
        Dict with promo summary:
        {
            "total_days": int,
            "promo_days": int,
            "non_promo_days": int,
            "promo_frequency": float,  # % of days on promo
            "avg_promo_sales": float,
            "avg_non_promo_sales": float,
        }
    
    Example:
        >>> stats = get_promo_summary_stats("SKU001", sales)
        >>> print(f"Promo frequency: {stats['promo_frequency']:.1f}%")
    """
    if asof_date is None:
        asof_date = date.today()
    
    start_date = asof_date - timedelta(days=lookback_days)
    
    # Filter sales for SKU within window
    sku_sales = [
        s for s in sales_records
        if s.sku == sku and start_date <= s.date < asof_date
    ]
    
    if not sku_sales:
        return {
            "total_days": 0,
            "promo_days": 0,
            "non_promo_days": 0,
            "promo_frequency": 0.0,
            "avg_promo_sales": 0.0,
            "avg_non_promo_sales": 0.0,
        }
    
    # Separate promo vs non-promo
    promo_sales = [s for s in sku_sales if s.promo_flag == 1]
    non_promo_sales = [s for s in sku_sales if s.promo_flag == 0]
    
    # Calculate stats
    n_promo = len(promo_sales)
    n_non_promo = len(non_promo_sales)
    total = len(sku_sales)
    
    avg_promo = sum(s.qty_sold for s in promo_sales) / n_promo if n_promo > 0 else 0.0
    avg_non_promo = sum(s.qty_sold for s in non_promo_sales) / n_non_promo if n_non_promo > 0 else 0.0
    promo_freq = n_promo / total * 100 if total > 0 else 0.0
    
    return {
        "total_days": total,
        "promo_days": n_promo,
        "non_promo_days": n_non_promo,
        "promo_frequency": promo_freq,
        "avg_promo_sales": avg_promo,
        "avg_non_promo_sales": avg_non_promo,
    }
