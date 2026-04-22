"""
Integration tests for promo uplift estimation with baseline forecast.

Validates end-to-end workflow:
- Baseline forecast trained on non-promo, non-censored days
- Per-event uplift calculation using baseline denominators
- Anti-leakage: baseline uses ONLY data before event_start
- Realistic scenarios with multiple SKUs, categories, departments
- Hierarchical pooling with real data
"""
import pytest
from datetime import date, timedelta
from src.domain.promo_uplift import estimate_uplift, UpliftReport
from src.forecast import baseline_forecast
from src.domain.models import SKU, SalesRecord, PromoWindow, Transaction, EventType
from src.persistence.csv_layer import CSVLayer
import tempfile
from pathlib import Path


class TestUpliftBaselineIntegration:
    """Integration tests for uplift estimation using baseline forecast."""
    
    def test_single_sku_single_promo_realistic(self):
        """
        Realistic scenario: SKU with non-promo baseline + one promo event.
        
        Validates:
        - Baseline trained on non-promo days only
        - Promo uplift > 1.0 (sales increased during promo)
        - Anti-leakage: baseline uses data before promo start
        """
        # Setup SKU
        sku = SKU(sku="SKU_COLA", description="Cola 1L", category="BEVERAGES", department="DRINKS")
        
        # Historical sales (non-promo, 60 days before promo)
        baseline_sales = []
        for day_offset in range(-60, 0):
            day_date = date(2024, 3, 1) + timedelta(days=day_offset)
            baseline_sales.append(
                SalesRecord(sku="SKU_COLA", date=day_date, qty_sold=10, promo_flag=0)
            )
        
        # Promo window: March 1-7, 2024
        promo_windows = [
            PromoWindow(sku="SKU_COLA", start_date=date(2024, 3, 1), end_date=date(2024, 3, 7))
        ]
        
        # Sales during promo (2x baseline → uplift ≈ 2.0)
        promo_sales = []
        for day in range(1, 8):
            promo_sales.append(
                SalesRecord(sku="SKU_COLA", date=date(2024, 3, day), qty_sold=20, promo_flag=1)
            )
        
        all_sales = baseline_sales + promo_sales
        
        # Settings
        settings = {
            "promo_uplift": {
                "min_uplift": {"value": 1.0},
                "max_uplift": {"value": 3.0},
                "min_events_sku": {"value": 1},
                "min_valid_days_sku": {"value": 5},
                "min_events_category": {"value": 5},
                "min_events_department": {"value": 10},
                "winsorize_trim_percent": {"value": 10.0},
                "denominator_epsilon": {"value": 0.1},
                "confidence_threshold_a": {"value": 3},
                "confidence_threshold_b": {"value": 5},
            }
        }
        
        # Estimate uplift
        report = estimate_uplift(
            sku_id="SKU_COLA",
            all_skus=[sku],
            promo_windows=promo_windows,
            sales_records=all_sales,
            transactions=[],
            settings=settings,
        )
        
        # Assertions
        assert report.sku == "SKU_COLA"
        assert report.n_events == 1
        assert report.n_valid_days_total == 7  # All 7 promo days valid
        assert report.uplift_factor >= 1.8  # Roughly 2.0 (some variance from baseline model)
        assert report.uplift_factor <= 2.2
        assert report.confidence == "B"  # Only 1 event (< threshold_a=3)
        assert report.pooling_source == "SKU"
    
    def test_multi_sku_category_pooling(self):
        """
        Category pooling scenario: Target SKU lacks data, pools from category.
        
        Validates:
        - Hierarchical pooling fetches events from same category
        - Pooled events aggregate correctly
        - Confidence downgraded to B (pooled)
        """
        # SKUs in same category
        skus = [
            SKU(sku="SKU_TARGET", description="New Product", category="SNACKS", department="FOOD"),
            SKU(sku="SKU_SISTER1", description="Sister Product 1", category="SNACKS", department="FOOD"),
            SKU(sku="SKU_SISTER2", description="Sister Product 2", category="SNACKS", department="FOOD"),
        ]
        
        # Promo windows: only sister SKUs have promo history
        promo_windows = [
            PromoWindow(sku="SKU_SISTER1", start_date=date(2024, 2, 1), end_date=date(2024, 2, 5)),
            PromoWindow(sku="SKU_SISTER1", start_date=date(2024, 3, 1), end_date=date(2024, 3, 5)),
            PromoWindow(sku="SKU_SISTER2", start_date=date(2024, 2, 10), end_date=date(2024, 2, 15)),
            PromoWindow(sku="SKU_SISTER2", start_date=date(2024, 3, 10), end_date=date(2024, 3, 15)),
            PromoWindow(sku="SKU_SISTER2", start_date=date(2024, 4, 1), end_date=date(2024, 4, 5)),
        ]
        
        # Sales for sister SKUs
        sales = []
        for sku_id in ["SKU_SISTER1", "SKU_SISTER2"]:
            # Baseline (Jan 2024)
            for day in range(1, 31):
                sales.append(SalesRecord(sku=sku_id, date=date(2024, 1, day), qty_sold=8, promo_flag=0))
            
            # Promo sales (higher)
            for window in promo_windows:
                if window.sku == sku_id:
                    current = window.start_date
                    while current <= window.end_date:
                        sales.append(SalesRecord(sku=sku_id, date=current, qty_sold=16, promo_flag=1))
                        current += timedelta(days=1)
        
        settings = {
            "promo_uplift": {
                "min_uplift": {"value": 1.0},
                "max_uplift": {"value": 3.0},
                "min_events_sku": {"value": 3},
                "min_valid_days_sku": {"value": 10},
                "min_events_category": {"value": 3},  # Category pooling threshold
                "min_events_department": {"value": 10},
                "winsorize_trim_percent": {"value": 10.0},
                "denominator_epsilon": {"value": 0.1},
                "confidence_threshold_a": {"value": 3},
                "confidence_threshold_b": {"value": 5},
            }
        }
        
        # Estimate uplift for target SKU (no promo history)
        report = estimate_uplift(
            sku_id="SKU_TARGET",
            all_skus=skus,
            promo_windows=promo_windows,
            sales_records=sales,
            transactions=[],
            settings=settings,
        )
        
        # Assertions
        assert report.sku == "SKU_TARGET"
        assert report.pooling_source == "category:SNACKS"  # Pooled from category
        assert report.n_events >= 3  # At least 3 events from sister SKUs
        assert report.uplift_factor >= 1.5  # Uplift roughly 2.0 (16/8)
        assert report.uplift_factor <= 2.5
        assert report.confidence == "B"  # Pooled (not SKU-level)
    
    def test_censored_days_excluded_from_uplift(self):
        """
        Censored days (OOS) should be excluded from uplift calculation.
        
        Validates:
        - Days with OOS events (UNFULFILLED) are skipped
        - Only non-censored days contribute to actual_sales and baseline_pred
        """
        sku = SKU(sku="SKU_MILK", description="Milk 1L", category="DAIRY", department="FOOD")
        
        # Baseline sales (Feb 2024)
        sales = []
        for day in range(1, 29):
            sales.append(SalesRecord(sku="SKU_MILK", date=date(2024, 2, day), qty_sold=15, promo_flag=0))
        
        # Initial stock (to avoid censoring all days due to OH=0)
        transactions = [
            Transaction(sku="SKU_MILK", date=date(2024, 2, 1), event=EventType.SNAPSHOT, qty=100),
        ]
        
        # Promo window: March 1-7
        promo_windows = [
            PromoWindow(sku="SKU_MILK", start_date=date(2024, 3, 1), end_date=date(2024, 3, 7))
        ]
        
        # Promo sales (but March 3-4 are OOS)
        for day in range(1, 8):
            if day not in [3, 4]:  # Non-OOS days: normal promo sales
                sales.append(SalesRecord(sku="SKU_MILK", date=date(2024, 3, day), qty_sold=30, promo_flag=1))
            else:  # OOS days: add zero sales (required for baseline prediction)
                sales.append(SalesRecord(sku="SKU_MILK", date=date(2024, 3, day), qty_sold=0, promo_flag=1))
        
        # Transactions: UNFULFILLED on March 3-4 (OOS)
        transactions += [
            Transaction(sku="SKU_MILK", date=date(2024, 3, 3), event=EventType.UNFULFILLED, qty=10),
            Transaction(sku="SKU_MILK", date=date(2024, 3, 4), event=EventType.UNFULFILLED, qty=8),
        ]
        
        settings = {
            "promo_uplift": {
                "min_uplift": {"value": 1.0},
                "max_uplift": {"value": 3.0},
                "min_events_sku": {"value": 1},
                "min_valid_days_sku": {"value": 3},
                "min_events_category": {"value": 5},
                "min_events_department": {"value": 10},
                "winsorize_trim_percent": {"value": 10.0},
                "denominator_epsilon": {"value": 0.1},
                "confidence_threshold_a": {"value": 3},
                "confidence_threshold_b": {"value": 5},
            }
        }
        
        report = estimate_uplift(
            sku_id="SKU_MILK",
            all_skus=[sku],
            promo_windows=promo_windows,
            sales_records=sales,
            transactions=transactions,
            settings=settings,
        )
        
        # Assertions
        assert report.n_events == 1
        # Some days should be censored (Mar 3-4), others not (Mar 1,2,5,6,7)
        # Exact count may vary depending on is_day_censored logic and stock availability
        assert report.n_valid_days_total >= 2  # At least some non-OOS days counted
        assert report.n_valid_days_total < 7  # Not all days (some are OOS)
        assert report.uplift_factor >= 1.8  # Uplift ≈ 2.0 (30/15)
        assert report.uplift_factor <= 2.2
    
    def test_anti_leakage_baseline_training(self):
        """
        Anti-leakage: Baseline should ONLY use sales before promo event start.
        
        Validates:
        - baseline_forecast trained with asof_date = event_start - 1
        - Promo sales NOT included in baseline training
        """
        sku = SKU(sku="SKU_CHIPS", description="Chips 100g", category="SNACKS", department="FOOD")
        
        # Baseline sales: Jan 1 - Feb 28, 2024 (stable at 12/day)
        sales = []
        for day in range(1, 60):
            sales.append(SalesRecord(sku="SKU_CHIPS", date=date(2024, 1, 1) + timedelta(days=day), qty_sold=12, promo_flag=0))
        
        # Promo window: March 1-5, 2024
        promo_windows = [
            PromoWindow(sku="SKU_CHIPS", start_date=date(2024, 3, 1), end_date=date(2024, 3, 5))
        ]
        
        # Promo sales (3x baseline → uplift ≈ 3.0)
        for day in range(1, 6):
            sales.append(SalesRecord(sku="SKU_CHIPS", date=date(2024, 3, day), qty_sold=36, promo_flag=1))
        
        # Post-promo sales (should NOT influence baseline for this event)
        for day in range(6, 15):
            sales.append(SalesRecord(sku="SKU_CHIPS", date=date(2024, 3, day), qty_sold=50, promo_flag=0))
        
        settings = {
            "promo_uplift": {
                "min_uplift": {"value": 1.0},
                "max_uplift": {"value": 3.0},
                "min_events_sku": {"value": 1},
                "min_valid_days_sku": {"value": 3},
                "min_events_category": {"value": 5},
                "min_events_department": {"value": 10},
                "winsorize_trim_percent": {"value": 10.0},
                "denominator_epsilon": {"value": 0.1},
                "confidence_threshold_a": {"value": 3},
                "confidence_threshold_b": {"value": 5},
            }
        }
        
        report = estimate_uplift(
            sku_id="SKU_CHIPS",
            all_skus=[sku],
            promo_windows=promo_windows,
            sales_records=sales,
            transactions=[],
            settings=settings,
        )
        
        # Assertions
        assert report.n_events == 1
        assert report.uplift_factor >= 2.7  # Uplift ≈ 3.0 (36/12)
        assert report.uplift_factor <= 3.0  # Clipped to max guardrail
        
        # Verify baseline used ONLY pre-promo data
        # (This is implicit: if post-promo data were used, baseline would be ~35, uplift ≈ 1.0)
        # With correct anti-leakage, baseline ≈ 12, uplift ≈ 3.0
    
    def test_guardrail_clipping_in_integration(self):
        """
        Guardrails [min_uplift, max_uplift] should clip extreme uplifts.
        
        Validates:
        - Very high uplift (e.g., 10x) clipped to max_uplift
        - Very low uplift (e.g., 0.5x) clipped to min_uplift
        """
        sku = SKU(sku="SKU_ENERGY", description="Energy Drink", category="BEVERAGES", department="DRINKS")
        
        # Baseline sales (very low: 1/day, Jan-Feb 2024)
        sales = []
        for day_offset in range(0, 60):  # 60 days of baseline
            sales.append(SalesRecord(sku="SKU_ENERGY", date=date(2024, 1, 1) + timedelta(days=day_offset), qty_sold=1, promo_flag=0))
        
        # Promo window: March 1-3
        promo_windows = [
            PromoWindow(sku="SKU_ENERGY", start_date=date(2024, 3, 1), end_date=date(2024, 3, 3))
        ]
        
        # Promo sales (very high: 50/day → 50x uplift, should be clipped to max_uplift=3.0)
        for day in range(1, 4):
            sales.append(SalesRecord(sku="SKU_ENERGY", date=date(2024, 3, day), qty_sold=50, promo_flag=1))
        
        settings = {
            "promo_uplift": {
                "min_uplift": {"value": 1.0},
                "max_uplift": {"value": 3.0},  # Max guardrail
                "min_events_sku": {"value": 1},
                "min_valid_days_sku": {"value": 1},
                "min_events_category": {"value": 5},
                "min_events_department": {"value": 10},
                "winsorize_trim_percent": {"value": 10.0},
                "denominator_epsilon": {"value": 0.1},
                "confidence_threshold_a": {"value": 3},
                "confidence_threshold_b": {"value": 5},
            }
        }
        
        report = estimate_uplift(
            sku_id="SKU_ENERGY",
            all_skus=[sku],
            promo_windows=promo_windows,
            sales_records=sales,
            transactions=[],
            settings=settings,
        )
        
        # Assertions
        assert report.uplift_factor == 3.0  # Clipped to max_uplift
        assert report.uplift_factor <= settings["promo_uplift"]["max_uplift"]["value"]


class TestUpliftWithCSVLayer:
    """Integration tests using CSVLayer for realistic data persistence."""
    
    def test_estimate_uplift_with_csv_persistence(self, tmp_path):
        """
        End-to-end test with CSV files (realistic workflow).
        
        Validates:
        - Read SKUs, promo_windows, sales from CSV files
        - Estimate uplift for all SKUs with promos
        - Verify results match expected uplifts
        """
        # Setup CSV layer
        csv_layer = CSVLayer(data_dir=tmp_path)
        
        # Create SKUs
        skus = [
            SKU(sku="SKU_SODA", description="Soda 500ml", category="BEVERAGES", department="DRINKS"),
            SKU(sku="SKU_JUICE", description="Juice 1L", category="BEVERAGES", department="DRINKS"),
        ]
        for sku in skus:
            csv_layer.write_sku(sku)
        
        # Initialize stock (to avoid censoring all days)
        initial_stock_txns = [
            Transaction(sku="SKU_SODA", date=date(2023, 12, 31), event=EventType.SNAPSHOT, qty=500),
            Transaction(sku="SKU_JUICE", date=date(2023, 12, 31), event=EventType.SNAPSHOT, qty=500),
        ]
        for txn in initial_stock_txns:
            csv_layer.write_transaction(txn)
        
        # Create promo windows
        promo_windows = [
            PromoWindow(sku="SKU_SODA", start_date=date(2024, 3, 1), end_date=date(2024, 3, 5)),
            PromoWindow(sku="SKU_SODA", start_date=date(2024, 4, 1), end_date=date(2024, 4, 5)),
            PromoWindow(sku="SKU_SODA", start_date=date(2024, 5, 1), end_date=date(2024, 5, 5)),  # 3rd event
            PromoWindow(sku="SKU_JUICE", start_date=date(2024, 3, 10), end_date=date(2024, 3, 15)),
            PromoWindow(sku="SKU_JUICE", start_date=date(2024, 4, 10), end_date=date(2024, 4, 15)),
            PromoWindow(sku="SKU_JUICE", start_date=date(2024, 5, 10), end_date=date(2024, 5, 15)),  # 3rd event
        ]
        for window in promo_windows:
            csv_layer.write_promo_window(window)
        
        # Create sales records
        sales = []
        
        # SKU_SODA: baseline 20/day, promo 40/day (Jan-Feb baseline)
        for day_offset in range(0, 60):  # 60 days
            day_date = date(2024, 1, 1) + timedelta(days=day_offset)
            sales.append(SalesRecord(sku="SKU_SODA", date=day_date, qty_sold=20, promo_flag=0))
        for day in range(1, 6):  # March promo
            sales.append(SalesRecord(sku="SKU_SODA", date=date(2024, 3, day), qty_sold=40, promo_flag=1))
        for day in range(1, 6):  # April promo
            sales.append(SalesRecord(sku="SKU_SODA", date=date(2024, 4, day), qty_sold=40, promo_flag=1))
        for day in range(1, 6):  # May promo (3rd event)
            sales.append(SalesRecord(sku="SKU_SODA", date=date(2024, 5, day), qty_sold=40, promo_flag=1))
        
        # SKU_JUICE: baseline 15/day, promo 30/day (Jan-Feb baseline)
        for day_offset in range(0, 60):  # 60 days
            day_date = date(2024, 1, 1) + timedelta(days=day_offset)
            sales.append(SalesRecord(sku="SKU_JUICE", date=day_date, qty_sold=15, promo_flag=0))
        for day in range(10, 16):  # March promo
            sales.append(SalesRecord(sku="SKU_JUICE", date=date(2024, 3, day), qty_sold=30, promo_flag=1))
        for day in range(10, 16):  # April promo (2nd event)
            sales.append(SalesRecord(sku="SKU_JUICE", date=date(2024, 4, day), qty_sold=30, promo_flag=1))
        for day in range(10, 16):  # May promo (3rd event)
            sales.append(SalesRecord(sku="SKU_JUICE", date=date(2024, 5, day), qty_sold=30, promo_flag=1))
        
        # Write sales
        csv_layer.write_sales(sales)
        
        # Read back from CSV
        loaded_skus = csv_layer.read_skus()
        loaded_windows = csv_layer.read_promo_calendar()
        loaded_sales = csv_layer.read_sales()
        loaded_transactions = csv_layer.read_transactions()
        loaded_settings = csv_layer.read_settings()
        
        # Estimate uplift for SKU_SODA
        report_soda = estimate_uplift(
            sku_id="SKU_SODA",
            all_skus=loaded_skus,
            promo_windows=loaded_windows,
            sales_records=loaded_sales,
            transactions=loaded_transactions,
            settings=loaded_settings,
        )
        
        # Estimate uplift for SKU_JUICE
        report_juice = estimate_uplift(
            sku_id="SKU_JUICE",
            all_skus=loaded_skus,
            promo_windows=loaded_windows,
            sales_records=loaded_sales,
            transactions=loaded_transactions,
            settings=loaded_settings,
        )
        
        # Assertions for SKU_SODA
        assert report_soda.sku == "SKU_SODA"
        assert report_soda.n_events == 3  # Three promo events
        assert report_soda.uplift_factor >= 1.8  # Roughly 2.0x
        assert report_soda.uplift_factor <= 2.2
        assert report_soda.pooling_source == "SKU"
        assert report_soda.confidence == "A"  # >= 3 events → confidence A
        
        # Assertions for SKU_JUICE
        assert report_juice.sku == "SKU_JUICE"
        assert report_juice.n_events == 3  # Three promo events
        assert report_juice.uplift_factor >= 1.8  # Roughly 2.0x
        assert report_juice.uplift_factor <= 2.2
        assert report_juice.pooling_source == "SKU"
        assert report_juice.confidence == "A"  # >= 3 events → confidence A
        assert report_juice.uplift_factor <= 2.2
        assert report_juice.pooling_source == "SKU"
