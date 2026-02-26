#!/usr/bin/env python3
"""
tools/export_openapi.py — Generate / verify docs/openapi.json from the live FastAPI app.

Usage
-----
Generate (or refresh) the committed schema::

    python tools/export_openapi.py

Verify that the committed schema matches what the app currently generates
(used by CI — exits 1 if there is a diff)::

    python tools/export_openapi.py --check

Custom output path::

    python tools/export_openapi.py --output path/to/schema.json

Why normalise?
--------------
FastAPI's ``app.openapi()`` returns a plain dict.  ``json.dumps`` with
``sort_keys=True`` and ``indent=2`` produces a stable, line-oriented
representation so that ``git diff`` and the CI check both show meaningful
changes rather than spurious ordering noise.

The trailing newline ensures the file satisfies POSIX text-file conventions
and avoids the "no newline at end of file" marker in diffs.
"""
from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_OUTPUT = _REPO_ROOT / "docs" / "openapi.json"

# ---------------------------------------------------------------------------
# Schema generation
# ---------------------------------------------------------------------------


def _build_schema() -> str:
    """
    Import the FastAPI app, call ``.openapi()``, and return a normalised JSON
    string (sorted keys, 2-space indent, trailing newline).

    This function adds the ``backend/`` directory to ``sys.path`` so it
    works whether the package is installed editable or not.
    """
    backend_dir = str(_REPO_ROOT / "backend")
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    # Import inside the function so sys.path manipulation takes effect first.
    # Import create_app from the sub-module directly (not via the package
    # __init__) to get a clean, isolated app instance.  dos_backend/api/__init__.py
    # uses a lazy __getattr__ for ``app`` precisely to prevent the server singleton
    # from being created as a side-effect of this import.
    from dos_backend.api.app import create_app  # noqa: PLC0415

    app = create_app()
    schema = app.openapi()
    return json.dumps(schema, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export / verify docs/openapi.json from the FastAPI app.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Compare generated schema against the committed file. "
            "Exit 1 if they differ (for use in CI)."
        ),
    )
    parser.add_argument(
        "--output",
        metavar="PATH",
        default=str(_DEFAULT_OUTPUT),
        help=f"Destination file (default: {_DEFAULT_OUTPUT.relative_to(_REPO_ROOT)})",
    )
    return parser.parse_args()


def _check(generated: str, committed_path: Path) -> None:
    """Diff *generated* against *committed_path* and exit 1 on any difference."""
    if not committed_path.exists():
        print(
            f"[openapi-check] ERROR: {committed_path.relative_to(_REPO_ROOT)} "
            "does not exist.\n"
            "Run `python tools/export_openapi.py` to generate it, then commit.",
            file=sys.stderr,
        )
        sys.exit(1)

    committed = committed_path.read_text(encoding="utf-8")
    if generated == committed:
        print(
            f"[openapi-check] OK — {committed_path.relative_to(_REPO_ROOT)} "
            "is up to date."
        )
        return

    # Show a trimmed unified diff so the CI log is actionable.
    diff_lines = list(
        difflib.unified_diff(
            committed.splitlines(keepends=True),
            generated.splitlines(keepends=True),
            fromfile=f"committed: {committed_path.relative_to(_REPO_ROOT)}",
            tofile="generated (current app)",
        )
    )
    print(
        "[openapi-check] FAIL — docs/openapi.json is out of date.\n"
        "Run `python tools/export_openapi.py` locally, review the diff, "
        "and commit the updated file.\n",
        file=sys.stderr,
    )
    # Print at most 200 diff lines to keep CI logs readable.
    for line in diff_lines[:200]:
        sys.stderr.write(line)
    if len(diff_lines) > 200:
        sys.stderr.write(
            f"\n… {len(diff_lines) - 200} more lines omitted.\n"
        )
    sys.exit(1)


def _write(generated: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(generated, encoding="utf-8")
    rel = output_path.relative_to(_REPO_ROOT)
    print(f"[export_openapi] Written {len(generated.splitlines())} lines → {rel}")


def main() -> None:
    args = _parse_args()
    output_path = Path(args.output).resolve()

    print("[export_openapi] Generating schema from dos_backend.api.app …")
    generated = _build_schema()

    if args.check:
        _check(generated, output_path)
    else:
        _write(generated, output_path)


if __name__ == "__main__":
    main()
