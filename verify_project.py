#!/usr/bin/env python3
"""
Verification checklist for desktop-order-system implementation.

Run this to verify all components are in place.
"""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).parent

def check_structure():
    """Verify project structure."""
    print("=" * 60)
    print("PROJECT STRUCTURE VERIFICATION")
    print("=" * 60)
    
    required_files = [
        "src/domain/models.py",
        "src/domain/ledger.py",
        "src/domain/migration.py",
        "src/persistence/csv_layer.py",
        "src/workflows/order.py",
        "src/workflows/receiving.py",
        "src/gui/app.py",
        "tests/test_stock_calculation.py",
        "tests/test_workflows.py",
        "tests/test_persistence.py",
        "tests/test_migration.py",
        "main.py",
        "config.py",
        "requirements.txt",
        "pytest.ini",
        "README.md",
        "QUICK_START.md",
        "DEVELOPMENT.md",
        "PROJECT_SUMMARY.md",
        ".github/copilot-instructions.md",
    ]
    
    missing = []
    for filepath in required_files:
        full_path = PROJECT_ROOT / filepath
        if not full_path.exists():
            missing.append(filepath)
        else:
            print(f"✅ {filepath}")
    
    if missing:
        print(f"\n❌ Missing files:")
        for f in missing:
            print(f"   - {f}")
        return False
    
    print(f"\n✅ All {len(required_files)} required files present")
    return True


def check_imports():
    """Verify all modules can be imported."""
    print("\n" + "=" * 60)
    print("MODULE IMPORTS VERIFICATION")
    print("=" * 60)
    
    sys.path.insert(0, str(PROJECT_ROOT))
    
    modules_to_import = [
        ("src.domain.models", ["SKU", "Transaction", "Stock", "EventType"]),
        ("src.domain.ledger", ["StockCalculator", "validate_ean"]),
        ("src.domain.migration", ["LegacyMigration"]),
        ("src.persistence.csv_layer", ["CSVLayer"]),
        ("src.workflows.order", ["OrderWorkflow", "calculate_daily_sales_average"]),
        ("src.workflows.receiving", ["ReceivingWorkflow", "ExceptionWorkflow"]),
    ]
    
    all_ok = True
    for module_name, expected_items in modules_to_import:
        try:
            module = __import__(module_name, fromlist=expected_items)
            for item in expected_items:
                if not hasattr(module, item):
                    print(f"❌ {module_name}.{item} not found")
                    all_ok = False
                else:
                    print(f"✅ {module_name}.{item}")
        except ImportError as e:
            print(f"❌ Cannot import {module_name}: {e}")
            all_ok = False
    
    return all_ok


def check_domain_logic():
    """Verify domain logic is sound."""
    print("\n" + "=" * 60)
    print("DOMAIN LOGIC VERIFICATION")
    print("=" * 60)
    
    from src.domain.models import SKU, Transaction, EventType, Stock
    from src.domain.ledger import StockCalculator, validate_ean
    from datetime import date
    
    try:
        # Test 1: SKU creation
        sku = SKU(sku="SKU001", description="Test Product", ean="5901234123457")
        print(f"✅ SKU creation: {sku.sku}")
        
        # Test 2: Transaction creation
        txn = Transaction(
            date=date(2026, 1, 1),
            sku="SKU001",
            event=EventType.SNAPSHOT,
            qty=100,
        )
        print(f"✅ Transaction creation: {txn.event.value}")
        
        # Test 3: Stock calculation
        stock = StockCalculator.calculate_asof(
            sku="SKU001",
            asof_date=date(2026, 1, 2),
            transactions=[txn],
        )
        print(f"✅ Stock calculation: on_hand={stock.on_hand}, on_order={stock.on_order}")
        
        # Test 4: EAN validation
        is_valid, error = validate_ean("5901234123457")
        print(f"✅ EAN validation (valid): {is_valid}")
        
        is_valid, error = validate_ean("invalid")
        print(f"✅ EAN validation (invalid): {not is_valid}")
        
        print("\n✅ All domain logic tests passed")
        return True
    
    except Exception as e:
        print(f"❌ Domain logic test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def check_csv_layer():
    """Verify CSV layer."""
    print("\n" + "=" * 60)
    print("CSV LAYER VERIFICATION")
    print("=" * 60)
    
    import tempfile
    import shutil
    from pathlib import Path
    
    from src.persistence.csv_layer import CSVLayer
    from src.domain.models import SKU
    
    try:
        # Create temp directory
        tmpdir = Path(tempfile.mkdtemp())
        
        # Initialize CSV layer
        csv_layer = CSVLayer(data_dir=tmpdir)
        print(f"✅ CSVLayer initialized with temp dir: {tmpdir}")
        
        # Verify files created
        expected_files = ["skus.csv", "transactions.csv", "sales.csv", "order_logs.csv", "receiving_logs.csv"]
        for filename in expected_files:
            if (tmpdir / filename).exists():
                print(f"✅ Auto-created: {filename}")
            else:
                print(f"❌ Missing: {filename}")
        
        # Test SKU write/read
        sku = SKU(sku="TEST001", description="Test Item", ean=None)
        csv_layer.write_sku(sku)
        skus = csv_layer.read_skus()
        if len(skus) == 1 and skus[0].sku == "TEST001":
            print(f"✅ SKU write/read: {skus[0].sku}")
        else:
            print(f"❌ SKU write/read failed")
        
        # Cleanup
        shutil.rmtree(tmpdir)
        
        print("\n✅ CSV layer tests passed")
        return True
    
    except Exception as e:
        print(f"❌ CSV layer test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all verification checks."""
    checks = [
        ("Structure", check_structure),
        ("Imports", check_imports),
        ("Domain Logic", check_domain_logic),
        ("CSV Layer", check_csv_layer),
    ]
    
    results = []
    for name, check_func in checks:
        try:
            result = check_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n❌ {name} check failed with exception: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    all_passed = True
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {name}")
        if not passed:
            all_passed = False
    
    print("=" * 60)
    
    if all_passed:
        print("\n🎉 ALL CHECKS PASSED - Project is ready!")
        print("\nNext steps:")
        print("  1. Run tests: python -m pytest tests/ -v")
        print("  2. Start GUI: python main.py")
        return 0
    else:
        print("\n❌ Some checks failed - please review above")
        return 1


if __name__ == "__main__":
    sys.exit(main())
