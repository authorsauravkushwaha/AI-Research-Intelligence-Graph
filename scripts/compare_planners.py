#!/usr/bin/env python3
"""Compare the Kotlin planner against the Python port on the shared fixture.

The Kotlin planner (`polyglot/kotlin`) is the reference implementation of the NEXUS
query DSL; `backend/services/planner.py` is the port the API falls back to. This
script feeds `tests/data/planner_parity.json` to both and fails when they disagree,
so "both planners emit the same Cypher and the same bind parameters" stays a test
result instead of a comment.

    python scripts/compare_planners.py --jar /tmp/planner.jar      # compile first
    python scripts/compare_planners.py --url http://127.0.0.1:8092 # running sidecar
    python scripts/compare_planners.py --jar /tmp/planner.jar 'limit 5 rel in (CITES)'

Exit code 0 only when every field of every plan is identical.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.services.planner import python_plan  # noqa: E402

FIXTURE = ROOT / "tests" / "data" / "planner_parity.json"
FIELDS = ("ok", "error", "query", "cypher", "params", "explanation", "cost")


def jar_planner(path: Path, java: str = "java") -> Callable[[str], dict[str, Any]]:
    def run(dsl: str) -> dict[str, Any]:
        proc = subprocess.run(
            [java, "-jar", str(path), "--plan", dsl],
            capture_output=True, text=True, timeout=120,
        )
        out = proc.stdout.strip().splitlines()
        if not out:
            raise SystemExit(f"the kotlin planner produced no output (stderr: {proc.stderr.strip()[:300]})")
        return json.loads(out[-1])

    return run


def url_planner(url: str) -> Callable[[str], dict[str, Any]]:
    import httpx

    def run(dsl: str) -> dict[str, Any]:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(url.rstrip("/") + "/api/plan", params={"q": dsl})
        if resp.status_code not in (200, 400):
            raise SystemExit(f"{url} answered HTTP {resp.status_code}")
        return resp.json()

    return run


def compare(kotlin: Callable[[str], dict[str, Any]], dsls: list[str], quiet: bool = False) -> int:
    problems = 0
    for dsl in dsls:
        theirs, ours = kotlin(dsl), python_plan(dsl)
        diffs = [f for f in FIELDS if json.dumps(theirs.get(f), sort_keys=True) != json.dumps(ours.get(f), sort_keys=True)]
        label = dsl if len(dsl) <= 58 else dsl[:55] + "…"
        if not diffs:
            if not quiet:
                print(f"  ok   {label}")
            continue
        problems += 1
        print(f"  DIFF {label} -> {', '.join(diffs)}")
        for field in diffs:
            print(f"       kotlin: {json.dumps(theirs.get(field), sort_keys=True)[:220]}")
            print(f"       python: {json.dumps(ours.get(field), sort_keys=True)[:220]}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--jar", type=Path, help="path to planner.jar (built by kotlinc)")
    source.add_argument("--url", help="base URL of a running planner sidecar")
    parser.add_argument("--java", default="java", help="java binary to use (default: java)")
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--write", action="store_true", help="regenerate the fixture from the reference planner")
    parser.add_argument("extra", nargs="*", help="additional DSL queries to compare")
    args = parser.parse_args()

    if not args.jar and not args.url:
        parser.error("give --jar PATH or --url URL — the reference planner must be reachable")

    fixture = json.loads(args.fixture.read_text())
    kotlin = jar_planner(args.jar, args.java) if args.jar else url_planner(args.url)
    recorded = lambda dsl: {k: v for k, v in kotlin(dsl).items() if k != "engine"}  # noqa: E731

    if args.write:
        fixture["queries"] = [{"dsl": row["dsl"], "plan": recorded(row["dsl"])} for row in fixture["queries"]]
        args.fixture.write_text(json.dumps(fixture, indent=2) + "\n")
        print(f"  · fixture rewritten from the reference planner: {args.fixture.relative_to(ROOT)}")
        return 0

    dsls = [row["dsl"] for row in fixture["queries"]] + list(args.extra)
    print(f"  · reference: {'jar ' + str(args.jar) if args.jar else args.url}")
    print(f"  · fixture:   {args.fixture.relative_to(ROOT)} ({len(fixture['queries'])} queries) + {len(args.extra)} extra")
    problems = compare(kotlin, dsls, quiet=args.quiet)

    # The recorded fixture must still be what the reference planner produces.
    stale = [row["dsl"] for row in fixture["queries"]
             if json.dumps(recorded(row["dsl"]), sort_keys=True) != json.dumps(row["plan"], sort_keys=True)]
    if stale:
        print(f"  ! fixture is stale for {len(stale)} queries — regenerate with --write")
        problems += len(stale)

    if problems:
        print(f"planners DIVERGE ({problems} problem(s)) — fix both implementations", file=sys.stderr)
        return 1
    print(f"kotlin and python planners agree on all {len(dsls)} queries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
