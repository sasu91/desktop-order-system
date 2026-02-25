"""
dos_backend/smoke.py — minimal import + runtime sanity check.

Usage:
    # From project root with the package installed in editable mode:
    python -m dos_backend.smoke

    # Or directly:
    python backend/dos_backend/smoke.py

Exit codes:
    0  — all checks passed
    1  — at least one check failed (details printed to stderr)
"""
import sys
import traceback
from datetime import date, timedelta
from pathlib import Path


def _section(title: str) -> None:
    print(f"\n--- {title} ---")


def main() -> None:
    errors: list[str] = []

    # ------------------------------------------------------------------
    # 1. Core imports
    # ------------------------------------------------------------------
    _section("1. Import check")

    try:
        from dos_backend.domain.models import SKU, Transaction, EventType, Stock
        from dos_backend.domain.ledger import StockCalculator
        print("  dos_backend.domain          OK")
    except Exception as exc:
        errors.append(f"domain import: {exc}")
        print(f"  dos_backend.domain          FAIL: {exc}")

    try:
        from dos_backend.persistence.storage_adapter import StorageAdapter
        print("  dos_backend.persistence     OK")
    except Exception as exc:
        errors.append(f"persistence import: {exc}")
        print(f"  dos_backend.persistence     FAIL: {exc}")

    try:
        from dos_backend.utils.paths import get_data_dir
        print("  dos_backend.utils.paths     OK")
    except Exception as exc:
        errors.append(f"utils.paths import: {exc}")
        print(f"  dos_backend.utils.paths     FAIL: {exc}")

    # Bail early if basic imports failed — nothing else will work.
    if errors:
        _print_summary(errors)
        sys.exit(1)

    # ------------------------------------------------------------------
    # 2. StorageAdapter initialisation (uses a temp in-memory data dir)
    # ------------------------------------------------------------------
    _section("2. StorageAdapter init")

    import tempfile
    with tempfile.TemporaryDirectory(prefix="dos_smoke_") as tmp:
        from dos_backend.persistence.storage_adapter import StorageAdapter

        try:
            adapter = StorageAdapter(data_dir=Path(tmp), force_backend="csv")
            print(f"  StorageAdapter (csv)  backend={adapter.get_backend()}  OK")
        except Exception as exc:
            errors.append(f"StorageAdapter init: {exc}")
            print(f"  StorageAdapter init   FAIL: {exc}")
            traceback.print_exc()
            _print_summary(errors)
            sys.exit(1)

        # ------------------------------------------------------------------
        # 3. Read SKUs (may be empty — that is fine)
        # ------------------------------------------------------------------
        _section("3. read_skus()")
        try:
            skus = adapter.read_skus()
            print(f"  SKUs found: {len(skus)}")
        except Exception as exc:
            errors.append(f"read_skus: {exc}")
            print(f"  read_skus FAIL: {exc}")
            skus = []

        # ------------------------------------------------------------------
        # 4. Read transactions (may be empty — that is fine)
        # ------------------------------------------------------------------
        _section("4. read_transactions()")
        try:
            transactions = adapter.read_transactions()
            print(f"  Transactions found: {len(transactions)}")
        except Exception as exc:
            errors.append(f"read_transactions: {exc}")
            print(f"  read_transactions FAIL: {exc}")
            transactions = []

        # ------------------------------------------------------------------
        # 5. StockCalculator.calculate_asof on a real or synthetic SKU
        # ------------------------------------------------------------------
        _section("5. StockCalculator.calculate_asof()")

        if skus:
            target_sku = skus[0].sku
            print(f"  Using existing SKU: '{target_sku}'")
        else:
            # Inject a minimal synthetic SKU so we can exercise the path
            from dos_backend.domain.models import SKU, Transaction, EventType
            _synthetic_sku = "SMOKE-001"
            synthetic_txn = Transaction(
                date=date.today() - timedelta(days=1),
                sku=_synthetic_sku,
                event=EventType.SNAPSHOT,
                qty=42,
            )
            transactions = [synthetic_txn]
            target_sku = _synthetic_sku
            print(f"  No stored SKUs — using synthetic SKU: '{target_sku}'")

        try:
            from dos_backend.domain.ledger import StockCalculator

            stock = StockCalculator.calculate_asof(
                sku=target_sku,
                asof_date=date.today(),
                transactions=transactions,
            )
            print(
                f"  calculate_asof result: "
                f"on_hand={stock.on_hand}, on_order={stock.on_order}"
            )
        except Exception as exc:
            errors.append(f"calculate_asof: {exc}")
            print(f"  calculate_asof FAIL: {exc}")
            traceback.print_exc()

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    _print_summary(errors)
    sys.exit(1 if errors else 0)


def _print_summary(errors: list[str]) -> None:
    print()
    if errors:
        print("SMOKE TEST FAILED", file=sys.stderr)
        for e in errors:
            print(f"  ✗ {e}", file=sys.stderr)
        sys.exit(1)
    else:
        print("OK")


if __name__ == "__main__":
    main()
