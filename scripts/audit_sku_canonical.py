"""
SKU Canonical Audit Tool — read-only scanner.

Scans the four operational CSV files for:
  1. Non-canonical SKU values (not exactly 7 numeric digits)
  2. Orphan SKUs (present in order_logs/receiving_logs/transactions but absent in skus.csv catalog)

Usage (from project root):
    python scripts/audit_sku_canonical.py [--data-dir PATH]

Output: human-readable summary + machine-readable JSON written to
    data/audit_sku_canonical_YYYYMMDD_HHMMSS.json

Takes no destructive action — safe to run at any time.
"""

import argparse
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Pattern
# ---------------------------------------------------------------------------
_SKU_RE = re.compile(r'^\d{7}$')

FILES_WITH_SKU = {
    "skus.csv": "sku",
    "order_logs.csv": "sku",
    "receiving_logs.csv": "sku",
    "transactions.csv": "sku",
}


def _is_canonical(value: str) -> bool:
    return bool(_SKU_RE.match(value))


def _read_csv_safe(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def audit(data_dir: Path) -> dict:
    """Run read-only audit. Returns result dict."""
    catalog_rows = _read_csv_safe(data_dir / "skus.csv")
    catalog_skus: set[str] = {r.get("sku", "").strip() for r in catalog_rows}

    results: dict = {}
    non_canonical_total = 0
    orphan_total = 0

    for filename, sku_col in FILES_WITH_SKU.items():
        path = data_dir / filename
        rows = _read_csv_safe(path)

        non_canonical: list[dict] = []
        orphan: list[dict] = []

        for lineno, row in enumerate(rows, start=2):  # 1-based, row 1 = header
            sku_raw = row.get(sku_col, "")
            sku = sku_raw.strip()

            if not _is_canonical(sku):
                non_canonical.append({
                    "line": lineno,
                    "sku_raw": sku_raw,
                    "row_keys": list(row.keys()),
                    "row_sample": {k: v for k, v in row.items()
                                   if k in (sku_col, "date", "order_id", "document_id", "receipt_id")},
                })

            elif filename != "skus.csv" and sku not in catalog_skus:
                orphan.append({
                    "line": lineno,
                    "sku": sku,
                    "row_sample": {k: v for k, v in row.items()
                                   if k in (sku_col, "date", "order_id", "document_id", "receipt_id")},
                })

        non_canonical_total += len(non_canonical)
        orphan_total += len(orphan)

        results[filename] = {
            "total_rows": len(rows),
            "non_canonical_count": len(non_canonical),
            "orphan_count": len(orphan),
            "non_canonical": non_canonical,
            "orphan": orphan,
        }

    summary = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data_dir": str(data_dir),
        "total_non_canonical": non_canonical_total,
        "total_orphan": orphan_total,
        "files": results,
    }
    return summary


def _print_report(summary: dict) -> None:
    ts = summary["timestamp"]
    print(f"\n{'='*60}")
    print(f"  SKU Canonical Audit — {ts}")
    print(f"  Data dir: {summary['data_dir']}")
    print(f"{'='*60}")
    print(f"  Non-canonical SKUs found : {summary['total_non_canonical']}")
    print(f"  Orphan SKUs found        : {summary['total_orphan']}")
    print(f"{'='*60}\n")

    for filename, info in summary["files"].items():
        status = "OK" if (info["non_canonical_count"] == 0 and info["orphan_count"] == 0) else "PROBLEMS"
        print(f"  {filename:30s}  rows={info['total_rows']:6d}  "
              f"non_canonical={info['non_canonical_count']:4d}  "
              f"orphan={info['orphan_count']:4d}  [{status}]")

        for rec in info["non_canonical"]:
            print(f"    [NON-CANONICAL] line {rec['line']}: sku_raw={rec['sku_raw']!r}  "
                  f"sample={rec['row_sample']}")
        for rec in info["orphan"]:
            print(f"    [ORPHAN]        line {rec['line']}: sku={rec['sku']!r}  "
                  f"sample={rec['row_sample']}")

    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only SKU canonical audit scanner.")
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Path to data directory (default: auto-detect from project layout)",
    )
    parser.add_argument(
        "--json-out",
        default=None,
        help="Write JSON report to this path (default: data/audit_sku_canonical_<ts>.json)",
    )
    args = parser.parse_args()

    # Resolve data dir
    if args.data_dir:
        data_dir = Path(args.data_dir)
    else:
        # Try standard locations relative to this script
        script_dir = Path(__file__).resolve().parent
        candidates = [
            script_dir.parent / "data",
            script_dir.parent / "src" / "data",
        ]
        data_dir = next((p for p in candidates if p.is_dir()), candidates[0])

    if not data_dir.is_dir():
        print(f"ERROR: data directory not found: {data_dir}", file=sys.stderr)
        return 2

    summary = audit(data_dir)
    _print_report(summary)

    # Determine JSON output path
    if args.json_out:
        json_path = Path(args.json_out)
    else:
        ts_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_path = data_dir / f"audit_sku_canonical_{ts_tag}.json"

    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  JSON report written to: {json_path}\n")

    return 1 if (summary["total_non_canonical"] > 0 or summary["total_orphan"] > 0) else 0


if __name__ == "__main__":
    sys.exit(main())
