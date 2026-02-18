#!/usr/bin/env python3
"""
Database Performance Profiling Tool

Profiles critical database operations and provides:
- Execution time measurements
- Query plan analysis (EXPLAIN QUERY PLAN)
- Recommendations for optimization
- Comparison with performance targets

Usage:
    python tools/profile_db.py [--verbose] [--explain] [--benchmark]
"""

import sys
import sqlite3
import time
from pathlib import Path
from datetime import date, timedelta
from typing import Dict, Any, List, Tuple, Optional
import random
import argparse

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db import DB_PATH, open_connection, close_connection
from src.repositories import SKURepository, LedgerRepository, OrdersRepository


# ============================================================
# Performance Targets
# ============================================================

PERFORMANCE_TARGETS = {
    "get_sku": 1,  # ms
    "list_all_skus": 50,  # ms (100 SKUs)
    "list_all_skus_1000": 200,  # ms (1000 SKUs)
    "get_transactions_for_sku": 10,  # ms (100 transactions)
    "get_transactions_for_sku_1000": 50,  # ms (1000 transactions)
    "get_all_transactions": 100,  # ms (10K transactions)
    "get_orders_for_sku": 10,  # ms
    "get_lots_for_sku": 5,  # ms (10 lots)
    "get_lots_for_sku_100": 20,  # ms (100 lots)
    "stock_calculation_single_sku": 10,  # ms (typical: 100 transactions)
    "stock_calculation_all_skus": 1000,  # ms (100 SKUs, 10K transactions)
}


# ============================================================
# Profiling Functions
# ============================================================

def profile_operation(name: str, func, target_ms: Optional[float] = None) -> Dict[str, Any]:
    """
    Profile a single operation.
    
    Returns:
        {
            "name": str,
            "time_ms": float,
            "target_ms": float,
            "status": "PASS" | "WARN" | "FAIL",
            "result": Any  # operation result
        }
    """
    start = time.perf_counter()
    result = func()
    end = time.perf_counter()
    
    time_ms = (end - start) * 1000
    
    # Determine status
    if target_ms is None:
        status = "INFO"
    elif time_ms <= target_ms:
        status = "PASS"
    elif time_ms <= target_ms * 2:
        status = "WARN"
    else:
        status = "FAIL"
    
    return {
        "name": name,
        "time_ms": time_ms,
        "target_ms": target_ms,
        "status": status,
        "result": result,
    }


def explain_query(conn: sqlite3.Connection, query: str, params: tuple = ()) -> List[str]:
    """
    Get EXPLAIN QUERY PLAN output for a query.
    
    Returns:
        List of query plan lines
    """
    cursor = conn.cursor()
    cursor.execute(f"EXPLAIN QUERY PLAN {query}", params)
    rows = cursor.fetchall()
    
    plan_lines = []
    for row in rows:
        # EXPLAIN QUERY PLAN returns: (id, parent, notused, detail)
        plan_lines.append(f"  {row[3]}")  # detail column
    
    return plan_lines


# ============================================================
# Critical Operations to Profile
# ============================================================

def profile_sku_operations(conn: sqlite3.Connection) -> List[Dict]:
    """Profile SKU repository operations."""
    repo = SKURepository(conn)
    results = []
    
    # Get all SKUs (for counting)
    all_skus = repo.list()
    sku_count = len(all_skus)
    
    # Operation 1: Get single SKU
    if sku_count > 0:
        test_sku = all_skus[0]["sku"]
        results.append(profile_operation(
            f"get_sku (1 SKU)",
            lambda: repo.get(test_sku),
            PERFORMANCE_TARGETS["get_sku"]
        ))
    
    # Operation 2: List all SKUs
    if sku_count <= 200:
        results.append(profile_operation(
            f"list_all_skus ({sku_count} SKUs)",
            lambda: repo.list(),
            PERFORMANCE_TARGETS["list_all_skus"] if sku_count <= 100 else PERFORMANCE_TARGETS["list_all_skus_1000"]
        ))
    
    # Operation 3: List in-assortment SKUs
    results.append(profile_operation(
        f"list_in_assortment ({sku_count} SKUs)",
        lambda: repo.list(filters={'in_assortment': 1}),
        PERFORMANCE_TARGETS["list_all_skus"]
    ))
    
    return results


def profile_ledger_operations(conn: sqlite3.Connection) -> List[Dict]:
    """Profile ledger (transactions) repository operations."""
    repo = LedgerRepository(conn)
    results = []
    
    # Get all SKUs for testing
    sku_repo = SKURepository(conn)
    all_skus = sku_repo.list()
    
    if not all_skus:
        return results
    
    test_sku = all_skus[0]["sku"]
    
    # Operation 1: Get transactions for single SKU
    results.append(profile_operation(
        f"get_transactions_for_sku",
        lambda: repo.list_transactions(sku=test_sku),
        PERFORMANCE_TARGETS["get_transactions_for_sku"]
    ))
    
    # Operation 2: Get all transactions (AsOf date)
    asof_date = date.today()
    results.append(profile_operation(
        f"get_transactions (AsOf {asof_date})",
        lambda: repo.list_transactions(date_to=asof_date.isoformat()),
        PERFORMANCE_TARGETS["get_all_transactions"]
    ))
    
    # Operation 3: Get transactions by event type
    results.append(profile_operation(
        f"get_transactions (event=ORDER)",
        lambda: repo.list_transactions(event="ORDER"),
        PERFORMANCE_TARGETS["get_all_transactions"]
    ))
    
    return results


def profile_orders_operations(conn: sqlite3.Connection) -> List[Dict]:
    """Profile orders repository operations."""
    repo = OrdersRepository(conn)
    results = []
    
    # Get all SKUs for testing
    sku_repo = SKURepository(conn)
    all_skus = sku_repo.list()
    
    if not all_skus:
        return results
    
    test_sku = all_skus[0]["sku"]
    
    # Operation 1: Get orders for SKU
    results.append(profile_operation(
        f"get_orders_for_sku",
        lambda: repo.list(sku=test_sku),
        PERFORMANCE_TARGETS["get_orders_for_sku"]
    ))
    
    # Operation 2: Get pending orders
    results.append(profile_operation(
        f"get_pending_orders",
        lambda: repo.list(status="PENDING"),
        PERFORMANCE_TARGETS["get_orders_for_sku"]
    ))
    
    return results


def profile_lots_operations(conn: sqlite3.Connection) -> List[Dict]:
    """Profile lots repository operations (FEFO-critical)."""
    results = []
    
    # Get all SKUs for testing
    sku_repo = SKURepository(conn)
    all_skus = sku_repo.list()
    
    if not all_skus:
        return results
    
    test_sku = all_skus[0]["sku"]
    cursor = conn.cursor()
    
    # Operation 1: Get lots for SKU (sorted by expiry - FEFO)
    results.append(profile_operation(
        f"get_lots_for_sku (FEFO)",
        lambda: cursor.execute("""
            SELECT * FROM lots 
            WHERE sku = ? AND qty_on_hand > 0 
            ORDER BY expiry_date ASC
        """, (test_sku,)).fetchall(),
        PERFORMANCE_TARGETS["get_lots_for_sku"]
    ))
    
    # Operation 2: Get all lots with qty > 0
    results.append(profile_operation(
        f"get_all_lots (qty > 0)",
        lambda: cursor.execute("""SELECT * FROM lots WHERE qty_on_hand > 0""").fetchall(),
        PERFORMANCE_TARGETS["get_lots_for_sku"]
    ))
    
    return results


def profile_composite_operations(conn: sqlite3.Connection) -> List[Dict]:
    """Profile composite operations (multi-table queries)."""
    results = []
    
    # Composite 1: Stock calculation for single SKU
    # (Simulate what domain/ledger.py does: read SKU + transactions)
    sku_repo = SKURepository(conn)
    ledger_repo = LedgerRepository(conn)
    
    all_skus = sku_repo.list()
    if not all_skus:
        return results
    
    test_sku = all_skus[0]["sku"]
    
    def calculate_stock_single():
        sku_data = sku_repo.get(test_sku)
        txns = ledger_repo.list_transactions(sku=test_sku)
        # Simulate stock calculation (sum events)
        on_hand = 0
        on_order = 0
        for txn in txns:
            event = txn["event"]
            qty = txn["qty"]
            if event == "SNAPSHOT":
                on_hand = qty
            elif event == "ORDER":
                on_order += qty
            elif event == "RECEIPT":
                on_order -= qty
                on_hand += qty
            elif event == "SALE":
                on_hand -= qty
        return sku_data, on_hand, on_order
    
    results.append(profile_operation(
        f"stock_calculation_single_sku",
        calculate_stock_single,
        PERFORMANCE_TARGETS["stock_calculation_single_sku"]
    ))
    
    # Composite 2: Stock calculation for all SKUs (refresh stock scenario)
    def calculate_stock_all():
        stock_by_sku = {}
        for sku_data in all_skus[:100]:  # Limit to 100 SKUs for performance test
            sku_code = sku_data["sku"]
            txns = ledger_repo.list_transactions(sku=sku_code)
            on_hand = 0
            on_order = 0
            for txn in txns:
                event = txn["event"]
                qty = txn["qty"]
                if event == "SNAPSHOT":
                    on_hand = qty
                elif event == "ORDER":
                    on_order += qty
                elif event == "RECEIPT":
                    on_order -= qty
                    on_hand += qty
                elif event == "SALE":
                    on_hand -= qty
            stock_by_sku[sku_code] = (on_hand, on_order)
        return stock_by_sku
    
    sku_limit = min(len(all_skus), 100)
    results.append(profile_operation(
        f"stock_calculation_all_skus ({sku_limit} SKUs)",
        calculate_stock_all,
        PERFORMANCE_TARGETS["stock_calculation_all_skus"]
    ))
    
    return results


# ============================================================
# Query Plan Analysis
# ============================================================

CRITICAL_QUERIES = {
    "get_sku": (
        "SELECT * FROM skus WHERE sku = ?",
        ("SKU001",)
    ),
    "list_all_skus": (
        "SELECT * FROM skus ORDER BY sku LIMIT 100",
        ()
    ),
    "list_in_assortment": (
        "SELECT * FROM skus WHERE in_assortment = 1 ORDER BY sku LIMIT 100",
        ()
    ),
    "get_transactions_for_sku": (
        "SELECT * FROM transactions WHERE sku = ? ORDER BY date ASC, transaction_id ASC",
        ("SKU001",)
    ),
    "get_transactions_asof": (
        "SELECT * FROM transactions WHERE date < ? ORDER BY date ASC",
        ("2026-02-17",)
    ),
    "get_orders_for_sku_pending": (
        "SELECT * FROM order_logs WHERE sku = ? AND status = 'PENDING' ORDER BY date ASC",
        ("SKU001",)
    ),
    "get_lots_fefo": (
        "SELECT * FROM lots WHERE sku = ? AND qty_on_hand > 0 ORDER BY expiry_date ASC",
        ("SKU001",)
    ),
    "get_sales_history": (
        "SELECT * FROM sales WHERE sku = ? AND date >= ? ORDER BY date ASC",
        ("SKU001", "2025-01-01")
    ),
}


def analyze_query_plans(conn: sqlite3.Connection) -> Dict[str, List[str]]:
    """Analyze query plans for critical queries."""
    plans = {}
    
    for query_name, (query, params) in CRITICAL_QUERIES.items():
        plans[query_name] = explain_query(conn, query, params)
    
    return plans


# ============================================================
# Benchmark Data Generation
# ============================================================

def generate_benchmark_data(conn: sqlite3.Connection, 
                           num_skus: int = 100, 
                           num_transactions_per_sku: int = 100) -> None:
    """
    Generate benchmark data for performance testing.
    
    Args:
        num_skus: Number of SKUs to create
        num_transactions_per_sku: Average transactions per SKU
    """
    print(f"Generating benchmark data: {num_skus} SKUs, ~{num_transactions_per_sku} txns/SKU...")
    
    sku_repo = SKURepository(conn)
    ledger_repo = LedgerRepository(conn)
    
    # Generate SKUs
    for i in range(num_skus):
        sku_code = f"BENCH{i:05d}"
        sku_data = {
            "sku": sku_code,
            "description": f"Benchmark Product {i}",
            "moq": 10,
            "pack_size": 1,
            "lead_time_days": 7,
            "safety_stock": 20,
            "in_assortment": 1,
        }
        try:
            sku_repo.upsert(sku_data)
        except Exception:
            pass  # Skip if exists
    
    # Generate transactions
    start_date = date.today() - timedelta(days=365)
    
    for i in range(num_skus):
        sku_code = f"BENCH{i:05d}"
        
        # SNAPSHOT
        ledger_repo.append_transaction(
            date=(start_date - timedelta(days=1)).isoformat(),
            sku=sku_code,
            event="SNAPSHOT",
            qty=100,
            note="Initial stock",
        )
        
        # Random transactions over 1 year
        for j in range(num_transactions_per_sku):
            event_choice = random.choices(
                ["SALE", "ORDER", "RECEIPT"],
                weights=[0.6, 0.2, 0.2],
                k=1
            )[0]
            
            txn_date = start_date + timedelta(days=random.randint(0, 365))
            qty = random.randint(5, 50)
            
            ledger_repo.append_transaction(
                date=txn_date.isoformat(),
                sku=sku_code,
                event=event_choice,
                qty=qty,
                receipt_date=(txn_date + timedelta(days=7)).isoformat() if event_choice == "ORDER" else None,
                note=f"Benchmark {event_choice}",
            )
    
    print(f"✓ Generated {num_skus} SKUs with ~{num_transactions_per_sku * num_skus} transactions")


# ============================================================
# Main Profiling Logic
# ============================================================

def run_profiling(conn: sqlite3.Connection, verbose: bool = False, explain: bool = False) -> Dict[str, Any]:
    """
    Run full profiling suite.
    
    Returns:
        {
            "timestamp": str,
            "database_stats": dict,
            "results": list,
            "query_plans": dict (if explain=True),
            "summary": dict
        }
    """
    print("="*80)
    print("DATABASE PERFORMANCE PROFILING")
    print("="*80)
    print()
    
    # Database stats
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM skus")
    sku_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM transactions")
    txn_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM order_logs")
    order_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM lots")
    lot_count = cursor.fetchone()[0]
    
    print(f"Database: {DB_PATH}")
    print(f"SKUs: {sku_count}")
    print(f"Transactions: {txn_count}")
    print(f"Orders: {order_count}")
    print(f"Lots: {lot_count}")
    print()
    
    # Run profiling
    all_results = []
    
    print("Profiling SKU operations...")
    all_results.extend(profile_sku_operations(conn))
    
    print("Profiling ledger operations...")
    all_results.extend(profile_ledger_operations(conn))
    
    print("Profiling orders operations...")
    all_results.extend(profile_orders_operations(conn))
    
    print("Profiling lots operations...")
    all_results.extend(profile_lots_operations(conn))
    
    print("Profiling composite operations...")
    all_results.extend(profile_composite_operations(conn))
    
    print()
    print("="*80)
    print("RESULTS")
    print("="*80)
    print()
    
    # Print results
    status_symbols = {
        "PASS": "✓",
        "WARN": "⚠",
        "FAIL": "✗",
        "INFO": "ℹ",
    }
    
    pass_count = 0
    warn_count = 0
    fail_count = 0
    
    for result in all_results:
        symbol = status_symbols.get(result["status"], "?")
        name = result["name"]
        time_ms = result["time_ms"]
        target_ms = result["target_ms"]
        status = result["status"]
        
        if target_ms:
            print(f"{symbol} {name:50s} {time_ms:8.2f} ms (target: {target_ms:.0f} ms) [{status}]")
        else:
            print(f"{symbol} {name:50s} {time_ms:8.2f} ms [{status}]")
        
        if verbose and "result" in result:
            print(f"    Result: {result['result']}")
        
        if status == "PASS":
            pass_count += 1
        elif status == "WARN":
            warn_count += 1
        elif status == "FAIL":
            fail_count += 1
    
    print()
    print("="*80)
    print("SUMMARY")
    print("="*80)
    print(f"✓ PASS: {pass_count}")
    print(f"⚠ WARN: {warn_count}")
    print(f"✗ FAIL: {fail_count}")
    print()
    
    # Query plan analysis
    query_plans = None
    if explain:
        print("="*80)
        print("QUERY PLAN ANALYSIS")
        print("="*80)
        print()
        
        query_plans = analyze_query_plans(conn)
        
        for query_name, plan in query_plans.items():
            print(f"{query_name}:")
            for line in plan:
                print(line)
            print()
    
    return {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "database_stats": {
            "skus": sku_count,
            "transactions": txn_count,
            "orders": order_count,
            "lots": lot_count,
        },
        "results": all_results,
        "query_plans": query_plans,
        "summary": {
            "pass": pass_count,
            "warn": warn_count,
            "fail": fail_count,
            "total": len(all_results),
        }
    }


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Profile database performance")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output (show results)")
    parser.add_argument("--explain", "-e", action="store_true", help="Show EXPLAIN QUERY PLAN analysis")
    parser.add_argument("--benchmark", "-b", action="store_true", help="Generate benchmark data first")
    parser.add_argument("--num-skus", type=int, default=100, help="Number of SKUs for benchmark (default: 100)")
    parser.add_argument("--num-txns", type=int, default=100, help="Transactions per SKU for benchmark (default: 100)")
    
    args = parser.parse_args()
    
    # Open connection
    conn = open_connection()
    
    try:
        # Generate benchmark data if requested
        if args.benchmark:
            generate_benchmark_data(conn, args.num_skus, args.num_txns)
            print()
        
        # Run profiling
        results = run_profiling(conn, verbose=args.verbose, explain=args.explain)
        
        # Exit code based on results
        if results["summary"]["fail"] > 0:
            sys.exit(1)
        elif results["summary"]["warn"] > 0:
            sys.exit(2)
        else:
            sys.exit(0)
    
    finally:
        close_connection(conn)


if __name__ == "__main__":
    main()
