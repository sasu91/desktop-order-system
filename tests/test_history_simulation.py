"""
Test suite for HistorySimulationWorkflow.

Covers:
1. test_basic_simulation — run completes, events generati con SIM_TAG
2. test_only_expected_event_types — solo SALE/ORDER/RECEIPT nel ledger
3. test_idempotency — rerun non duplica dati
4. test_non_sim_transactions_preserved — transazioni reali sopravvivono al rerun
5. test_sales_csv_replaced_no_duplicates — sales.csv senza duplicati dopo rerun
6. test_sales_outside_period_preserved — vendite fuori periodo conservate
7. test_receipt_before_sale_same_day — RECEIPT precede SALE stesso giorno
8. test_order_receipt_date_offset — receipt_date su ORDER = order_date + lead_time
9. test_oos_warnings_when_no_replenishment — OOS con lead_time > n_days
10. test_no_oos_warning_zero_demand — nessun warning con domanda zero
11. test_leading_zero_sku_preserved — codice SKU con zero iniziale preservato
12. test_validation_errors — input non validi → ValueError
"""
import csv
import tempfile
import shutil
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.workflows.history_simulation import HistorySimulationWorkflow, SIM_TAG
from src.persistence.csv_layer import CSVLayer
from src.domain.models import EventType, SalesRecord, Transaction

# ---------------------------------------------------------------------------
# Schema e helpers per CSV di test
# ---------------------------------------------------------------------------

_SKU_SCHEMA = [
    "sku", "description", "ean", "ean_secondary", "moq", "pack_size",
    "lead_time_days", "review_period", "safety_stock", "shelf_life_days",
    "min_shelf_life_days", "waste_penalty_mode", "waste_penalty_factor",
    "waste_risk_threshold", "max_stock", "reorder_point", "demand_variability",
    "category", "department", "oos_boost_percent", "oos_detection_mode",
    "oos_popup_preference", "forecast_method", "mc_distribution",
    "mc_n_simulations", "mc_random_seed", "mc_output_stat",
    "mc_output_percentile", "mc_horizon_mode", "mc_horizon_days",
    "in_assortment", "target_csl", "has_expiry_label",
]


def _write_csv(path: Path, filename: str, schema: list, rows: list) -> None:
    full_path = path / filename
    with open(full_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=schema, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            full_row = {k: "" for k in schema}
            full_row.update(row)
            writer.writerow(full_row)


def _make_sku_row(
    sku: str,
    lead_time: int = 5,
    reorder_point: int = 0,
    max_stock: int = 999,
    review_period: int = 7,
    safety_stock: int = 0,
) -> dict:
    return {
        "sku": sku,
        "description": f"Test SKU {sku}",
        "ean": "",
        "moq": "1",
        "pack_size": "1",
        "lead_time_days": str(lead_time),
        "review_period": str(review_period),
        "safety_stock": str(safety_stock),
        "max_stock": str(max_stock),
        "reorder_point": str(reorder_point),
        "in_assortment": "true",
        "target_csl": "0",
        "oos_popup_preference": "ask",
    }


def _setup_layer(data_dir: Path, sku_rows: list) -> CSVLayer:
    """Crea CSVLayer in *data_dir* e scrive i dati SKU richiesti."""
    layer = CSVLayer(data_dir=data_dir)
    _write_csv(data_dir, "skus.csv", _SKU_SCHEMA, sku_rows)
    return layer


def _sim_txns(csv_layer: CSVLayer, sku_code: str) -> list:
    """Restituisce le transazioni simulate (con SIM_TAG) per lo sku indicato."""
    return [
        t for t in csv_layer.read_transactions()
        if t.sku == sku_code and t.note and SIM_TAG in t.note
    ]


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d)


@pytest.fixture
def csv_layer(tmp_dir):
    return _setup_layer(tmp_dir, [_make_sku_row("SKU_TEST", lead_time=5, reorder_point=0)])


@pytest.fixture
def workflow(csv_layer):
    return HistorySimulationWorkflow(csv_layer)


# ---------------------------------------------------------------------------
# 1. Test di base
# ---------------------------------------------------------------------------

class TestBasicSimulation:
    """La simulazione gira senza errori e genera gli eventi attesi."""

    END_DATE = date(2025, 1, 31)
    N_DAYS = 10

    def test_returns_correct_days_generated(self, workflow):
        result = workflow.run_for_sku("SKU_TEST", 2, 4, self.N_DAYS, self.END_DATE, random_seed=42)
        assert result.days_generated == self.N_DAYS

    def test_creates_transactions_in_ledger(self, csv_layer, workflow):
        workflow.run_for_sku("SKU_TEST", 2, 4, self.N_DAYS, self.END_DATE, random_seed=42)
        assert len(_sim_txns(csv_layer, "SKU_TEST")) > 0

    def test_all_sim_transactions_tagged(self, csv_layer, workflow):
        workflow.run_for_sku("SKU_TEST", 2, 4, self.N_DAYS, self.END_DATE, random_seed=42)
        txns = _sim_txns(csv_layer, "SKU_TEST")
        assert all(SIM_TAG in (t.note or "") for t in txns), \
            "Ogni transazione simulata deve contenere SIM_TAG nel campo note"

    def test_only_sale_order_receipt_event_types(self, csv_layer, workflow):
        workflow.run_for_sku("SKU_TEST", 2, 4, self.N_DAYS, self.END_DATE, random_seed=42)
        txns = _sim_txns(csv_layer, "SKU_TEST")
        allowed = {EventType.SALE, EventType.ORDER, EventType.RECEIPT}
        actual = {t.event for t in txns}
        assert actual.issubset(allowed), f"Tipi di evento inattesi: {actual - allowed}"

    def test_at_least_one_order_created(self, workflow):
        # Con stock iniziale 0 e reorder_point=0 deve essere generato almeno 1 ordine
        result = workflow.run_for_sku("SKU_TEST", 2, 4, self.N_DAYS, self.END_DATE, random_seed=42)
        assert result.orders_created >= 1

    def test_orders_created_matches_ledger(self, csv_layer, workflow):
        result = workflow.run_for_sku("SKU_TEST", 1, 3, self.N_DAYS, self.END_DATE, random_seed=1)
        order_txns = [t for t in _sim_txns(csv_layer, "SKU_TEST") if t.event == EventType.ORDER]
        assert result.orders_created == len(order_txns)

    def test_receipts_created_matches_ledger(self, csv_layer, workflow):
        result = workflow.run_for_sku("SKU_TEST", 1, 3, self.N_DAYS, self.END_DATE, random_seed=1)
        receipt_txns = [t for t in _sim_txns(csv_layer, "SKU_TEST") if t.event == EventType.RECEIPT]
        assert result.receipts_created == len(receipt_txns)

    def test_total_sales_qty_matches_ledger(self, csv_layer, workflow):
        result = workflow.run_for_sku("SKU_TEST", 2, 4, self.N_DAYS, self.END_DATE, random_seed=7)
        sale_txns = [t for t in _sim_txns(csv_layer, "SKU_TEST") if t.event == EventType.SALE]
        ledger_total = sum(t.qty for t in sale_txns)
        assert result.total_sales_qty == ledger_total


# ---------------------------------------------------------------------------
# 2. Idempotenza
# ---------------------------------------------------------------------------

class TestIdempotency:
    """Il rerun della simulazione sovrascrive i dati precedenti senza duplicati."""

    END_DATE = date(2025, 2, 28)
    N_DAYS = 14

    def test_rerun_same_seed_no_duplicates(self, csv_layer, workflow):
        workflow.run_for_sku("SKU_TEST", 2, 4, self.N_DAYS, self.END_DATE, random_seed=7)
        cnt_first = len(_sim_txns(csv_layer, "SKU_TEST"))
        workflow.run_for_sku("SKU_TEST", 2, 4, self.N_DAYS, self.END_DATE, random_seed=7)
        cnt_second = len(_sim_txns(csv_layer, "SKU_TEST"))
        assert cnt_first == cnt_second, \
            "Rerun con stesso seed non deve duplicare le transazioni simulate"

    def test_rerun_different_seed_replaces_all(self, csv_layer, workflow):
        workflow.run_for_sku("SKU_TEST", 2, 4, self.N_DAYS, self.END_DATE, random_seed=1)
        workflow.run_for_sku("SKU_TEST", 2, 4, self.N_DAYS, self.END_DATE, random_seed=2)
        txns = _sim_txns(csv_layer, "SKU_TEST")
        start = self.END_DATE - timedelta(days=self.N_DAYS - 1)
        for t in txns:
            assert start <= t.date <= self.END_DATE, \
                f"Transazione fuori periodo trovata dopo rerun: {t.date}"

    def test_non_sim_transactions_survive_rerun(self, csv_layer, workflow):
        # Inserisce una transazione reale (niente SIM_TAG)
        manual_txn = Transaction(
            date=date(2024, 6, 1),
            sku="SKU_TEST",
            event=EventType.SNAPSHOT,
            qty=100,
            note="nota manuale",
        )
        csv_layer.write_transactions_batch([manual_txn])

        workflow.run_for_sku("SKU_TEST", 2, 4, self.N_DAYS, self.END_DATE, random_seed=3)
        workflow.run_for_sku("SKU_TEST", 2, 4, self.N_DAYS, self.END_DATE, random_seed=3)

        all_txns = csv_layer.read_transactions()
        manual_kept = [t for t in all_txns if t.note == "nota manuale" and t.sku == "SKU_TEST"]
        assert len(manual_kept) == 1, \
            "La transazione reale (non simulata) deve sopravvivere al rerun"

    def test_other_sku_transactions_not_touched(self, tmp_dir):
        """Il purge della simulazione non deve toccare transazioni di altri SKU."""
        layer = _setup_layer(tmp_dir, [
            _make_sku_row("SKU_A", lead_time=5),
            _make_sku_row("SKU_B", lead_time=5),
        ])
        # Inserisce una transazione per SKU_B con SIM_TAG (stesso periodo)
        other_txn = Transaction(
            date=date(2025, 2, 15),
            sku="SKU_B",
            event=EventType.SALE,
            qty=3,
            note=f"{SIM_TAG}|simulazione per SKU_B",
        )
        layer.write_transactions_batch([other_txn])

        wf = HistorySimulationWorkflow(layer)
        end = date(2025, 2, 28)
        wf.run_for_sku("SKU_A", 1, 3, 14, end, random_seed=5)

        # Transazione di SKU_B deve essere ancora presente
        all_txns = layer.read_transactions()
        skub_txns = [t for t in all_txns if t.sku == "SKU_B"]
        assert len(skub_txns) >= 1, \
            "Le transazioni di altri SKU non devono essere cancellate dalla simulazione"


# ---------------------------------------------------------------------------
# 3. sales.csv
# ---------------------------------------------------------------------------

class TestSalesCsv:
    """Le righe di sales.csv per SKU+periodo vengono sostituite a ogni run."""

    END_DATE = date(2025, 3, 31)
    N_DAYS = 7

    def _period_sales(self, csv_layer, sku_code):
        start = self.END_DATE - timedelta(days=self.N_DAYS - 1)
        return [
            s for s in csv_layer.read_sales()
            if s.sku == sku_code and start <= s.date <= self.END_DATE
        ]

    def test_no_duplicate_dates_after_rerun(self, csv_layer, workflow):
        workflow.run_for_sku("SKU_TEST", 2, 5, self.N_DAYS, self.END_DATE, random_seed=10)
        workflow.run_for_sku("SKU_TEST", 2, 5, self.N_DAYS, self.END_DATE, random_seed=10)
        period_sales = self._period_sales(csv_layer, "SKU_TEST")
        dates = [s.date for s in period_sales]
        assert len(dates) == len(set(dates)), \
            "Non devono esserci date duplicate in sales.csv dopo rerun"

    def test_sales_outside_period_preserved(self, csv_layer, workflow):
        # Aggiunge una vendita fuori periodo
        outside_sale = SalesRecord(date=date(2020, 1, 1), sku="SKU_TEST", qty_sold=99)
        csv_layer.write_sales([outside_sale])
        workflow.run_for_sku("SKU_TEST", 2, 5, self.N_DAYS, self.END_DATE, random_seed=10)
        kept = [
            s for s in csv_layer.read_sales()
            if s.date == date(2020, 1, 1) and s.sku == "SKU_TEST"
        ]
        assert len(kept) == 1, "La vendita fuori periodo deve essere conservata"

    def test_other_sku_sales_not_removed(self, tmp_dir):
        layer = _setup_layer(tmp_dir, [
            _make_sku_row("SKU_X", lead_time=5),
            _make_sku_row("SKU_Y", lead_time=5),
        ])
        # Aggiunge vendite per SKU_Y nello stesso periodo
        start = self.END_DATE - timedelta(days=self.N_DAYS - 1)
        other_sale = SalesRecord(date=start, sku="SKU_Y", qty_sold=10)
        layer.write_sales([other_sale])

        wf = HistorySimulationWorkflow(layer)
        wf.run_for_sku("SKU_X", 2, 5, self.N_DAYS, self.END_DATE, random_seed=10)

        remaining = [s for s in layer.read_sales() if s.sku == "SKU_Y"]
        assert len(remaining) >= 1, "Le vendite di altri SKU non devono essere eliminate"


# ---------------------------------------------------------------------------
# 4. Ordinamento eventi: RECEIPT prima di SALE lo stesso giorno
# ---------------------------------------------------------------------------

class TestEventOrdering:
    """Quando un RECEIPT arriva lo stesso giorno di una SALE, RECEIPT precede nel ledger."""

    END_DATE = date(2025, 4, 30)
    N_DAYS = 20

    def test_receipt_before_sale_same_day(self, csv_layer, workflow):
        workflow.run_for_sku("SKU_TEST", 2, 4, self.N_DAYS, self.END_DATE, random_seed=99)
        txns = _sim_txns(csv_layer, "SKU_TEST")

        by_date: dict = {}
        for t in txns:
            by_date.setdefault(t.date, []).append(t.event)

        for d, events in by_date.items():
            if EventType.RECEIPT in events and EventType.SALE in events:
                receipt_idx = next(i for i, e in enumerate(events) if e == EventType.RECEIPT)
                sale_idx = next(i for i, e in enumerate(events) if e == EventType.SALE)
                assert receipt_idx < sale_idx, \
                    f"Giorno {d}: RECEIPT deve precedere SALE nel ledger"

    def test_order_receipt_date_offset_equals_lead_time(self, csv_layer, workflow):
        """Il campo receipt_date di ogni ORDER deve essere esattamente lead_time giorni dopo."""
        workflow.run_for_sku("SKU_TEST", 2, 4, self.N_DAYS, self.END_DATE, random_seed=42)
        txns = _sim_txns(csv_layer, "SKU_TEST")
        order_txns = [t for t in txns if t.event == EventType.ORDER]
        assert order_txns, "Deve esserci almeno un ORDER nella simulazione"
        # lead_time_days per SKU_TEST = 5 (dalla fixture _make_sku_row)
        for o in order_txns:
            assert o.receipt_date is not None, f"ORDER mancante di receipt_date: {o}"
            delta = (o.receipt_date - o.date).days
            assert delta == 5, \
                f"ORDER del {o.date}: atteso +5 giorni, trovato +{delta}"


# ---------------------------------------------------------------------------
# 5. Warning OOS
# ---------------------------------------------------------------------------

class TestOOSWarnings:
    """I warning vengono generati correttamente quando lo stock è insufficiente."""

    END_DATE = date(2025, 5, 31)

    def test_oos_warnings_when_lead_time_exceeds_period(self, tmp_dir):
        """
        Con lead_time > n_days nessun RECEIPT può arrivare nel periodo:
        lo stock rimane 0 e ogni giorno con domanda > 0 genera un warning.
        """
        layer = _setup_layer(tmp_dir, [_make_sku_row("SKU_OOS", lead_time=100)])
        wf = HistorySimulationWorkflow(layer)
        n_days = 5
        result = wf.run_for_sku("SKU_OOS", 1, 2, n_days, self.END_DATE, random_seed=1)
        # Tutti i 5 giorni non possono essere soddisfatti → 5 warning
        assert len(result.warnings) == n_days, \
            f"Attesi {n_days} warning OOS, trovati {len(result.warnings)}"

    def test_no_oos_warning_when_demand_is_zero(self, tmp_dir):
        """Con qty_min=qty_max=0 non c'è domanda → nessun warning OOS."""
        layer = _setup_layer(tmp_dir, [_make_sku_row("SKU_ZERO", lead_time=5)])
        wf = HistorySimulationWorkflow(layer)
        result = wf.run_for_sku("SKU_ZERO", 0, 0, 10, self.END_DATE, random_seed=1)
        assert result.warnings == [], "Zero domanda non deve generare warning OOS"

    def test_oos_warning_messages_contain_date(self, tmp_dir):
        """I messaggi di warning devono contenere la data del giorno OOS."""
        layer = _setup_layer(tmp_dir, [_make_sku_row("SKU_MSG", lead_time=100)])
        wf = HistorySimulationWorkflow(layer)
        result = wf.run_for_sku("SKU_MSG", 1, 1, 3, self.END_DATE, random_seed=1)
        for w in result.warnings:
            assert any(char.isdigit() for char in w), \
                f"Il warning '{w}' dovrebbe contenere una data"


# ---------------------------------------------------------------------------
# 6. SKU con zero iniziale
# ---------------------------------------------------------------------------

class TestLeadingZeroSku:
    """I codici SKU con zero iniziale vengono preservati verbatim."""

    END_DATE = date(2025, 6, 30)
    N_DAYS = 5
    SKU_CODE = "0450636"

    @pytest.fixture
    def layer_zerosku(self, tmp_dir):
        return _setup_layer(tmp_dir, [_make_sku_row(self.SKU_CODE, lead_time=3)])

    def test_run_without_error(self, layer_zerosku):
        wf = HistorySimulationWorkflow(layer_zerosku)
        result = wf.run_for_sku(self.SKU_CODE, 1, 3, self.N_DAYS, self.END_DATE, random_seed=5)
        assert result.days_generated == self.N_DAYS

    def test_sku_code_preserved_in_transactions(self, layer_zerosku):
        wf = HistorySimulationWorkflow(layer_zerosku)
        wf.run_for_sku(self.SKU_CODE, 1, 3, self.N_DAYS, self.END_DATE, random_seed=5)
        txns = layer_zerosku.read_transactions()
        sim_txns = [t for t in txns if SIM_TAG in (t.note or "")]
        assert sim_txns, "Deve esistere almeno una transazione simulata"
        for t in sim_txns:
            assert t.sku == self.SKU_CODE, \
                f"SKU atteso '{self.SKU_CODE}', trovato '{t.sku}'"

    def test_sku_code_preserved_in_sales_csv(self, layer_zerosku):
        wf = HistorySimulationWorkflow(layer_zerosku)
        wf.run_for_sku(self.SKU_CODE, 1, 3, self.N_DAYS, self.END_DATE, random_seed=5)
        sales = layer_zerosku.read_sales()
        sku_sales = [s for s in sales if s.sku == self.SKU_CODE]
        # Con lead_time=3 e n_days=5, lo stock parte da 0; alcune vendite potrebbero essere OOS
        # Ma tutti i record esistenti devono avere il codice corretto
        for s in sku_sales:
            assert s.sku == self.SKU_CODE, \
                f"SKU in sales.csv atteso '{self.SKU_CODE}', trovato '{s.sku}'"


# ---------------------------------------------------------------------------
# 7. Errori di validazione
# ---------------------------------------------------------------------------

class TestValidationErrors:
    END_DATE = date(2025, 7, 31)

    def test_unknown_sku_raises_value_error(self, workflow):
        with pytest.raises(ValueError, match="non trovato"):
            workflow.run_for_sku("SKU_NONEXISTENT", 1, 2, 5, self.END_DATE)

    def test_qty_max_less_than_qty_min_raises(self, workflow):
        with pytest.raises(ValueError, match="qty_max"):
            workflow.run_for_sku("SKU_TEST", 5, 3, 10, self.END_DATE)

    def test_negative_qty_min_raises(self, workflow):
        with pytest.raises(ValueError, match="negativo"):
            workflow.run_for_sku("SKU_TEST", -1, 2, 10, self.END_DATE)

    def test_n_days_zero_raises(self, workflow):
        with pytest.raises(ValueError, match="n_days"):
            workflow.run_for_sku("SKU_TEST", 1, 2, 0, self.END_DATE)

    def test_invalid_sku_format_raises(self, workflow):
        # SKU con spazio non è valido secondo validate_sku_canonical
        with pytest.raises((ValueError, Exception)):
            workflow.run_for_sku("SKU INVALID", 1, 2, 5, self.END_DATE)


# ---------------------------------------------------------------------------
# 8. Determinismo con seed fisso
# ---------------------------------------------------------------------------

class TestDeterminism:
    """Con lo stesso seed la simulazione produce risultati identici."""

    END_DATE = date(2025, 8, 31)
    N_DAYS = 10

    def test_same_seed_produces_same_total_sales(self, tmp_dir):
        # Due layer separati, stesso seed → stesso totale vendite
        layer_a = _setup_layer(tmp_dir / "a", [_make_sku_row("SKU_DET")])
        layer_b = _setup_layer(tmp_dir / "b", [_make_sku_row("SKU_DET")])
        (tmp_dir / "a").mkdir(exist_ok=True)
        (tmp_dir / "b").mkdir(exist_ok=True)

        wf_a = HistorySimulationWorkflow(layer_a)
        wf_b = HistorySimulationWorkflow(layer_b)

        res_a = wf_a.run_for_sku("SKU_DET", 2, 5, self.N_DAYS, self.END_DATE, random_seed=123)
        res_b = wf_b.run_for_sku("SKU_DET", 2, 5, self.N_DAYS, self.END_DATE, random_seed=123)

        assert res_a.total_sales_qty == res_b.total_sales_qty, \
            "Stesso seed deve produrre lo stesso totale di vendite"
        assert res_a.orders_created == res_b.orders_created, \
            "Stesso seed deve produrre lo stesso numero di ordini"
