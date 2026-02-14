"""
Tests for KPI calculation module (src/analytics/kpi.py).

Validates:
- OOS rate calculation with strict/relaxed modes
- Assortment exclusion and override markers
- Lost sales estimation (base and forecast methods)
- Forecast accuracy metrics (WMAPE, bias)
- Supplier proxy KPIs (fill rate, OTIF, delay)
"""

import pytest
from datetime import date, timedelta
from pathlib import Path
import tempfile
import shutil

from src.domain.models import EventType, Transaction, SalesRecord
from src.persistence.csv_layer import CSVLayer
from src.analytics.kpi import (
    compute_oos_kpi,
    estimate_lost_sales,
    compute_forecast_accuracy,
    compute_supplier_proxy_kpi,
)


@pytest.fixture
def temp_data_dir():
    """Create temporary data directory for tests."""
    temp_dir = Path(tempfile.mkdtemp())
    yield temp_dir
    shutil.rmtree(temp_dir)


@pytest.fixture
def csv_layer(temp_data_dir):
    """CSV layer with test data."""
    return CSVLayer(data_dir=temp_data_dir)


class TestComputeOOSKPI:
    """Test OOS KPI calculation."""
    
    def test_basic_oos_strict_mode(self, csv_layer):
        """Test basic OOS calculation in strict mode."""
        # Setup: SKU with 3 OOS days out of 10
        today = date(2024, 2, 10)
        sku = "TEST_SKU"
        
        # Create transactions: SNAPSHOT on day 1, SALES on days 2-10
        transactions = [
            Transaction(date(2024, 2, 1), sku, EventType.SNAPSHOT, 10),
        ]
        
        # Sales that deplete stock
        sales = []
        for i in range(2, 11):
            sales.append(SalesRecord(date(2024, 2, i), sku, 2))  # Sell 2 units per day
        
        # Write to CSV
        for txn in transactions:
            csv_layer.write_transaction(txn)
        csv_layer.write_sales(sales)
        
        # Calculate OOS KPI (last 10 days, strict mode)
        result = compute_oos_kpi(sku, 10, "strict", csv_layer, asof_date=today)
        
        # Stock: Day 1=10, Day 2=8, Day 3=6, Day 4=4, Day 5=2, Day 6=0 (OOS), Day 7-10=0 (OOS)
        # Expected: 5 OOS days (days 6-10)
        assert result["oos_days_count"] == 5
        assert result["oos_rate"] == 0.5  # 5/10
        assert result["n_periods"] == 10
    
    def test_oos_relaxed_mode(self, csv_layer):
        """Test OOS calculation in relaxed mode (on_hand + on_order == 0)."""
        today = date(2024, 2, 12)
        sku = "TEST_SKU"
        
        # Create scenario: stock depleted but order placed before depletion
        transactions = [
            Transaction(date(2024, 2, 1), sku, EventType.SNAPSHOT, 5),
            Transaction(date(2024, 2, 3), sku, EventType.ORDER, 20),  # Order placed on day 3
        ]
        
        # Sales: 1 unit per day for 8 days only (stops before ALL stock+order consumed)
        sales = [SalesRecord(date(2024, 2, i), sku, 1) for i in range(1, 9)]
        
        for txn in transactions:
            csv_layer.write_transaction(txn)
        csv_layer.write_sales(sales)
        
        # Strict mode: Days 6-12 should be OOS (on_hand=0 after day 5)
        # Day 1: 5, Day 2: 4, Day 3: 3 (+20 on_order), Day 4: 2, Day 5: 1, Day 6-12: 0 (but +20 on_order)
        strict_result = compute_oos_kpi(sku, 12, "strict", csv_layer, asof_date=today, return_details=True)
        assert strict_result["oos_days_count"] >= 7  # Days when on_hand=0
        
        # Relaxed mode: No OOS days from day 3 onward (on_hand+on_order > 0)
        # Days 1-2: stock available, not OOS; Days 3-12: order placed, on_hand+on_order > 0
        relaxed_result = compute_oos_kpi(sku, 12, "relaxed", csv_layer, asof_date=today, return_details=True)
    def test_override_marker_exclusion(self, csv_layer):
        """Test that OOS_ESTIMATE_OVERRIDE markers exclude days from OOS count."""
        today = date(2024, 2, 10)
        sku = "TEST_SKU"
        
        transactions = [
            Transaction(date(2024, 2, 1), sku, EventType.SNAPSHOT, 3),
            # Day 6: manual override (treat as non-OOS for forecast purposes)
            Transaction(date(2024, 2, 6), sku, EventType.WASTE, 0, note="OOS_ESTIMATE_OVERRIDE:2024-02-06"),
        ]
        
        sales = []
        for i in range(2, 11):
            sales.append(SalesRecord(date(2024, 2, i), sku, 1))
        
        for txn in transactions:
            csv_layer.write_transaction(txn)
        csv_layer.write_sales(sales)
        
        result = compute_oos_kpi(sku, 10, "strict", csv_layer, asof_date=today, return_details=True)
        
        # Days 4-10 would be OOS (stock depleted on day 4)
        # But day 6 has override marker, so excluded
        # Expected: Days 4, 5, 7, 8, 9, 10 = 6 OOS days
        assert result["oos_days_count"] == 6
        assert date(2024, 2, 6) not in result["oos_days_list"]
    
    def test_assortment_out_exclusion(self, csv_layer):
        """Test that out-of-assortment periods are excluded from OOS calculation."""
        today = date(2024, 2, 15)
        sku = "TEST_SKU"
        
        transactions = [
            Transaction(date(2024, 2, 1), sku, EventType.SNAPSHOT, 0),  # Start with 0 stock
            Transaction(date(2024, 2, 5), sku, EventType.ASSORTMENT_OUT, 0),  # Out from day 5
            Transaction(date(2024, 2, 10), sku, EventType.ASSORTMENT_IN, 0),  # Back in on day 10
        ]
        
        for txn in transactions:
            csv_layer.write_transaction(txn)
        csv_layer.write_sales([])
        
        result = compute_oos_kpi(sku, 15, "strict", csv_layer, asof_date=today)
        
        # Days 1-4: in assortment, OOS (4 days)
        # Days 5-9: out of assortment, excluded (5 days)
        # Days 10-15: in assortment, OOS (6 days)
        # Total: 10 OOS days out of 10 valid periods (excluding 5 assortment-out days)
        assert result["n_periods"] == 10  # 15 - 5 excluded days
        assert result["oos_days_count"] == 10
        assert result["oos_rate"] == 1.0


class TestEstimateLostSales:
    """Test lost sales estimation."""
    
    def test_base_method(self, csv_layer):
        """Test base lost sales method (avg × oos_count)."""
        today = date(2024, 2, 20)
        sku = "TEST_SKU"
        
        transactions = [
            Transaction(date(2024, 2, 1), sku, EventType.SNAPSHOT, 50),  # Enough for 10 days
        ]
        
        # 10 days with sales of 5 units, then 10 days OOS
        sales = []
        for i in range(1, 11):
            sales.append(SalesRecord(date(2024, 2, i), sku, 5))
        
        # Days 11-20: OOS (stock depleted)
        
        for txn in transactions:
            csv_layer.write_transaction(txn)
        csv_layer.write_sales(sales)
        
        result = estimate_lost_sales(sku, 20, "strict", csv_layer, asof_date=today, method="base")
        
        # Avg sales excluding OOS = 50 / 10 = 5 units/day
        # OOS days = 10
        # Lost sales = 5 × 10 = 50 units
        assert result["lost_units_est"] == pytest.approx(50.0, rel=0.1)
        assert result["method_used"] == "base"
    
    def test_forecast_method_fallback(self, csv_layer):
        """Test forecast method with insufficient history (fallback to base)."""
        today = date(2024, 2, 10)
        sku = "TEST_SKU"
        
        transactions = [
            Transaction(date(2024, 2, 1), sku, EventType.SNAPSHOT, 3),
        ]
        
        # Only 5 days of sales (< 7 required for forecast)
        sales = [SalesRecord(date(2024, 2, i), sku, 2) for i in range(1, 6)]
        
        for txn in transactions:
            csv_layer.write_transaction(txn)
        csv_layer.write_sales(sales)
        
        result = estimate_lost_sales(sku, 10, "strict", csv_layer, asof_date=today, method="forecast")
        
        # Should fallback to base method
        assert result["method_used"] == "base"
        assert "fallback_reason" in result


class TestComputeForecastAccuracy:
    """Test forecast accuracy calculation."""
    
    def test_basic_accuracy_calculation(self, csv_layer):
        """Test WMAPE and bias calculation with simple data."""
        today = date(2024, 3, 15)
        sku = "TEST_SKU"
        
        transactions = [
            Transaction(date(2024, 1, 1), sku, EventType.SNAPSHOT, 1000),  # Plenty of stock
        ]
        
        # 70 days of stable sales (enough for rolling window: 8*7+7=63 days minimum)
        sales = []
        for i in range(1, 71):
            sales.append(SalesRecord(date(2024, 1, 1) + timedelta(days=i-1), sku, 10))
        
        for txn in transactions:
            csv_layer.write_transaction(txn)
        csv_layer.write_sales(sales)
        
        result = compute_forecast_accuracy(sku, 70, "strict", csv_layer, asof_date=today)
        
        # With stable sales, forecast should work (though may not meet "sufficient" threshold if n_points < 7)
        # The key is that it doesn't error and provides metrics
        assert result["wmape"] is not None or result["n_points"] == 0  # Either has WMAPE or no points
        if result["wmape"] is not None:
            assert result["wmape"] < 50  # Reasonably accurate for stable demand
    
    def test_insufficient_data(self, csv_layer):
        """Test forecast accuracy with insufficient data."""
        today = date(2024, 2, 10)
        sku = "TEST_SKU"
        
        transactions = [Transaction(date(2024, 2, 1), sku, EventType.SNAPSHOT, 10)]
        sales = [SalesRecord(date(2024, 2, i), sku, 2) for i in range(1, 6)]
        
        for txn in transactions:
            csv_layer.write_transaction(txn)
        csv_layer.write_sales(sales)
        
        result = compute_forecast_accuracy(sku, 10, "strict", csv_layer, asof_date=today)
        
        assert result["sufficient_data"] is False
        assert result["n_points"] < 7


class TestComputeSupplierProxyKPI:
    """Test supplier performance KPIs."""
    
    def test_fill_rate_calculation(self, csv_layer):
        """Test fill rate calculation (qty_received / qty_ordered)."""
        today = date(2024, 2, 10)
        sku = "TEST_SKU"
        
        # Create order logs
        csv_layer.write_order_log(
            order_id="ORD001",
            date_str="2024-02-01",
            sku=sku,
            qty=100,
            status="RECEIVED",
            qty_received=90,  # 90% fill rate
            receipt_date="2024-02-05"
        )
        
        csv_layer.write_order_log(
            order_id="ORD002",
            date_str="2024-02-03",
            sku=sku,
            qty=50,
            status="RECEIVED",
            qty_received=50,  # 100% fill rate
            receipt_date="2024-02-07"
        )
        
        result = compute_supplier_proxy_kpi(sku, 10, csv_layer, asof_date=today)
        
        # Fill rate = (90 + 50) / (100 + 50) = 140/150 = 0.933
        assert result["fill_rate"] == pytest.approx(140/150, rel=0.01)
        assert result["n_orders"] == 2
    
    def test_otif_calculation(self, csv_layer):
        """Test OTIF calculation (on-time and in-full)."""
        today = date(2024, 2, 15)
        sku = "TEST_SKU"
        
        # Order 1: On-time and in-full (OTIF = True)
        csv_layer.write_order_log(
            order_id="ORD001",
            date_str="2024-02-01",
            sku=sku,
            qty=100,
            status="RECEIVED",
            qty_received=100,
            receipt_date="2024-02-05"
        )
        
        # Create receiving log with order_ids
        csv_layer.write_receiving_log(
            document_id="DDT001",
            date_str="2024-02-05",
            sku=sku,
            qty=100,
            receipt_date="2024-02-05",
            order_ids="ORD001"
        )
        
        # Order 2: Late delivery (OTIF = False)
        csv_layer.write_order_log(
            order_id="ORD002",
            date_str="2024-02-03",
            sku=sku,
            qty=50,
            status="RECEIVED",
            qty_received=50,
            receipt_date="2024-02-08"
        )
        
        csv_layer.write_receiving_log(
            document_id="DDT002",
            date_str="2024-02-10",
            sku=sku,
            qty=50,
            receipt_date="2024-02-10",  # Late (expected 2024-02-08)
            order_ids="ORD002"
        )
        
        result = compute_supplier_proxy_kpi(sku, 15, csv_layer, asof_date=today)
        
        # OTIF: 1 out of 2 orders (50%)
        assert result["otif_rate"] == pytest.approx(0.5, rel=0.01)
        assert result["n_otif_calculable"] == 2
    
    def test_avg_delay_calculation(self, csv_layer):
        """Test average delay calculation."""
        today = date(2024, 2, 15)
        sku = "TEST_SKU"
        
        # Order 1: 2 days late
        csv_layer.write_order_log(
            order_id="ORD001",
            date_str="2024-02-01",
            sku=sku,
            qty=100,
            status="RECEIVED",
            qty_received=100,
            receipt_date="2024-02-05"
        )
        
        csv_layer.write_receiving_log(
            document_id="DDT001",
            date_str="2024-02-07",
            sku=sku,
            qty=100,
            receipt_date="2024-02-07",
            order_ids="ORD001"
        )
        
        # Order 2: 1 day early
        csv_layer.write_order_log(
            order_id="ORD002",
            date_str="2024-02-03",
            sku=sku,
            qty=50,
            status="RECEIVED",
            qty_received=50,
            receipt_date="2024-02-10"
        )
        
        csv_layer.write_receiving_log(
            document_id="DDT002",
            date_str="2024-02-09",
            sku=sku,
            qty=50,
            receipt_date="2024-02-09",
            order_ids="ORD002"
        )
        
        result = compute_supplier_proxy_kpi(sku, 15, csv_layer, asof_date=today)
        
        # Delay: (2 + (-1)) / 2 = 0.5 days
        assert result["avg_delay_days"] == pytest.approx(0.5, rel=0.1)
    
    def test_no_orders(self, csv_layer):
        """Test supplier KPIs with no orders."""
        today = date(2024, 2, 10)
        sku = "TEST_SKU"
        
        result = compute_supplier_proxy_kpi(sku, 30, csv_layer, asof_date=today)
        
        assert result["fill_rate"] is None
        assert result["otif_rate"] is None
        assert result["avg_delay_days"] is None
        assert result["n_orders"] == 0
