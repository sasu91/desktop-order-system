#!/usr/bin/env python3
"""
Workflow integration tests for cannibalization (downlift) feature.

Tests: Verify that OrderProposal has cannibalization fields and they are correctly populated.
"""

import pytest
from datetime import date, timedelta
from pathlib import Path
import tempfile
import shutil

from src.domain.models import SKU, SalesRecord, PromoWindow, Transaction, EventType, Stock
from src.persistence.csv_layer import CSVLayer
from src.workflows.order import OrderWorkflow


def test_order_proposal_has_cannibalization_fields():
    """OrderProposal should have all 5 cannibalization fields."""
    with tempfile.TemporaryDirectory() as tmpdir:
        csv = CSVLayer(data_dir=Path(tmpdir))
        workflow = OrderWorkflow(csv_layer=csv, lead_time_days=3)
        
        # Minimal setup
        sku_obj = SKU(sku="TEST_SKU", description="Test", moq=10, pack_size=10)
        csv.write_sku(sku_obj)
        
        # Generate proposal with minimal params
        stock = Stock(sku="TEST_SKU", on_hand=100, on_order=0)
        proposal = workflow.generate_proposal(
            sku="TEST_SKU",
            description="Test",
            current_stock=stock,
            daily_sales_avg=10.0,
            sku_obj=sku_obj,
        )
        
        # Verify cannibalization fields exist
        assert hasattr(proposal, "cannibalization_applied")
        assert hasattr(proposal, "cannibalization_driver_sku")
        assert hasattr(proposal, "cannibalization_downlift_factor")
        assert hasattr(proposal, "cannibalization_confidence")
        assert hasattr(proposal, "cannibalization_note")
        
        # Without settings/history, should default to not applied
        assert proposal.cannibalization_applied is False
        assert proposal.cannibalization_driver_sku == ""
        assert proposal.cannibalization_downlift_factor == 1.0


def test_cannibalization_disabled_by_default():
    """Cannibalization should be disabled when not explicitly enabled in settings."""
    with tempfile.TemporaryDirectory() as tmpdir:
        csv = CSVLayer(data_dir=Path(tmpdir))
        workflow = OrderWorkflow(csv_layer=csv, lead_time_days=3)
        
        # Setup SKU
        sku_obj = SKU(sku="TARGET_SKU", description="Target", moq=10, pack_size=10)
        csv.write_sku(sku_obj)
        csv.write_sku(SKU(sku="DRIVER_SKU", description="Driver", moq=10, pack_size=10))
        
        # Verify settings have cannibalization disabled
        settings = csv.read_settings()
        cannib_enabled = settings.get("promo_cannibalization", {}).get("enabled", {}).get("value", False)
        assert cannib_enabled is False
        
        # Generate proposal
        stock = Stock(sku="TARGET_SKU", on_hand=100, on_order=0)
        proposal = workflow.generate_proposal(
            sku="TARGET_SKU",
            description="Target",
            current_stock=stock,
            daily_sales_avg=10.0,
            sku_obj=sku_obj,
        )
        
        # Cannibalization should not be applied
        assert proposal.cannibalization_applied is False
        assert proposal.cannibalization_driver_sku == ""
        assert proposal.cannibalization_downlift_factor == 1.0
        assert proposal.cannibalization_confidence == ""


def test_cannibalization_fields_initialized_even_if_not_applied():
    """Even when cannibalization is not applied, fields should be initialized with safe defaults."""
    with tempfile.TemporaryDirectory() as tmpdir:
        csv = CSVLayer(data_dir=Path(tmpdir))
        workflow = OrderWorkflow(csv_layer=csv, lead_time_days=3)
        
        sku_obj = SKU(sku="SIMPLE_SKU", description="Simple", moq=10, pack_size=10)
        csv.write_sku(sku_obj)
        
        stock = Stock(sku="SIMPLE_SKU", on_hand=50, on_order=0)
        proposal = workflow.generate_proposal(
            sku="SIMPLE_SKU",
            description="Simple",
            current_stock=stock,
            daily_sales_avg=5.0,
            sku_obj=sku_obj,
        )
        
        # All fields should exist with safe defaults
        assert proposal.cannibalization_applied is False
        assert proposal.cannibalization_driver_sku == ""
        assert proposal.cannibalization_downlift_factor == 1.0  # Neutral (no reduction)
        assert proposal.cannibalization_confidence == ""
        assert isinstance(proposal.cannibalization_note, str)  # Note should be string (empty or with other info)
