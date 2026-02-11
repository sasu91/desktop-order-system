"""
Test: Gestione festività con ordini anticipati.

Dimostra che il sistema può gestire ordini anticipati a causa di festività,
usando sia override manuale che configurazione CalendarConfig.
"""
import pytest
from datetime import date
from pathlib import Path
import tempfile

from src.domain.models import SKU, Transaction, EventType, SalesRecord, Stock, DemandVariability
from src.domain.ledger import StockCalculator
from src.domain.calendar import CalendarConfig, next_receipt_date, Lane, is_delivery_day
from src.persistence.csv_layer import CSVLayer
from src.workflows.order import OrderWorkflow


def test_wednesday_holiday_tuesday_order():
    """
    Scenario: Mercoledì festivo, ordine anticipato martedì per consegna giovedì.
    
    Setup:
    - Martedì 11 febbraio: ordine
    - Mercoledì 12 febbraio: FESTIVITÀ (fornitore chiuso)
    - Giovedì 13 febbraio: consegna
    
    Verifica:
    - next_receipt_date() salta il mercoledì festivo
    - Projected IP sottrae vendite di 2 giorni (mar→gio)
    - Sistema funziona senza modifiche al codice
    """
    # Configura festività
    wednesday_holiday = date(2026, 2, 12)
    config = CalendarConfig(holidays={wednesday_holiday})
    
    # Verifica che mercoledì non sia valido per consegne
    assert not is_delivery_day(wednesday_holiday, config), "Mercoledì deve essere festivo"
    
    # Ordine martedì
    tuesday = date(2026, 2, 11)
    
    # next_receipt_date deve saltare mercoledì e dare giovedì
    receipt = next_receipt_date(tuesday, Lane.STANDARD, config)
    expected_thursday = date(2026, 2, 13)
    
    assert receipt == expected_thursday, \
        f"Receipt deve essere giovedì {expected_thursday}, non mercoledì festivo, got {receipt}"
    
    # Proiezione IP con vendite previste di 2 giorni
    stock = Stock(
        sku="SKU_HOLIDAY",
        on_hand=100,
        on_order=0,
        unfulfilled_qty=0,
        asof_date=tuesday
    )
    
    projected_ip = StockCalculator.projected_inventory_position(
        sku="SKU_HOLIDAY",
        target_date=expected_thursday,
        current_stock=stock,
        transactions=[],
        daily_sales_forecast=10.0
    )
    
    # IP = 100 - (2 giorni × 10 pz/giorno) = 80
    expected_ip = 80
    assert projected_ip == expected_ip, \
        f"IP deve sottrarre 2 giorni di vendite (mar+mer), expected {expected_ip}, got {projected_ip}"


def test_manual_override_for_holiday():
    """
    Test: Override manuale della receipt_date per gestire festività.
    
    Non serve configurare CalendarConfig, basta impostare target_receipt_date
    manualmente nella GUI o nel workflow.
    """
    test_dir = tempfile.mkdtemp()
    
    try:
        csv_layer = CSVLayer(data_dir=Path(test_dir))
        workflow = OrderWorkflow(csv_layer=csv_layer, lead_time_days=1)
        
        sku_id = "SKU_MANUAL"
        sku = SKU(
            sku=sku_id,
            description="Manual Override Test",
            ean="",
            moq=10,
            pack_size=10,
            lead_time_days=1,
            review_period=7,
            safety_stock=20,
            shelf_life_days=0,
            max_stock=500,
            reorder_point=30,
            demand_variability=DemandVariability.LOW,
            in_assortment=True
        )
        csv_layer.write_sku(sku)
        
        # Stock iniziale
        tuesday = date(2026, 2, 11)
        transactions = [
            Transaction(date=date(2026, 2, 10), sku=sku_id, event=EventType.SNAPSHOT, qty=100)
        ]
        for txn in transactions:
            csv_layer.write_transaction(txn)
        
        # Sales history
        sales = [
            SalesRecord(date=date(2026, 2, i), sku=sku_id, qty_sold=10)
            for i in range(5, 11)
        ]
        csv_layer.write_sales(sales)
        
        stock = Stock(sku=sku_id, on_hand=100, on_order=0, unfulfilled_qty=0, asof_date=tuesday)
        
        # Ordine con override manuale: receipt_date = giovedì (salta mercoledì festivo)
        thursday = date(2026, 2, 13)
        proposal = workflow.generate_proposal(
            sku=sku_id,
            description="Holiday Override",
            current_stock=stock,
            daily_sales_avg=10.0,
            sku_obj=sku,
            target_receipt_date=thursday,  # Override manuale
            protection_period_days=2,  # Gio→Sab (prossimo ordine venerdì)
            transactions=transactions,
            sales_records=sales
        )
        
        # Verifica che IP sottrae 2 giorni di vendite
        # IP = 100 - (2 × 10) = 80
        expected_ip = 80
        assert proposal.inventory_position == expected_ip, \
            f"IP deve essere {expected_ip} (2 giorni vendite), got {proposal.inventory_position}"
        
        # Verifica receipt_date corretta
        assert proposal.receipt_date == thursday, \
            f"Receipt must be {thursday}, got {proposal.receipt_date}"
        
    finally:
        import shutil
        shutil.rmtree(test_dir, ignore_errors=True)


def test_multiple_holidays_skip():
    """
    Test: Gestione multipli giorni festivi consecutivi (es. ponte).
    
    Scenario:
    - Giovedì 25 aprile: Festa della Liberazione
    - Venerdì 26 aprile: Ponte (festivo)
    - Ordine mercoledì 24 → consegna lunedì 27 (primo giorno valido)
    """
    thursday_holiday = date(2026, 4, 25)
    friday_holiday = date(2026, 4, 26)
    config = CalendarConfig(holidays={thursday_holiday, friday_holiday})
    
    wednesday = date(2026, 4, 24)
    
    # next_receipt_date deve saltare gio+ven e dare lunedì 27
    receipt = next_receipt_date(wednesday, Lane.STANDARD, config)
    expected_monday = date(2026, 4, 27)  # Primo giorno di consegna valido
    
    assert receipt == expected_monday, \
        f"Receipt deve saltare gio+ven → lunedì {expected_monday}, got {receipt}"
    
    # Proiezione IP: 3 giorni di vendite (mer→lun, escludendo gio e ven festivi)
    stock = Stock(
        sku="SKU_BRIDGE",
        on_hand=200,
        on_order=0,
        unfulfilled_qty=0,
        asof_date=wednesday
    )
    
    projected_ip = StockCalculator.projected_inventory_position(
        sku="SKU_BRIDGE",
        target_date=expected_monday,
        current_stock=stock,
        transactions=[],
        daily_sales_forecast=15.0
    )
    
    # IP = 200 - (3 giorni × 15 pz) = 155
    # Note: il calcolo usa (monday - wednesday).days = 3 giorni
    expected_ip = 155
    assert projected_ip == expected_ip, \
        f"IP deve sottrarre 3 giorni (mer→lun), expected {expected_ip}, got {projected_ip}"


def test_friday_saturday_lane_with_saturday_holiday():
    """
    Test: Lane SATURDAY con sabato festivo → salta a lunedì.
    
    Scenario edge-case: Sabato festivo (es. 1° maggio su sabato)
    """
    saturday_holiday = date(2026, 5, 2)  # Sabato 2 maggio (festivo)
    config = CalendarConfig(holidays={saturday_holiday})
    
    friday = date(2026, 5, 1)  # Venerdì 1° maggio
    
    # Lane SATURDAY normalmente dà sabato, ma è festivo → lunedì
    receipt = next_receipt_date(friday, Lane.SATURDAY, config)
    expected_monday = date(2026, 5, 4)
    
    assert receipt == expected_monday, \
        f"SATURDAY lane con sabato festivo deve dare lunedì {expected_monday}, got {receipt}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
