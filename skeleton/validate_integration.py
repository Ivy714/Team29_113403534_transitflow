#!/usr/bin/env python3
"""
Integration checks against train-mock-data JSON and live databases.
Run: python skeleton/validate_integration.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skeleton.config import DATA_DIR
from skeleton import agent
from databases.relational import queries as pg
from databases.graph import queries as graph

FAILURES: list[str] = []


def ok(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  OK  {name}")
    else:
        msg = f"  FAIL {name}" + (f": {detail}" if detail else "")
        print(msg)
        FAILURES.append(name + (f" ({detail})" if detail else ""))


def expected_nr_schedules(origin: str, dest: str) -> set[str]:
    """Ground truth from national_rail_schedules.json (rideable, correct direction)."""
    data = json.loads((DATA_DIR / "national_rail_schedules.json").read_text())
    out: set[str] = set()
    for sch in data:
        stops = sch["stops_in_order"]
        if origin not in stops or dest not in stops:
            continue
        oi, di = stops.index(origin), stops.index(dest)
        if oi < di:
            out.add(sch["schedule_id"])
    return out


def main() -> int:
    print("=== JSON ground truth ===")
    exp = expected_nr_schedules("NR01", "NR05")
    ok("JSON NR01→NR05 schedules", exp == {"NR_SCH01", "NR_SCH05"}, str(exp))

    rf = json.loads((DATA_DIR / "refund_policy.json").read_text())
    r5 = next(p for p in rf if p["policy_id"] == "RF005")
    r1 = next(r for r in r5["compensation_rules"] if r["rule_id"] == "RF005_R1")
    ok("RF005 45min tier", "50%" in r1["compensation"], r1["compensation"])

    print("\n=== PostgreSQL ===")
    try:
        rows = pg.query_national_rail_availability("NR01", "NR05")
        got = {r["schedule_id"] for r in rows}
        ok("PG NR01→NR05", got == exp, f"got {got}")
        for sid in got:
            r = next(x for x in rows if x["schedule_id"] == sid)
            ok(
                f"  {sid} stop_order",
                r["origin_stop_order"] < r["destination_stop_order"],
                f"{r['origin_stop_order']} vs {r['destination_stop_order']}",
            )
    except Exception as e:
        ok("PG connection", False, str(e))

    print("\n=== Neo4j ===")
    try:
        r = graph.query_shortest_route("MS01", "MS14")
        ok("MS01→MS14 found", r.get("found"), str(r))
        ok("MS01→MS14 time 16min", r.get("total_time_min") == 16, str(r.get("total_time_min")))

        x = graph.query_interchange_path("MS01", "NR05")
        ok("MS01→NR05 found", x.get("found"), str(x))
        ok("MS01→NR05 time 42min", x.get("total_time_min") == 42, str(x.get("total_time_min")))

        alts_rail = graph.query_alternative_routes("NR01", "NR05", "NR03", network="rail")
        alts_auto = graph.query_alternative_routes("NR01", "NR05", "NR03", network="auto")
        # Linear NR1 corridor: no path exists without NR03 — "no route" is correct per JSON topology.
        ok(
            "NR01→NR05 avoid NR03 (graph)",
            len(alts_rail) == 0 and len(alts_auto) == 0,
            f"rail={len(alts_rail)} auto={len(alts_auto)}",
        )
    except Exception as e:
        ok("Neo4j", False, str(e))

    print("\n=== Agent (rule router) ===")
    cases = [
        (
            "What national rail trains run from Central (NR01) to Stonehaven (NR05)?",
            None,
            lambda t: "NR_SCH01" in t and "NR_SCH05" in t and "NR_SCH02" not in t and "NR_SCH06" not in t,
        ),
        (
            "What is the fastest metro route from MS01 to MS14?",
            None,
            lambda t: "16" in t and "found" not in t.lower() or "16" in t,
        ),
        (
            "How do I get from Central Square (MS01) to Stonehaven (NR05)?",
            None,
            lambda t: "42" in t,
        ),
        (
            "My train was delayed 45 minutes — what compensation am I entitled to?",
            None,
            lambda t: "50%" in t and "RF005_R1" in t,
        ),
    ]

    for msg, email, check in cases:
        reply, _ = agent.run_agent(msg, [], current_user_email=email)
        ok(msg[:50] + "…", check(reply), reply[:200].replace("\n", " "))

    # Closed station alternative
    msg_alt = "If Old Town station (NR03) is closed, what alternative routes exist from NR01 to NR05?"
    reply_alt, _ = agent.run_agent(msg_alt, [])
    ok(
        "NR03 closed alternatives (agent)",
        "No alternative" in reply_alt and "NR03" in reply_alt,
        reply_alt[:180],
    )

    print(f"\n=== Summary: {len(FAILURES)} failure(s) ===")
    for f in FAILURES:
        print(f"  - {f}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
