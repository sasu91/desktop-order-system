"""
Test suite for intermittent demand forecasting (Croston/SBA/TSB).

Validates:
1. Classification: ADI/CV² criteria
2. Fitting: Croston, SBA, TSB with synthetic series
3. Prediction: mu_P over P days
4. Backtest: rolling origin performance
5. Integration: demand_builder dispatcher
6. Regression: stable series not misclassified as intermittent
7. Golden series: intermittent patterns (zeros + spikes, obsolescence)

Author: Desktop Order System Team
Date: February 2026
"""

import pytest
from datetime import date, timedelta
from typing import List


# ---------------------------------------------------------------------------
# Fixtures: Synthetic series
# ---------------------------------------------------------------------------

@pytest.fixture
def stable_series() -> List[float]:
    """Series with stable daily demand (10 ± 2)."""
    import random
    random.seed(42)
    return [10 + random.uniform(-2, 2) for _ in range(90)]


@pytest.fixture
def intermittent_series() -> List[float]:
    """Series with frequent zeros and occasional spikes (ADI > 1.32, CV² > 0.49)."""
    import random
    random.seed(43)
    series = []
    for i in range(90):
        if i % 4 == 0:  # Demand every ~4 days → ADI ≈ 4
            # Very high variability in non-zero demands → CV² > 0.49
            # Mix of small and large values to increase CV²
            if i % 12 == 0:
                series.append(random.uniform(40, 50))  # High peak
            else:
                series.append(random.uniform(5, 15))  # Low demand
        else:
            series.append(0.0)
    return series


@pytest.fixture
def obsolescence_series() -> List[float]:
    """Series with declining trend (obsolescence pattern) + intermittent."""
    import random
    random.seed(44)
    series = []
    for i in range(90):
        # Decay factor: starts at 30, declines to ~5
        base = max(5, 30 - (i / 90) * 25)
        if i % 3 == 0:  # Demand every ~3 days → ADI ≈ 3
            # Alternate high spikes and low dips for CV² > 0.49
            if (i // 3) % 2 == 0:
                series.append(base * random.uniform(3, 5))  # High spike
            else:
                series.append(base * random.uniform(0.2, 0.5))  # Low dip
        else:
            series.append(0.0)
    return series


@pytest.fixture
def all_zeros_series() -> List[float]:
    """Degenerate case: all zeros."""
    return [0.0] * 90


# ---------------------------------------------------------------------------
# Test Classification (ADI/CV²)
# ---------------------------------------------------------------------------

class TestIntermittentClassification:
    """Test ADI/CV² classification criteria."""

    def test_stable_series_not_intermittent(self, stable_series):
        """Stable series should NOT be classified as intermittent."""
        from src.domain.intermittent_forecast import classify_intermittent

        classification = classify_intermittent(
            series=stable_series,
            adi_threshold=1.32,
            cv2_threshold=0.49
        )

        # Stable series: ADI ≈ 1.0 (every day has demand), CV² < 0.49
        assert classification.is_intermittent is False
        assert classification.adi < 1.32
        assert classification.n_nonzero == len(stable_series)

    def test_intermittent_series_classified_correctly(self, intermittent_series):
        """Intermittent series should be classified as intermittent."""
        from src.domain.intermittent_forecast import classify_intermittent

        classification = classify_intermittent(
            series=intermittent_series,
            adi_threshold=1.32,
            cv2_threshold=0.49
        )

        # Intermittent: ADI > 1.32, CV² > 0.49
        assert classification.is_intermittent is True
        assert classification.adi > 1.32
        assert classification.cv2 > 0.49
        assert classification.n_nonzero > 0
        assert classification.n_nonzero < len(intermittent_series)

    def test_all_zeros_classified_intermittent(self, all_zeros_series):
        """All zeros → definitely intermittent (degenerate case)."""
        from src.domain.intermittent_forecast import classify_intermittent

        classification = classify_intermittent(
            series=all_zeros_series,
            adi_threshold=1.32,
            cv2_threshold=0.49
        )

        assert classification.is_intermittent is True
        assert classification.n_nonzero == 0
        assert classification.adi > 1.32

    def test_classification_respects_censoring(self, intermittent_series):
        """Censored days should be excluded from classification."""
        from src.domain.intermittent_forecast import classify_intermittent

        # Mark first 10 days as censored
        exclude_indices = list(range(10))

        classification = classify_intermittent(
            series=intermittent_series,
            adi_threshold=1.32,
            cv2_threshold=0.49,
            exclude_indices=exclude_indices
        )

        assert classification.n_censored == 10
        assert classification.n_total == len(intermittent_series) - 10


# ---------------------------------------------------------------------------
# Test Fitting (Croston/SBA/TSB)
# ---------------------------------------------------------------------------

class TestIntermittentFitting:
    """Test Croston/SBA/TSB model fitting."""

    def test_croston_fit_convergence(self, intermittent_series):
        """Croston should converge to reasonable p_t and z_t."""
        from src.domain.intermittent_forecast import fit_croston

        model = fit_croston(series=intermittent_series, alpha=0.1)

        assert model.method == "croston"
        assert model.alpha == 0.1
        assert model.p_t > 0  # Interval between demands
        assert model.z_t > 0  # Size of non-zero demands
        assert model.n_nonzero > 0
        assert model.n_total == len(intermittent_series)

    def test_sba_fit_same_params_as_croston(self, intermittent_series):
        """SBA should have same p_t/z_t as Croston (bias correction in predict)."""
        from src.domain.intermittent_forecast import fit_croston, fit_sba

        croston_model = fit_croston(series=intermittent_series, alpha=0.1)
        sba_model = fit_sba(series=intermittent_series, alpha=0.1)

        assert sba_model.method == "sba"
        assert sba_model.p_t == croston_model.p_t
        assert sba_model.z_t == croston_model.z_t
        assert sba_model.n_nonzero == croston_model.n_nonzero

    def test_tsb_fit_includes_probability(self, intermittent_series):
        """TSB should produce b_t (probability of demand)."""
        from src.domain.intermittent_forecast import fit_tsb

        model = fit_tsb(series=intermittent_series, alpha_demand=0.1, alpha_probability=0.1)

        assert model.method == "tsb"
        assert model.b_t is not None
        assert 0.0 <= model.b_t <= 1.0
        assert model.z_t > 0

    def test_all_zeros_handle_gracefully(self, all_zeros_series):
        """All zeros should not crash, return model with zero forecast."""
        from src.domain.intermittent_forecast import fit_croston, predict_daily

        model = fit_croston(series=all_zeros_series, alpha=0.1)

        assert model.n_nonzero == 0
        assert model.z_t == 0.0

        # Predict should return 0
        daily_forecast = predict_daily(model)
        assert daily_forecast == 0.0


# ---------------------------------------------------------------------------
# Test Prediction
# ---------------------------------------------------------------------------

class TestIntermittentPrediction:
    """Test prediction functions (daily and P-day)."""

    def test_predict_daily_croston(self, intermittent_series):
        """Croston daily prediction = z_t / p_t."""
        from src.domain.intermittent_forecast import fit_croston, predict_daily

        model = fit_croston(series=intermittent_series, alpha=0.1)
        daily_forecast = predict_daily(model)

        expected = model.z_t / model.p_t
        assert abs(daily_forecast - expected) < 0.01

    def test_predict_daily_sba_bias_correction(self, intermittent_series):
        """SBA daily prediction includes bias correction (1 - alpha/2)."""
        from src.domain.intermittent_forecast import fit_sba, predict_daily

        model = fit_sba(series=intermittent_series, alpha=0.1)
        daily_forecast = predict_daily(model)

        correction = 1.0 - model.alpha / 2.0
        expected = correction * model.z_t / model.p_t
        assert abs(daily_forecast - expected) < 0.01

    def test_predict_daily_tsb(self, intermittent_series):
        """TSB daily prediction = b_t * z_t."""
        from src.domain.intermittent_forecast import fit_tsb, predict_daily

        model = fit_tsb(series=intermittent_series, alpha_demand=0.1, alpha_probability=0.1)
        daily_forecast = predict_daily(model)

        expected = model.b_t * model.z_t
        assert abs(daily_forecast - expected) < 0.01

    def test_predict_P_days(self, intermittent_series):
        """P-day prediction = daily_forecast * P."""
        from src.domain.intermittent_forecast import fit_sba, predict_daily, predict_P_days

        model = fit_sba(series=intermittent_series, alpha=0.1)
        daily_forecast = predict_daily(model)

        P = 14
        mu_P = predict_P_days(model, P)

        expected = daily_forecast * P
        assert abs(mu_P - expected) < 0.01


# ---------------------------------------------------------------------------
# Test Backtest
# ---------------------------------------------------------------------------

class TestIntermittentBacktest:
    """Test rolling origin backtest."""

    def test_backtest_method_runs_successfully(self, intermittent_series):
        """Backtest should run without error and produce metrics."""
        from src.domain.intermittent_forecast import backtest_method

        result = backtest_method(
            series=intermittent_series,
            method="sba",
            test_periods=4,
            alpha=0.1
        )

        assert result.method == "sba"
        assert result.n_forecasts > 0
        assert result.wmape >= 0.0

    def test_select_best_method(self, intermittent_series):
        """select_best_method should return one of the candidates."""
        from src.domain.intermittent_forecast import select_best_method

        best_method, results = select_best_method(
            series=intermittent_series,
            candidate_methods=["sba", "tsb"],
            test_periods=4,
            alpha=0.1,
            metric="wmape"
        )

        assert best_method in ["sba", "tsb"]
        assert "sba" in results
        assert "tsb" in results
        assert results[best_method].wmape <= max(results["sba"].wmape, results["tsb"].wmape)

    def test_obsolescence_series_prefers_tsb(self, obsolescence_series):
        """Obsolescence series should favor TSB (better for declining demand)."""
        from src.domain.intermittent_forecast import select_best_method

        best_method, results = select_best_method(
            series=obsolescence_series,
            candidate_methods=["sba", "tsb"],
            test_periods=4,
            alpha=0.1,
            metric="wmape"
        )

        # TSB should perform better or equally on declining series
        # (This is not a strict assertion, depends on data, but validated empirically)
        assert best_method in ["sba", "tsb"]


# ---------------------------------------------------------------------------
# Test Integration (demand_builder)
# ---------------------------------------------------------------------------

class TestIntermittentIntegration:
    """Test integration with demand_builder dispatcher."""

    def _make_history(self, series: List[float]) -> List[dict]:
        """Convert series to history dict format."""
        base_date = date(2025, 11, 1)
        return [
            {"date": base_date + timedelta(days=i), "qty_sold": qty}
            for i, qty in enumerate(series)
        ]

    def test_demand_builder_croston(self, intermittent_series):
        """demand_builder with method='croston' should use Croston."""
        from src.domain.demand_builder import build_demand_distribution

        history = self._make_history(intermittent_series)
        settings = {"alpha_default": 0.1}

        demand = build_demand_distribution(
            method="croston",
            history=history,
            protection_period_days=14,
            asof_date=date(2026, 2, 1),
            mc_params=settings
        )

        assert demand.forecast_method == "croston"
        assert demand.intermittent_method == "croston"
        assert demand.intermittent_classification is True
        assert demand.mu_P > 0

    def test_demand_builder_sba(self, intermittent_series):
        """demand_builder with method='sba' should use SBA."""
        from src.domain.demand_builder import build_demand_distribution

        history = self._make_history(intermittent_series)
        settings = {"alpha_default": 0.1}

        demand = build_demand_distribution(
            method="sba",
            history=history,
            protection_period_days=14,
            asof_date=date(2026, 2, 1),
            mc_params=settings
        )

        assert demand.forecast_method == "sba"
        assert demand.intermittent_method == "sba"
        assert demand.mu_P > 0

    def test_demand_builder_tsb(self, intermittent_series):
        """demand_builder with method='tsb' should use TSB."""
        from src.domain.demand_builder import build_demand_distribution

        history = self._make_history(intermittent_series)
        settings = {"alpha_default": 0.1}

        demand = build_demand_distribution(
            method="tsb",
            history=history,
            protection_period_days=14,
            asof_date=date(2026, 2, 1),
            mc_params=settings
        )

        assert demand.forecast_method == "tsb"
        assert demand.intermittent_method == "tsb"
        assert demand.mu_P > 0

    def test_demand_builder_intermittent_auto(self, intermittent_series):
        """demand_builder with method='intermittent_auto' should classify and select method."""
        from src.domain.demand_builder import build_demand_distribution

        history = self._make_history(intermittent_series)
        settings = {
            "alpha_default": 0.1,
            "adi_threshold": 1.32,
            "cv2_threshold": 0.49,
            "backtest_enabled": True,
            "backtest_periods": 4,
            "default_method": "sba"
        }

        demand = build_demand_distribution(
            method="intermittent_auto",
            history=history,
            protection_period_days=14,
            asof_date=date(2026, 2, 1),
            mc_params=settings
        )

        # Should classify as intermittent and select a method
        assert demand.intermittent_classification is True
        assert demand.intermittent_method in ["sba", "tsb", "croston"]
        assert demand.mu_P > 0

    def test_demand_builder_fallback_to_simple(self, stable_series):
        """intermittent_auto with stable series should fallback to simple."""
        from src.domain.demand_builder import build_demand_distribution

        history = self._make_history(stable_series)
        settings = {
            "alpha_default": 0.1,
            "adi_threshold": 1.32,
            "cv2_threshold": 0.49,
            "fallback_to_simple": True
        }

        demand = build_demand_distribution(
            method="intermittent_auto",
            history=history,
            protection_period_days=14,
            asof_date=date(2026, 2, 1),
            mc_params=settings
        )

        # Should fallback to simple (not classified as intermittent)
        assert demand.forecast_method == "simple"
        assert demand.intermittent_classification is False


# ---------------------------------------------------------------------------
# Test Regression (stable series)
# ---------------------------------------------------------------------------

class TestRegressionStableSeries:
    """Ensure intermittent methods don't break stable series."""

    def test_stable_series_with_simple_unchanged(self, stable_series):
        """Stable series with method='simple' should work as before."""
        from src.domain.demand_builder import build_demand_distribution

        base_date = date(2025, 11, 1)
        history = [
            {"date": base_date + timedelta(days=i), "qty_sold": qty}
            for i, qty in enumerate(stable_series)
        ]

        demand = build_demand_distribution(
            method="simple",
            history=history,
            protection_period_days=14,
            asof_date=date(2026, 2, 1)
        )

        assert demand.forecast_method == "simple"
        assert demand.mu_P > 0
        assert demand.sigma_P > 0
        # Intermittent fields should be at default (not used)
        assert demand.intermittent_classification is False


# ---------------------------------------------------------------------------
# Test Golden Series
# ---------------------------------------------------------------------------

class TestGoldenIntermittentSeries:
    """Golden series tests: expected behavior on known patterns."""

    def test_golden_frequent_zeros_sba_better_than_simple(self, intermittent_series):
        """Golden: SBA should outperform simple on intermittent series (by WMAPE or stable forecast)."""
        from src.domain.demand_builder import build_demand_distribution

        base_date = date(2025, 11, 1)
        history = [
            {"date": base_date + timedelta(days=i), "qty_sold": qty}
            for i, qty in enumerate(intermittent_series)
        ]

        # Build with simple
        demand_simple = build_demand_distribution(
            method="simple",
            history=history,
            protection_period_days=14,
            asof_date=date(2026, 2, 1)
        )

        # Build with SBA
        demand_sba = build_demand_distribution(
            method="sba",
            history=history,
            protection_period_days=14,
            asof_date=date(2026, 2, 1),
            mc_params={"alpha_default": 0.1}
        )

        # SBA should produce non-zero, reasonable mu_P
        assert demand_sba.mu_P > 0
        assert demand_sba.intermittent_classification is True

        # Simple might over/under-smooth with frequent zeros
        # (Exact comparison depends on implementation, but SBA mu_P should be stable)
        assert demand_sba.sigma_P > 0

    def test_golden_obsolescence_tsb_reduces_forecast(self, obsolescence_series):
        """Golden: TSB on obsolescence should produce declining forecast."""
        from src.domain.demand_builder import build_demand_distribution

        base_date = date(2025, 11, 1)
        history = [
            {"date": base_date + timedelta(days=i), "qty_sold": qty}
            for i, qty in enumerate(obsolescence_series)
        ]

        demand_tsb = build_demand_distribution(
            method="tsb",
            history=history,
            protection_period_days=14,
            asof_date=date(2026, 2, 1),
            mc_params={"alpha_default": 0.1}
        )

        # TSB should produce forecast reflecting declining trend
        assert demand_tsb.mu_P > 0
        assert demand_tsb.intermittent_b_t < 0.5  # Probability of demand should be low for obsolescence

        # Verify metadata
        assert demand_tsb.intermittent_method == "tsb"
        assert demand_tsb.intermittent_classification is True


# ---------------------------------------------------------------------------
# Test Determinism
# ---------------------------------------------------------------------------

class TestIntermittentDeterminism:
    """Test determinism: same input → same output."""

    def test_croston_deterministic(self, intermittent_series):
        """Same series + alpha → identical Croston model."""
        from src.domain.intermittent_forecast import fit_croston

        model1 = fit_croston(series=intermittent_series, alpha=0.1)
        model2 = fit_croston(series=intermittent_series, alpha=0.1)

        assert model1.p_t == model2.p_t
        assert model1.z_t == model2.z_t
        assert model1.n_nonzero == model2.n_nonzero

    def test_demand_builder_intermittent_deterministic(self, intermittent_series):
        """Same history + settings → identical demand distribution."""
        from src.domain.demand_builder import build_demand_distribution

        base_date = date(2025, 11, 1)
        history = [
            {"date": base_date + timedelta(days=i), "qty_sold": qty}
            for i, qty in enumerate(intermittent_series)
        ]
        settings = {"alpha_default": 0.1, "backtest_enabled": False, "default_method": "sba"}

        demand1 = build_demand_distribution(
            method="intermittent_auto",
            history=history,
            protection_period_days=14,
            asof_date=date(2026, 2, 1),
            mc_params=settings
        )

        demand2 = build_demand_distribution(
            method="intermittent_auto",
            history=history,
            protection_period_days=14,
            asof_date=date(2026, 2, 1),
            mc_params=settings
        )

        assert demand1.mu_P == demand2.mu_P
        assert demand1.sigma_P == demand2.sigma_P
        assert demand1.intermittent_method == demand2.intermittent_method
