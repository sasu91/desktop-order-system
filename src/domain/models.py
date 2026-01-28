"""
Domain models for desktop-order-system.

Pure data classes + value objects. No I/O, no side effects.
Deterministic and fully testable.
"""
from dataclasses import dataclass, field
from enum import Enum
from datetime import date
from typing import Optional


class EventType(Enum):
    """Event types that impact stock ledger."""
    SNAPSHOT = "SNAPSHOT"      # Base inventory reset: on_hand := qty
    ORDER = "ORDER"            # Increase on_order: on_order += qty
    RECEIPT = "RECEIPT"        # Receipt: on_order -= qty, on_hand += qty
    SALE = "SALE"              # Sale: on_hand -= qty
    WASTE = "WASTE"            # Waste: on_hand -= qty
    ADJUST = "ADJUST"          # Signed adjustment: on_hand ± qty
    UNFULFILLED = "UNFULFILLED"  # Tracking only (no impact on stock)
    SKU_EDIT = "SKU_EDIT"      # SKU metadata change (description/EAN) - no stock impact
    EXPORT_LOG = "EXPORT_LOG"  # Export operation log - no stock impact


@dataclass(frozen=True)
class SKU:
    """Stock Keeping Unit (inventory item) - immutable."""
    sku: str
    description: str
    ean: Optional[str] = None  # EAN/GTIN; can be empty or invalid
    
    def __post_init__(self):
        if not self.sku or not self.sku.strip():
            raise ValueError("SKU cannot be empty")
        if not self.description or not self.description.strip():
            raise ValueError("Description cannot be empty")


@dataclass(frozen=True)
class Transaction:
    """Ledger transaction event - immutable."""
    date: date          # Event date (YYYY-MM-DD)
    sku: str            # Reference to SKU
    event: EventType    # Event type
    qty: int            # Signed quantity
    receipt_date: Optional[date] = None  # For ORDER/RECEIPT events
    note: Optional[str] = None
    
    def __post_init__(self):
        if self.date > date.today():
            raise ValueError("Transaction date cannot be in the future")
        if self.event == EventType.SNAPSHOT and self.qty < 0:
            raise ValueError("SNAPSHOT qty must be non-negative")


@dataclass(frozen=True)
class Stock:
    """Calculated stock state for a SKU at a given AsOf date."""
    sku: str
    on_hand: int
    on_order: int
    asof_date: Optional[date] = None
    
    def __post_init__(self):
        if self.on_hand < 0:
            raise ValueError("on_hand cannot be negative")
        if self.on_order < 0:
            raise ValueError("on_order cannot be negative")
    
    def available(self) -> int:
        """Total available inventory (on_hand + on_order)."""
        return self.on_hand + self.on_order


@dataclass(frozen=True)
class AuditLog:
    """Audit trail entry for tracking operations."""
    timestamp: str      # ISO format with time: YYYY-MM-DD HH:MM:SS
    operation: str      # SKU_EDIT, EXPORT, etc.
    sku: Optional[str]  # Affected SKU (if applicable)
    details: str        # Human-readable description
    user: str = "system"  # User/operator (default: system)


@dataclass(frozen=True)
class SalesRecord:
    """Daily sales record from sales.csv."""
    date: date
    sku: str
    qty_sold: int
    
    def __post_init__(self):
        if self.qty_sold < 0:
            raise ValueError("qty_sold cannot be negative")


@dataclass
class OrderProposal:
    """Order proposal for a SKU."""
    sku: str
    description: str
    current_on_hand: int
    current_on_order: int
    daily_sales_avg: float
    proposed_qty: int
    receipt_date: Optional[date] = None
    notes: Optional[str] = None


@dataclass
class OrderConfirmation:
    """Confirmed order details."""
    order_id: str
    date: date
    sku: str
    qty_ordered: int
    receipt_date: date
    status: str = "PENDING"  # PENDING, RECEIVED, PARTIAL


@dataclass
class ReceivingLog:
    """Receiving closure record."""
    receipt_id: str
    date: date
    sku: str
    qty_received: int
    receipt_date: date
