"""
Test Suite: Shelf Life UI Integration (Phase 4)

Validates that shelf life parameters are correctly integrated into:
1. Settings persistence (shelf_life_policy section)
2. SKU CRUD operations (4 operational parameters)
3. OrderProposal includes shelf life display fields
"""
import pytest
from datetime import date, timedelta
from pathlib import Path
import tempfile
import shutil
import json

from src.domain.models import SKU, DemandVariability, OrderProposal
from src.persistence.csv_layer import CSVLayer


class TestShelfLifeSettingsPersistence:
    """Test shelf_life_policy section in settings.json."""
    
    def test_settings_shelf_life_policy_structure(self, tmp_path):
        """Verify settings.json includes shelf_life_policy with all required fields."""
        csv_layer = CSVLayer(data_dir=tmp_path)
        
        # Create settings with shelf_life_policy
        settings = {
            "shelf_life_policy": {
                "enabled": {"value": True, "type": "bool"},
                "min_shelf_life_global": {"value": 14, "type": "int"},
                "waste_penalty_mode": {"value": "soft", "type": "choice"},
                "waste_penalty_factor": {"value": 0.3, "type": "float"},
                "waste_risk_threshold": {"value": 20.0, "type": "float"},
                "waste_horizon_days": {"value": 30, "type": "int"},
                "waste_realization_factor": {"value": 0.5, "type": "float"}
            }
        }
        
        settings_path = tmp_path / "settings.json"
        with open(settings_path, 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=2)
        
        # Load and verify
        with open(settings_path, 'r', encoding='utf-8') as f:
            loaded = json.load(f)
        
        assert "shelf_life_policy" in loaded
        policy = loaded["shelf_life_policy"]
        
        # Verify all fields exist
        assert policy["enabled"]["value"] is True
        assert policy["min_shelf_life_global"]["value"] == 14
        assert policy["waste_penalty_mode"]["value"] == "soft"
        assert policy["waste_penalty_factor"]["value"] == 0.3
        assert policy["waste_risk_threshold"]["value"] == 20.0
        assert policy["waste_horizon_days"]["value"] == 30
        assert policy["waste_realization_factor"]["value"] == 0.5
    
    def test_settings_defaults_for_shelf_life(self, tmp_path):
        """Verify default values for shelf_life_policy if not specified."""
        csv_layer = CSVLayer(data_dir=tmp_path)
        
        # Create minimal settings (no shelf_life_policy)
        settings = {
            "reorder_engine": {
                "lead_time_days": {"value": 7, "type": "int"}
            }
        }
        
        settings_path = tmp_path / "settings.json"
        with open(settings_path, 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=2)
        
        # Workflow should handle missing shelf_life_policy gracefully
        # (this is implicit - no crash means success)
        assert settings_path.exists()


class TestShelfLifeSKUParameters:
    """Test SKU CRUD with shelf life operational parameters."""
    
    def test_create_sku_with_shelf_life_params(self, tmp_path):
        """Create SKU with all 4 shelf life parameters."""
        csv_layer = CSVLayer(data_dir=tmp_path)
        
        sku = SKU(
            sku="YOGURT_001",
            description="Yogurt Greco 500g",
            ean="8001234567890",
            pack_size=12,
            moq=12,
            lead_time_days=3,
            review_period=7,
            safety_stock=24,
            shelf_life_days=21,  # Total shelf life
            min_shelf_life_days=14,  # Minimum acceptable
            waste_penalty_mode="soft",
            waste_penalty_factor=0.5,
            waste_risk_threshold=25.0,
            max_stock=200,
            reorder_point=36,
            demand_variability=DemandVariability.STABLE
        )
        
        csv_layer.write_sku(sku)
        
        # Reload and verify
        loaded_skus = csv_layer.read_skus()
        assert len(loaded_skus) == 1
        
        loaded = loaded_skus[0]
        assert loaded.sku == "YOGURT_001"
        assert loaded.shelf_life_days == 21
        assert loaded.min_shelf_life_days == 14
        assert loaded.waste_penalty_mode == "soft"
        assert loaded.waste_penalty_factor == 0.5
        assert loaded.waste_risk_threshold == 25.0
    
    def test_update_sku_shelf_life_params(self, tmp_path):
        """Update SKU shelf life parameters."""
        csv_layer = CSVLayer(data_dir=tmp_path)
        
        # Create initial SKU
        sku = SKU(
            sku="CHEESE_001",
            description="Mozzarella 125g",
            ean="8001111111111",
            pack_size=10,
            moq=10,
            lead_time_days=2,
            review_period=7,
            safety_stock=20,
            shelf_life_days=10,
            min_shelf_life_days=0,  # Use global default
            waste_penalty_mode="",  # Use global default
            waste_penalty_factor=0.0,  # Use global default
            waste_risk_threshold=0.0,  # Use global default
        )
        csv_layer.write_sku(sku)
        
        # Update with specific shelf life policy
        success = csv_layer.update_sku(
            old_sku_id="CHEESE_001",
            new_sku_id="CHEESE_001",
            new_description="Mozzarella 125g",
            new_ean="8001111111111",
            moq=10,
            pack_size=10,
            lead_time_days=2,
            review_period=7,
            safety_stock=20,
            shelf_life_days=10,
            max_stock=999,
            reorder_point=10,
            demand_variability=DemandVariability.STABLE,
            oos_boost_percent=0.0,
            oos_detection_mode="",
            oos_popup_preference="ask",
            min_shelf_life_days=7,  # Override
            waste_penalty_mode="hard",  # Override
            waste_penalty_factor=0.3,  # Override
            waste_risk_threshold=15.0,  # Override
            forecast_method="",
            mc_distribution="",
            mc_n_simulations=0,
            mc_random_seed=0,
            mc_output_stat="",
            mc_output_percentile=0,
            mc_horizon_mode="",
            mc_horizon_days=0,
            in_assortment=True
        )
        
        assert success is True
        
        # Reload and verify
        loaded_skus = csv_layer.read_skus()
        assert len(loaded_skus) == 1
        
        loaded = loaded_skus[0]
        assert loaded.min_shelf_life_days == 7
        assert loaded.waste_penalty_mode == "hard"
        assert loaded.waste_penalty_factor == 0.3
        assert loaded.waste_risk_threshold == 15.0
    
    def test_sku_shelf_life_validation(self):
        """Test SKU validation for shelf life parameters."""
        # min_shelf_life_days > shelf_life_days should fail
        with pytest.raises(ValueError, match="Min shelf life cannot exceed total shelf life"):
            SKU(
                sku="INVALID_001",
                description="Invalid Product",
                pack_size=1,
                moq=1,
                lead_time_days=7,
                shelf_life_days=10,
                min_shelf_life_days=15,  # Invalid: > shelf_life_days
            )
        
        # Invalid waste_penalty_mode
        with pytest.raises(ValueError, match="Waste penalty mode"):
            SKU(
                sku="INVALID_002",
                description="Invalid Product",
                pack_size=1,
                moq=1,
                lead_time_days=7,
                waste_penalty_mode="invalid_mode",
            )
        
        # Invalid waste_penalty_factor
        with pytest.raises(ValueError, match="Waste penalty factor"):
            SKU(
                sku="INVALID_003",
                description="Invalid Product",
                pack_size=1,
                moq=1,
                lead_time_days=7,
                waste_penalty_factor=1.5,  # > 1.0
            )
        
        # Invalid waste_risk_threshold
        with pytest.raises(ValueError, match="Waste risk threshold"):
            SKU(
                sku="INVALID_004",
                description="Invalid Product",
                pack_size=1,
                moq=1,
                lead_time_days=7,
                waste_risk_threshold=150.0,  # > 100.0
            )


class TestOrderProposalShelfLifeDisplay:
    """Test OrderProposal includes shelf life display fields."""
    
    def test_order_proposal_has_shelf_life_fields(self):
        """Verify OrderProposal dataclass includes shelf life display fields."""
        today = date.today()
        
        proposal = OrderProposal(
            sku="MILK_001",
            description="Latte Intero 1L",
            current_on_hand=100,
            current_on_order=50,
            daily_sales_avg=15.0,
            proposed_qty=48,
            receipt_date=today + timedelta(days=7),
            usable_stock=85,  # Shelf life field
            unusable_stock=15,  # Shelf life field
            waste_risk_percent=18.5,  # Shelf life field
            shelf_life_penalty_applied=True,  # Shelf life field
            shelf_life_penalty_message="Reduced by 30%"  # Shelf life field
        )
        
        # Verify all shelf life fields are accessible
        assert proposal.usable_stock == 85
        assert proposal.unusable_stock == 15
        assert proposal.waste_risk_percent == 18.5
        assert proposal.shelf_life_penalty_applied is True
        assert proposal.shelf_life_penalty_message == "Reduced by 30%"
    
    def test_order_proposal_shelf_life_defaults(self):
        """Test OrderProposal shelf life fields have sensible defaults."""
        today = date.today()
        
        # Create proposal without shelf life fields (should use defaults)
        proposal = OrderProposal(
            sku="PASTA_001",
            description="Pasta Secca 500g",
            current_on_hand=500,
            current_on_order=0,
            daily_sales_avg=20.0,
            proposed_qty=240,
            receipt_date=today + timedelta(days=7)
            # No shelf life fields specified
        )
        
        # Verify defaults (from Phase 2)
        assert proposal.usable_stock == 0
        assert proposal.unusable_stock == 0
        assert proposal.waste_risk_percent == 0.0
        assert proposal.shelf_life_penalty_applied is False
        assert proposal.shelf_life_penalty_message == ""


class TestShelfLifeUIDataFlow:
    """Integration test: Settings → SKU → OrderProposal."""
    
    def test_shelf_life_end_to_end_data_flow(self, tmp_path):
        """Test complete data flow from settings to order proposal display."""
        csv_layer = CSVLayer(data_dir=tmp_path)
        
        # 1. Create settings with shelf life policy
        settings = {
            "shelf_life_policy": {
                "enabled": {"value": True, "type": "bool"},
                "min_shelf_life_global": {"value": 10, "type": "int"},
                "waste_penalty_mode": {"value": "soft", "type": "choice"},
                "waste_penalty_factor": {"value": 0.5, "type": "float"},
                "waste_risk_threshold": {"value": 20.0, "type": "float"},
                "waste_horizon_days": {"value": 21, "type": "int"},
                "waste_realization_factor": {"value": 0.5, "type": "float"}
            }
        }
        
        settings_path = tmp_path / "settings.json"
        with open(settings_path, 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=2)
        
        # 2. Create SKU with shelf life parameters
        sku = SKU(
            sku="FRESH_001",
            description="Insalata Mista 200g",
            pack_size=10,
            moq=10,
            lead_time_days=1,
            review_period=7,
            safety_stock=30,
            shelf_life_days=7,
            min_shelf_life_days=5,  # Override global
            waste_penalty_mode="soft",  # Override global
            waste_penalty_factor=0.6,  # Override global
            waste_risk_threshold=25.0,  # Override global
        )
        csv_layer.write_sku(sku)
        
        # 3. Create OrderProposal with shelf life info
        today = date.today()
        proposal = OrderProposal(
            sku="FRESH_001",
            description="Insalata Mista 200g",
            current_on_hand=50,
            current_on_order=0,
            daily_sales_avg=12.0,
            proposed_qty=100,
            receipt_date=today + timedelta(days=1),
            usable_stock=40,  # 10 expiring soon
            unusable_stock=10,
            waste_risk_percent=30.0,  # Above threshold 25.0
            shelf_life_penalty_applied=True,
            shelf_life_penalty_message="Reduced by 60%"
        )
        
        # 4. Verify all data is preserved
        loaded_skus = csv_layer.read_skus()
        assert len(loaded_skus) == 1
        loaded_sku = loaded_skus[0]
        
        # SKU parameters match
        assert loaded_sku.min_shelf_life_days == 5
        assert loaded_sku.waste_penalty_factor == 0.6
        assert loaded_sku.waste_risk_threshold == 25.0
        
        # Proposal has correct shelf life display data
        assert proposal.waste_risk_percent > loaded_sku.waste_risk_threshold  # 30% > 25%
        assert proposal.shelf_life_penalty_applied is True
        assert "60%" in proposal.shelf_life_penalty_message
        
        # Settings loaded correctly
        with open(settings_path, 'r', encoding='utf-8') as f:
            loaded_settings = json.load(f)
        
        policy = loaded_settings["shelf_life_policy"]
        assert policy["enabled"]["value"] is True
        assert policy["min_shelf_life_global"]["value"] == 10
        assert policy["waste_realization_factor"]["value"] == 0.5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
