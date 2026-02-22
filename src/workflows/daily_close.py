"""
Daily closing workflow: EOD stock entry and automatic sales calculation with FEFO.
"""
from datetime import date
from typing import Tuple, Optional
import logging

from ..domain.models import Transaction, EventType, SalesRecord
from ..domain.ledger import calculate_sold_from_eod_stock, LotConsumptionManager
from ..persistence.csv_layer import CSVLayer

logger = logging.getLogger(__name__)


class DailyCloseWorkflow:
    """Workflow for end-of-day stock entry and sales derivation."""
    
    def __init__(self, csv_layer: CSVLayer):
        """
        Initialize the workflow.
        
        Args:
            csv_layer: CSV layer for data persistence
        """
        self.csv_layer = csv_layer
    
    def process_eod_stock(
        self,
        sku: str,
        eod_date: date,
        eod_stock_on_hand: int,
    ) -> Tuple[Optional[SalesRecord], Optional[Transaction], str]:
        """
        Process end-of-day stock entry: calculate sales and adjustment.
        
        Args:
            sku: SKU identifier
            eod_date: End-of-day date
            eod_stock_on_hand: Declared stock on hand at end of day
        
        Returns:
            Tuple[Optional[SalesRecord], Optional[Transaction], str]:
                - SalesRecord written to sales.csv (if qty_sold > 0)
                - ADJUST transaction written to ledger (if adjustment != 0)
                - Status message
        
        Raises:
            ValueError: If eod_stock_on_hand < 0 or SKU doesn't exist
        """
        # Validation
        if eod_stock_on_hand < 0:
            raise ValueError(f"Stock EOD cannot be negative: {eod_stock_on_hand}")
        
        # Check SKU exists
        skus = self.csv_layer.read_skus()
        if not any(s.sku == sku for s in skus):
            raise ValueError(f"SKU {sku} does not exist")
        
        # Load current data
        transactions = self.csv_layer.read_transactions()
        sales_records = self.csv_layer.read_sales()
        
        # Calculate sold qty and adjustment
        qty_sold, adjustment = calculate_sold_from_eod_stock(
            sku=sku,
            eod_date=eod_date,
            eod_stock_on_hand=eod_stock_on_hand,
            transactions=transactions,
            sales_records=sales_records,
        )
        
        sales_record = None
        adjust_txn = None
        
        # Write sales if qty_sold > 0
        if qty_sold > 0:
            # Check if sale already recorded for this date/SKU (idempotency)
            existing_sale = next(
                (s for s in sales_records if s.date == eod_date and s.sku == sku),
                None
            )
            
            if existing_sale:
                # Update existing sale (overwrite)
                sales_records = [s for s in sales_records if not (s.date == eod_date and s.sku == sku)]
                sales_records.append(SalesRecord(date=eod_date, sku=sku, qty_sold=qty_sold))
                self.csv_layer.write_sales(sales_records)
                sales_record = SalesRecord(date=eod_date, sku=sku, qty_sold=qty_sold)
            else:
                # Append new sale
                sales_record = SalesRecord(date=eod_date, sku=sku, qty_sold=qty_sold)
                self.csv_layer.append_sales(sales_record)
            
            # Apply FEFO consumption to lots (real-time sync)
            # Note: EOD sales are recorded in sales.csv only, not as SALE transactions in ledger
            # Therefore FEFO must be applied explicitly here (auto-FEFO in write_transaction only
            # applies to SALE/WASTE events written to ledger)
            try:
                lots = self.csv_layer.get_lots_by_sku(sku, sort_by_expiry=True)
                if lots:
                    consumption_records = LotConsumptionManager.consume_from_lots(
                        sku=sku,
                        qty_to_consume=qty_sold,
                        lots=lots,
                        csv_layer=self.csv_layer,
                    )
                    
                    if consumption_records:
                        logger.info(f"FEFO consumption for {sku} EOD sales: {consumption_records}")
            except Exception as e:
                logger.warning(f"FEFO consumption failed for {sku}: {e}, continuing without lot update")


        
        # Write ADJUST if adjustment != 0 (stock discrepancy after accounting for sales)
        if adjustment != 0:
            # Create ADJUST transaction to align stock
            adjust_txn = Transaction(
                date=eod_date,
                sku=sku,
                event=EventType.ADJUST,
                qty=eod_stock_on_hand,  # ADJUST sets stock to this value
                note=f"EOD adjustment (discrepancy: {adjustment:+d})",
            )
            self.csv_layer.write_transaction(adjust_txn)
        
        # Build status message
        msg_parts = []
        if qty_sold > 0:
            msg_parts.append(f"Venduto: {qty_sold}")
        if adjustment != 0:
            msg_parts.append(f"Rettifica: {adjustment:+d}")
        if not msg_parts:
            msg_parts.append("Nessun cambiamento (stock teorico = EOD)")
        
        status = f"{sku} | {' | '.join(msg_parts)}"
        
        return sales_record, adjust_txn, status
    
    def process_bulk_eod_stock(
        self,
        eod_entries: dict,
        eod_date: date,
    ) -> list:
        """
        Process multiple EOD stock entries at once.
        
        Args:
            eod_entries: Dict {sku: eod_stock_on_hand}
            eod_date: End-of-day date
        
        Returns:
            List of status messages for each SKU
        
        Note: Continues processing even if one SKU fails (logs error).
        """
        results = []
        
        for sku, eod_stock in eod_entries.items():
            try:
                _, _, status = self.process_eod_stock(sku, eod_date, eod_stock)
                results.append(f"✓ {status}")
            except Exception as e:
                results.append(f"✗ {sku} | Errore: {str(e)}")
        
        return results
