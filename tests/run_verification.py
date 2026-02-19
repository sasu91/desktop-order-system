#!/usr/bin/env python3
"""
QA Verification Report Runner
==============================

Esegue test_verification_suite.py e produce un report strutturato
con PASS/FAIL per sezione, golden values e shadow-paths summary.

Uso:
    python tests/run_verification.py [--verbose] [--json]

Output:
    PASS/FAIL per ogni test + summary table con mu_P / sigma_P / Q_final
    per ogni dataset golden.

Author: Desktop Order System QA / GitHub Copilot
Date: February 2026
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Any

WORKSPACE = Path(__file__).parent.parent
FIXTURES  = WORKSPACE / "tests" / "fixtures"

# Golden constants (must stay aligned with test_verification_suite.py)
ASOF     = date(2026, 2, 26)
P        = 7
DELIVERY = ASOF + timedelta(days=P)

# ---------------------------------------------------------------------------
# Section definitions (mirrors test_verification_suite.py structure)
# ---------------------------------------------------------------------------

SECTIONS = {
    "A": ("Pipeline contract",       ["A1","A2","A3","A4"]),
    "B": ("CSL policy coherence",    ["B1","B2","B3","B4"]),
    "C": ("Intermittent demand",     ["C1","C2","C3","C4","C5","C6"]),
    "D": ("Modifiers Engine",        ["D1","D2","D3","D4","D5"]),
    "E": ("End-to-end integration",  ["E1","E2","E3","E4","E5","E6"]),
    "F": ("Shadow paths (probes)",   ["F1","F2","F3","F4"]),
}

SHADOW_PATHS = [
    ("S1 [CRITICAL]", "generate_proposal calls compute_order (old) in CSL branch → sigma incoherent",
     "A3: propose_order_for_sku calls compute_order_v2 not compute_order"),
    ("S2 [CRITICAL]", "generate_proposal bypasses build_demand_distribution entirely",
     "A1: propose_order_for_sku calls build_demand_distribution"),
    ("S3 [MEDIUM]",   "forecast_method in {'croston','sba','tsb','intermittent_auto'} "
                      "silently falls to simple in generate_proposal",
     "C6/E3: propose_order_for_sku correctly routes to intermittent_auto"),
    ("S4 [LOW]",      "Cannibalization not included in _any_modifier_enabled gate "
                      "→ never fires in generate_proposal when promo/event/holiday all False",
     "F4: apply_modifiers handles cannibalization standalone"),
]


# ---------------------------------------------------------------------------
# Compute golden summary values
# ---------------------------------------------------------------------------

def _compute_golden_summary() -> List[Dict[str, Any]]:
    """Run the 4 golden dataset computations and return structured results."""
    sys.path.insert(0, str(WORKSPACE))
    import csv as csv_mod

    from src.domain.demand_builder import build_demand_distribution
    from src.domain.intermittent_forecast import classify_intermittent

    results = []

    # --- DS1 ---
    rows = list(csv_mod.DictReader(open(FIXTURES / "DS1_STABLE.csv")))
    h1 = [{"date": date.fromisoformat(r["date"]), "qty_sold": float(r["qty_sold"])} for r in rows]
    dd1 = build_demand_distribution("simple", h1, P, ASOF)
    clf1 = classify_intermittent([float(r["qty_sold"]) for r in rows])
    results.append({
        "dataset": "DS1_STABLE",
        "method": "simple",
        "n": len(h1),
        "mu_P": round(dd1.mu_P, 4),
        "sigma_P": round(dd1.sigma_P, 4),
        "is_intermittent": clf1.is_intermittent,
        "adi": round(clf1.adi, 3),
        "cv2": round(clf1.cv2, 3),
        "Q_alpha_95": "N/A (legacy)",
    })

    # --- DS2 ---
    rows = list(csv_mod.DictReader(open(FIXTURES / "DS2_VARIABLE.csv")))
    h2 = [{"date": date.fromisoformat(r["date"]), "qty_sold": float(r["qty_sold"])} for r in rows]
    mc_params = {"distribution": "empirical", "n_simulations": 1000,
                 "random_seed": 42, "output_stat": "mean", "output_percentile": 80}
    dd2 = build_demand_distribution("monte_carlo", h2, P, ASOF, mc_params=mc_params)
    q95 = dd2.quantiles.get(0.95, dd2.quantiles.get("0.95", "N/A")) if dd2.quantiles else "N/A"
    results.append({
        "dataset": "DS2_VARIABLE",
        "method": "monte_carlo (seed=42)",
        "n": len(h2),
        "mu_P": round(dd2.mu_P, 4),
        "sigma_P": round(dd2.sigma_P, 4),
        "is_intermittent": False,
        "adi": "N/A",
        "cv2": "N/A",
        "Q_alpha_95": round(float(q95), 4) if q95 != "N/A" else "N/A",
    })

    # --- DS3 ---
    rows = list(csv_mod.DictReader(open(FIXTURES / "DS3_INTERMITTENT.csv")))
    h3 = [{"date": date.fromisoformat(r["date"]), "qty_sold": float(r["qty_sold"])} for r in rows]
    clf3 = classify_intermittent([float(r["qty_sold"]) for r in rows])
    mc_i = {"min_nonzero_observations": 4, "backtest_enabled": True,
             "backtest_periods": 3, "backtest_min_history": 28,
             "alpha_default": 0.1, "fallback_to_simple": True}
    dd3 = build_demand_distribution("intermittent_auto", h3, P, ASOF, mc_params=mc_i)
    results.append({
        "dataset": "DS3_INTERMITTENT",
        "method": f"intermittent_auto → {dd3.intermittent_method or 'simple'}",
        "n": len(h3),
        "mu_P": round(dd3.mu_P, 4),
        "sigma_P": round(dd3.sigma_P, 4),
        "is_intermittent": clf3.is_intermittent,
        "adi": round(clf3.adi, 3),
        "cv2": round(clf3.cv2, 3),
        "Q_alpha_95": "N/A (no quantiles)",
    })

    # --- DS4 ---
    rows = list(csv_mod.DictReader(open(FIXTURES / "DS4_MODIFIERS.csv")))
    h4 = [{"date": date.fromisoformat(r["date"]), "qty_sold": float(r["qty_sold"])} for r in rows]
    dd4 = build_demand_distribution("simple", h4, P, ASOF)
    results.append({
        "dataset": "DS4_MODIFIERS",
        "method": "simple",
        "n": len(h4),
        "mu_P": round(dd4.mu_P, 4),
        "sigma_P": round(dd4.sigma_P, 4),
        "is_intermittent": False,
        "adi": "N/A",
        "cv2": "N/A",
        "Q_alpha_95": "N/A (stacking: 70×1.2×1.1×0.85=78.54)",
    })

    return results


# ---------------------------------------------------------------------------
# Run pytest and parse output
# ---------------------------------------------------------------------------

def _run_pytest(verbose: bool) -> Dict[str, Any]:
    """Run the verification suite and return parsed results."""
    cmd = [
        sys.executable, "-m", "pytest",
        "tests/test_verification_suite.py",
        "-v",
        "--tb=short",
        "--no-header",
        "-q",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(WORKSPACE))
    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def _parse_results(stdout: str) -> Dict[str, str]:
    """Parse pytest -v output → {test_id: 'PASS'|'FAIL'}."""
    results = {}
    for line in stdout.splitlines():
        # e.g.: "tests/test_verification_suite.py::TestPipelineContract::test_A1_... PASSED"
        if "::" not in line:
            continue
        status = None
        if " PASSED" in line:
            status = "PASS"
        elif " FAILED" in line:
            status = "FAIL"
        elif " ERROR" in line:
            status = "ERROR"
        if status is None:
            continue
        # Extract test name
        parts = line.split("::")
        test_id = parts[-1].split(" ")[0]  # e.g. test_A1_propose_calls_...
        # Extract letter key: A1, B2, etc.
        for key in test_id.split("_"):
            if len(key) >= 2 and key[0].isalpha() and key[1:].isdigit():
                results[key.upper()] = status
                break
        results[test_id] = status
    return results


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

_W = 80

def _hr(char: str = "=") -> str:
    return char * _W


def _banner(title: str) -> str:
    pad = (_W - len(title) - 2) // 2
    return f"{'='*pad} {title} {'='*(_W - pad - len(title) - 2)}"


def _print_report(parsed: Dict[str, str], golden: List[Dict], pytest_out: Dict,
                  verbose: bool = False) -> bool:
    """Render the full CLI report. Returns True if all tests passed."""
    all_passed = True

    print(_banner("QA VERIFICATION REPORT — Desktop Order System"))
    print(f"  Date: {date.today().isoformat()}")
    print(f"  Suite: tests/test_verification_suite.py")
    print(f"  Total tests: 29")
    print()

    # --- Section results ---
    print(_banner("SECTION RESULTS"))
    section_totals: Dict[str, tuple] = {}
    for sec_key, (sec_name, test_ids) in SECTIONS.items():
        passed = failed = 0
        lines = []
        for tid in test_ids:
            status = parsed.get(tid, parsed.get(f"test_{tid.lower()}", "?"))
            if not isinstance(status, str) or status not in ("PASS","FAIL","ERROR"):
                # Try case-insensitive lookup
                matches = [v for k,v in parsed.items()
                           if k.upper().startswith(tid.upper()) and v in ("PASS","FAIL","ERROR")]
                status = matches[0] if matches else "?"
            icon = "✓" if status == "PASS" else ("✗" if status == "FAIL" else "?")
            lines.append(f"  [{icon}] {sec_key}{tid[1:] if len(tid)>1 else ''}  {status}")
            if status == "PASS": passed += 1
            elif status in ("FAIL","ERROR"):
                failed += 1
                all_passed = False
        section_totals[sec_key] = (passed, failed, len(test_ids))
        print(f"\n  Section {sec_key} — {sec_name}  ({passed}/{len(test_ids)} PASS)")
        if verbose:
            for l in lines:
                print(l)

    # --- Section summary table ---
    print()
    print(_hr("-"))
    print(f"  {'Section':<6} {'Name':<32} {'Passed':>7} {'Failed':>7} {'Status':>8}")
    print(_hr("-"))
    for sec_key, (passed, failed, total) in section_totals.items():
        status = "PASS" if failed == 0 else "FAIL"
        icon = "✓" if status == "PASS" else "✗"
        print(f"  {icon} {sec_key:<5} {SECTIONS[sec_key][0]:<32} {passed}/{total:>3}   "
              f"{failed:>4}       {status}")
    print(_hr("-"))

    # --- Golden datasets ---
    print()
    print(_banner("GOLDEN DATASET VALUES"))
    col = "{:<22} {:<28} {:>7} {:>10} {:>12}"
    print(col.format("Dataset", "Method", "mu_P", "sigma_P", "n"))
    print(_hr("-"))
    for g in golden:
        print(col.format(
            g["dataset"],
            str(g["method"])[:28],
            str(g["mu_P"]),
            str(g["sigma_P"]),
            str(g["n"]),
        ))
        intermittent_info = (
            f"    ADI={g['adi']}, CV2={g['cv2']}, "
            f"is_intermittent={g['is_intermittent']}"
            if g.get("adi") != "N/A" else ""
        )
        if intermittent_info:
            print(intermittent_info)
        if "Q_alpha_95" in g and g["Q_alpha_95"] != "N/A":
            print(f"    Q(0.95)={g['Q_alpha_95']}")
        elif "Q_alpha_95" in g:
            print(f"    {g['Q_alpha_95']}")
    print(_hr("-"))

    # --- Shadow paths summary ---
    print()
    print(_banner("SHADOW PATHS SUMMARY"))
    for sid, description, mitigation in SHADOW_PATHS:
        print(f"\n  {sid}")
        print(f"  Description:  {description}")
        print(f"  Mitigated by: {mitigation}")
    print()
    print("  NOTE: Shadow paths S1–S4 exist in generate_proposal() (legacy GUI path).")
    print("  They are NOT present in propose_order_for_sku() (clean facade).")
    print("  Tests A1–A3 enforce the clean path is always used for new logic.")

    # --- Coverage matrix ---
    print()
    print(_banner("COVERAGE MATRIX"))
    matrix = [
        ("Improvement",             "Test IDs",     "Entry point verified"),
        ("─"*28,                    "─"*20,         "─"*26),
        ("1. Intermittent demand",  "C1–C6",        "build_demand_distribution"),
        ("2. Modifiers Engine",     "D1–D5, E6",    "apply_modifiers"),
        ("3. CSL via compute_v2",   "B1–B4, A3",    "compute_order_v2"),
        ("4. Clean single-entry",   "A1–A4, E1–E6", "propose_order_for_sku"),
    ]
    for row in matrix:
        print(f"  {row[0]:<30} {row[1]:<22} {row[2]}")

    # --- Final verdict ---
    print()
    print(_hr("="))
    total_pass = sum(1 for v in parsed.values() if v == "PASS")
    total_fail = sum(1 for v in parsed.values() if v == "FAIL")
    verdict = "ALL TESTS PASS ✓" if pytest_out["returncode"] == 0 else f"FAILURES DETECTED ✗"
    print(f"  VERDICT: {verdict}   ({total_pass} passed, {total_fail} failed / 29 total)")
    print(_hr("="))

    return pytest_out["returncode"] == 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="QA Verification Report for Desktop Order System"
    )
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show individual test IDs in section blocks")
    parser.add_argument("--json", action="store_true",
                        help="Also write report.json in tests/ directory")
    args = parser.parse_args()

    print("Running verification suite...\n")
    pytest_out = _run_pytest(verbose=args.verbose)
    parsed     = _parse_results(pytest_out["stdout"])

    print("Computing golden dataset values...")
    try:
        golden = _compute_golden_summary()
    except Exception as e:
        golden = [{"dataset": f"ERROR: {e}", "method": "", "n": 0,
                   "mu_P": 0, "sigma_P": 0, "is_intermittent": None,
                   "adi": "ERR", "cv2": "ERR", "Q_alpha_95": "ERR"}]

    print()
    success = _print_report(parsed, golden, pytest_out, verbose=args.verbose)

    if args.verbose and pytest_out.get("stderr"):
        print("\n--- pytest stderr ---")
        print(pytest_out["stderr"][:2000])

    if args.json:
        report_path = WORKSPACE / "tests" / "verification_report.json"
        with open(report_path, "w") as f:
            json.dump({
                "date": date.today().isoformat(),
                "passed": success,
                "pytest_returncode": pytest_out["returncode"],
                "parsed_results": parsed,
                "golden_datasets": golden,
            }, f, indent=2, default=str)
        print(f"\nJSON report written to: {report_path}")

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
