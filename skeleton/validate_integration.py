#!/usr/bin/env python3
"""
Full integration checks: README sample queries vs train-mock-data JSON + live DBs.

Run: python skeleton/validate_integration.py
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skeleton.config import DATA_DIR
from skeleton import agent
from databases.relational import queries as pg
from databases.graph import queries as graph

FAILURES: list[str] = []
ALICE_EMAIL = "alice.tan@email.com"


def ok(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  OK  {name}")
    else:
        msg = f"  FAIL {name}" + (f": {detail}" if detail else "")
        print(msg)
        FAILURES.append(name + (f" ({detail})" if detail else ""))


def expected_nr_schedules(origin: str, dest: str) -> set[str]:
    """Rideable schedules from national_rail_schedules.json (origin before destination)."""
    data = json.loads((DATA_DIR / "national_rail_schedules.json").read_text())
    out: set[str] = set()
    for sch in data:
        stops = sch["stops_in_order"]
        if origin not in stops or dest not in stops:
            continue
        if stops.index(origin) < stops.index(dest):
            out.add(sch["schedule_id"])
    return out


def expected_nr_fare(schedule_id: str, origin: str, dest: str, fare_class: str = "standard") -> float:
    """Fare from JSON: base + per_stop * stops_travelled."""
    data = json.loads((DATA_DIR / "national_rail_schedules.json").read_text())
    sch = next(s for s in data if s["schedule_id"] == schedule_id)
    stops = sch["stops_in_order"]
    passed = set(sch.get("passed_through_stations") or [])
    stopping = [s for s in stops if s not in passed]
    oi, di = stopping.index(origin), stopping.index(dest)
    stops_travelled = di - oi
    fc = sch["fare_classes"][fare_class]
    return round(fc["base_fare_usd"] + fc["per_stop_rate_usd"] * stops_travelled, 2)


def agent_ok(msg: str, check, email: str | None = None) -> None:
    reply, _ = agent.run_agent(msg, [], current_user_email=email)
    ok(msg[:55] + "…", check(reply), reply[:220].replace("\n", " "))


def pick_open_travel_date(email: str, origin: str, dest: str) -> str:
    """Return a date with no confirmed booking for the same route (idempotent tests)."""
    existing = pg.query_user_bookings(email)

    def _iso(d) -> str:
        return d.isoformat() if hasattr(d, "isoformat") else str(d)

    taken = {
        _iso(b["travel_date"])
        for b in existing["national_rail"]
        if b.get("origin_station_id") == origin
        and b.get("destination_station_id") == dest
        and b.get("status") == "confirmed"
    }
    d = date(2027, 1, 1)
    for _ in range(400):
        ds = d.isoformat()
        if ds not in taken:
            return ds
        d += timedelta(days=1)
    return "2028-01-01"


def main() -> int:
    print("=== JSON ground truth ===")
    exp_sched = expected_nr_schedules("NR01", "NR05")
    ok("NR01→NR05 rideable schedules", exp_sched == {"NR_SCH01", "NR_SCH05"}, str(exp_sched))

    exp_fare = expected_nr_fare("NR_SCH01", "NR01", "NR05")
    ok("NR_SCH01 standard fare (JSON)", exp_fare == 8.50, str(exp_fare))

    rf = json.loads((DATA_DIR / "refund_policy.json").read_text())
    r5 = next(p for p in rf if p["policy_id"] == "RF005")
    r1 = next(r for r in r5["compensation_rules"] if r["rule_id"] == "RF005_R1")
    ok("RF005 45min → 50%", "50%" in r1["compensation"], r1["compensation"])

    tp = json.loads((DATA_DIR / "travel_policies.json").read_text())
    bikes = tp["national_rail"]["bicycles"]["foldable_bicycles"]
    ok("JSON national rail foldable bikes permitted", bikes["permitted"] is True, "")

    print("\n=== PostgreSQL ===")
    try:
        rows = pg.query_national_rail_availability("NR01", "NR05")
        got = {r["schedule_id"] for r in rows}
        ok("PG NR01→NR05 schedules", got == exp_sched, f"got {got}")

        fare_row = pg.query_national_rail_fare("NR_SCH01", "standard", 4)
        pg_fare = float(fare_row["total_fare_usd"]) if fare_row else -1
        ok("PG NR_SCH01 fare matches JSON", pg_fare == exp_fare, f"pg={pg_fare} json={exp_fare}")

        profile = pg.query_user_profile(ALICE_EMAIL)
        ok("Alice profile exists", profile is not None, "")
        if profile:
            books = pg.query_user_bookings(ALICE_EMAIL)
            ok(
                "Alice has booking history",
                len(books["national_rail"]) + len(books["metro"]) > 0,
                f"nr={len(books['national_rail'])} metro={len(books['metro'])}",
            )
    except Exception as e:
        ok("PostgreSQL", False, str(e))

    print("\n=== Neo4j ===")
    try:
        r = graph.query_shortest_route("MS01", "MS14")
        ok("MS01→MS14 found", r.get("found"), str(r))
        ok("MS01→MS14 time 16min", r.get("total_time_min") == 16, str(r.get("total_time_min")))

        x = graph.query_interchange_path("MS01", "NR05")
        ok("MS01→NR05 found", x.get("found"), str(x))
        ok("MS01→NR05 time 42min", x.get("total_time_min") == 42, str(x.get("total_time_min")))

        alts = graph.query_alternative_routes("NR01", "NR05", "NR03", network="auto")
        ok("NR01→NR05 avoid NR03: no graph path", len(alts) == 0, f"count={len(alts)}")
    except Exception as e:
        ok("Neo4j", False, str(e))

    print("\n=== Agent — README sample queries ===")
    agent_ok(
        "What national rail trains run from Central (NR01) to Stonehaven (NR05)?",
        lambda t: "NR_SCH01" in t and "NR_SCH05" in t and "NR_SCH02" not in t and "NR_SCH06" not in t,
    )
    agent_ok(
        "What is the fastest metro route from MS01 to MS14?",
        lambda t: "16" in t,
    )
    agent_ok(
        "How do I get from Central Square (MS01) to Stonehaven (NR05)?",
        lambda t: "42" in t,
    )
    agent_ok(
        "If Old Town station (NR03) is closed, what alternative routes exist from NR01 to NR05?",
        lambda t: "No alternative" in t and "NR03" in t,
    )
    agent_ok(
        "My train was delayed 45 minutes — what compensation am I entitled to?",
        lambda t: "50%" in t and "RF005_R1" in t,
    )
    agent_ok(
        "What is the company policy on travelling with a bicycle on national rail?",
        lambda t: "foldable" in t.lower() and ("permitted" in t.lower() or "yes" in t.lower()),
    )
    agent_ok(
        "Show my bookings",
        lambda t: "booking" in t.lower() or "BK" in t,
        email=ALICE_EMAIL,
    )

    travel_date = pick_open_travel_date(ALICE_EMAIL, "NR01", "NR05")
    book_msg = (
        f"Book me a standard ticket from Central Station (NR01) to Stonehaven (NR05) "
        f"on {travel_date}"
    )
    reply, _ = agent.run_agent(book_msg, [], current_user_email=ALICE_EMAIL)
    ok(
        f"Book NR01→NR05 on {travel_date}",
        "Booking confirmed" in reply or "booking_id" in reply.lower(),
        reply[:220].replace("\n", " "),
    )

    print("\n=== Task 6 — seat occupancy ===")
    try:
        occ = pg.query_schedule_seat_occupancy("NR_SCH01", "2026-06-01", "standard")
        ok(
            "query_schedule_seat_occupancy",
            occ.get("total_seats", 0) > 0 and occ.get("available_seats", -1) >= 0,
            str(occ),
        )
    except Exception as e:
        ok("query_schedule_seat_occupancy", False, str(e))

    agent_ok(
        "How many seats are available on NR_SCH01 on 2026-06-15?",
        lambda t: "available" in t.lower() and ("seat" in t.lower() or "occupancy" in t.lower()),
    )

    print("\n=== Teammate policy_chunks sync ===")
    import subprocess

    for br in ("113403501", "113403504"):
        h = subprocess.run(
            ["git", "rev-parse", f"origin/{br}:train-mock-data/policy_chunks.json"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        if br == "113403501":
            hash_501 = h.stdout.strip()
        else:
            hash_504 = h.stdout.strip()
    ok("policy_chunks.json matches 113403501", hash_501 == hash_504, f"{hash_501[:8]} vs {hash_504[:8]}")

    print("\n=== Policy JSON (offline) ===")
    from skeleton.policy_lookup import search_policy_json

    ok(
        "RF010 metro online (JSON)",
        search_policy_json("metro online book app") is not None,
        "",
    )
    ok(
        "RF005 60min (JSON)",
        search_policy_json("delay 60 minutes compensation") is not None
        and "100%" in search_policy_json("delay 60 minutes compensation"),
        "",
    )

    print(f"\n=== Summary: {len(FAILURES)} failure(s) ===")
    for f in FAILURES:
        print(f"  - {f}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
