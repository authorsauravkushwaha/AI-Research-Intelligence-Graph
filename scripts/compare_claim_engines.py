#!/usr/bin/env python3
"""Compare the Ruby claim resolver against the Python engine on the demo corpus.

    ruby polyglot/ruby/claim_resolver.rb /tmp/claims.json > /tmp/ruby.json
    python scripts/compare_claim_engines.py /tmp/ruby.json

Exits non-zero when the two engines disagree on which claim pairs are candidates or on
their scores, so the "both engines use the same rule set" claim stays verifiable in CI
instead of living in a comment. `polyglot/README.md` documents how the Ruby side was
executed without a system Ruby (CRuby 3.2 on wasm).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.store.memory_store import MemoryGraphStore  # noqa: E402

TOLERANCE = 1e-9


def python_engine() -> dict[tuple[str, str], dict[str, object]]:
    """Run the Python resolver exactly as the store does, over the shipped corpus."""
    store = MemoryGraphStore()
    store.load()
    return {
        (row["claim_a"], row["claim_b"]): {"score": row["score"], "kind": row["kind"]}
        for row in store.conflicts
    }


def ruby_engine(path: Path) -> dict[tuple[str, str], dict[str, object]]:
    payload = json.loads(path.read_text())
    if "conflicts" not in payload:
        raise SystemExit(f"{path} is not a claim-resolver response (no 'conflicts' key)")
    return {
        (row["claim_a"], row["claim_b"]): {"score": row["score"], "kind": row["kind"]}
        for row in payload["conflicts"]
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ruby_json", type=Path, help="the Ruby resolver's JSON response")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    python_rows = python_engine()
    ruby_rows = ruby_engine(args.ruby_json)

    problems: list[str] = []
    for pair in sorted(set(python_rows) - set(ruby_rows)):
        problems.append(f"python only: {pair[0]} x {pair[1]} ({python_rows[pair]['score']})")
    for pair in sorted(set(ruby_rows) - set(python_rows)):
        problems.append(f"ruby only:   {pair[0]} x {pair[1]} ({ruby_rows[pair]['score']})")
    for pair in sorted(set(python_rows) & set(ruby_rows)):
        py, rb = python_rows[pair], ruby_rows[pair]
        if abs(float(py["score"]) - float(rb["score"])) > TOLERANCE:
            problems.append(f"score differs for {pair[0]} x {pair[1]}: python {py['score']} vs ruby {rb['score']}")
        if py["kind"] != rb["kind"]:
            problems.append(f"kind differs for {pair[0]} x {pair[1]}: python {py['kind']} vs ruby {rb['kind']}")

    if not args.quiet:
        print("  · engines: python resolver (backend/ingestion/claims.py) + ruby resolver (polyglot/ruby)")
        print(f"  · conflicts: python {len(python_rows)} · ruby {len(ruby_rows)}")
        if problems:
            for problem in problems[:20]:
                print(f"  ! {problem}")

    if problems:
        print(f"claim engines DIVERGE ({len(problems)} difference(s)) — fix both implementations", file=sys.stderr)
        return 1
    print(f"claim engines agree exactly: {len(python_rows)} conflicts, identical scores and kinds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
