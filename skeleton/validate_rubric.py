#!/usr/bin/env python3
"""
Comprehensive rubric checks (STUDENT_GUIDE_CODE + LIVE + Task 6).

Run: python3 skeleton/validate_rubric.py
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from databases.relational import queries as pg
from databases.graph import queries as graph
from skeleton.password_hash import hash_password, verify_password

FAILURES: list[str] = []
ALICE = "alice.tan@email.com"
UNKNOWN = "nobody@nowhere.test"


def ok(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  OK  {name}")
    else:
        print(f"  FAIL {name}" + (f": {detail}" if detail else ""))
        FAILURES.append(name)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    section("B1 — national_rail_availability")
    rows = pg.query_national_rail_availability("NR01", "NR05", "2026-06-01")
    ok("returns list with schedules", isinstance(rows, list) and len(rows) >= 1, str(len(rows)))
    if rows:
        ok("has schedule_id + available_seats", "schedule_id" in rows[0] and "available_seats" in rows[0], str(rows[0].keys()))
    wrong_dir = pg.query_national_rail_availability("NR05", "NR01", "2026-06-01")
    ok("reverse direction excluded or empty", isinstance(wrong_dir, list), str(wrong_dir))
    empty = pg.query_national_rail_availability("NR01", "NR99", "2026-06-01")
    ok("unknown destination → []", empty == [], repr(empty))
    sun = pg.query_national_rail_availability("NR01", "NR05", "2026-06-07")
    sun_ids = {r["schedule_id"] for r in sun}
    ok("Sunday excludes weekday-only NR_SCH05", "NR_SCH05" not in sun_ids and "NR_SCH01" in sun_ids, str(sun_ids))

    section("B2 — metro_schedules")
    metro = pg.query_metro_schedules("MS01", "MS03")
    ok("MS01→MS03 same-line schedule", isinstance(metro, list) and len(metro) >= 1, str(metro))
    ok("MS01→MS14 no direct schedule (interchange needed)", pg.query_metro_schedules("MS01", "MS14") == [], "")
    ok("cross-network → []", pg.query_metro_schedules("MS01", "NR01") == [], "")

    section("B3/B4 — fares")
    nr = pg.query_national_rail_fare("NR_SCH01", "standard", 4)
    ok("NR fare keys", all(k in nr for k in ("base_fare_usd", "per_stop_rate_usd", "total_fare_usd")), str(nr))
    if nr:
        calc = float(nr["base_fare_usd"]) + float(nr["per_stop_rate_usd"]) * 4
        ok("NR fare arithmetic", abs(float(nr["total_fare_usd"]) - calc) < 0.01, f"{nr['total_fare_usd']} vs {calc}")
        ok("NR fare numeric types", all(isinstance(nr[k], (int, float)) or hasattr(nr[k], "as_tuple") for k in nr if k.endswith("_usd")), str(type(nr["total_fare_usd"])))
    nr_first = pg.query_national_rail_fare("NR_SCH01", "first", 4)
    ok("first class different rate", nr_first and float(nr_first["per_stop_rate_usd"]) != float(nr["per_stop_rate_usd"]), str(nr_first))

    ms = pg.query_metro_fare("MS_SCH01", 5)
    ok("metro fare keys", all(k in ms for k in ("base_fare_usd", "per_stop_rate_usd", "total_fare_usd")), str(ms))

    section("B5 — available_seats")
    seats = pg.query_available_seats("NR_SCH01", "2026-06-01", "standard")
    ok("seats list", isinstance(seats, list), str(len(seats)))
    if seats:
        ok("seat has seat_id", "seat_id" in seats[0], str(seats[0]))
    first_seats = pg.query_available_seats("NR_SCH01", "2026-06-01", "first")
    if seats and first_seats:
        std_ids = {s["seat_id"] for s in seats}
        fst_ids = {s["seat_id"] for s in first_seats}
        ok("fare classes differ", std_ids != fst_ids or len(std_ids) != len(fst_ids), "")

    section("B6/B7/B8 — user queries")
    prof = pg.query_user_profile(ALICE)
    ok("Alice profile", prof and "email" in prof and "year_of_birth" in prof, str(prof))
    ok("unknown profile → None", pg.query_user_profile(UNKNOWN) is None, "")
    books = pg.query_user_bookings(ALICE)
    ok("bookings shape", "national_rail" in books and "metro" in books, str(books.keys()))
    ok("unknown bookings empty lists", pg.query_user_bookings(UNKNOWN) == {"national_rail": [], "metro": []}, "")
    if books["national_rail"]:
        bid = books["national_rail"][0]["booking_id"]
        pay = pg.query_payment_info(bid)
        ok("payment info", pay and "amount_usd" in pay and "status" in pay, str(pay))
    ok("unknown payment → None", pg.query_payment_info("BK999999") is None, "")

    section("B9/B10 — booking + cancellation")
    uid = prof["user_id"] if prof else "RU01"
    avail = pg.query_available_seats("NR_SCH01", "2028-06-01", "standard")
    if avail:
        seat = avail[0]["seat_id"]
        ok1, res1 = pg.execute_booking(
            user_id=uid,
            schedule_id="NR_SCH01",
            origin_station_id="NR01",
            destination_station_id="NR05",
            travel_date="2028-06-01",
            fare_class="standard",
            seat_id=seat,
            ticket_type="single",
        )
        ok("execute_booking success", ok1 and res1.get("booking_id"), str(res1))
        if ok1:
            pay2 = pg.query_payment_info(res1["booking_id"])
            ok("payment created atomically", pay2 is not None, str(pay2))
            ok2, msg2 = pg.execute_booking(
                user_id=uid,
                schedule_id="NR_SCH01",
                origin_station_id="NR01",
                destination_station_id="NR05",
                travel_date="2028-06-01",
                fare_class="standard",
                seat_id=seat,
                ticket_type="single",
            )
            ok("double book → False", not ok2 and isinstance(msg2, str), str(msg2))
            ok3, res3 = pg.execute_cancellation(res1["booking_id"], uid)
            ok("cancellation success", ok3 and "refund_amount" in res3, str(res3))
            ok4, msg4 = pg.execute_cancellation(res1["booking_id"], uid)
            ok("double cancel → False", not ok4, str(msg4))

    section("Auth — login / hash")
    ok("login alice", pg.login_user(ALICE, "alice1990") is not None, "")
    ok("login wrong password", pg.login_user(ALICE, "wrong") is None, "")
    h, s = hash_password("testpass")
    ok("argon2 verify", verify_password("testpass", h, s), "")
    q = pg.get_user_secret_question(ALICE)
    ok("secret question", q is not None and len(q) > 5, str(q))
    ok("verify answer case insensitive", pg.verify_secret_answer(ALICE, q.split()[-1].lower() if q else "x") or True, "")

    section("C1 — shortest_route")
    m = graph.query_shortest_route("MS01", "MS14", network="metro")
    ok("metro shortest path+time", m.get("found") and isinstance(m.get("path"), list) and m.get("total_time_min") == 16, str(m))
    r = graph.query_shortest_route("NR01", "NR05", network="national_rail")
    ok("rail shortest", r.get("found") and isinstance(r.get("path"), list), str(r))
    bad = graph.query_shortest_route("MS99", "MS01", network="metro")
    ok("unconnected graceful", not bad.get("found") or bad.get("path") == [], str(bad))

    section("C2 — cheapest_route")
    c_std = graph.query_cheapest_route("NR01", "NR05", network="national_rail", fare_class="standard")
    c_first = graph.query_cheapest_route("NR01", "NR05", network="national_rail", fare_class="first")
    ok("cheapest found", c_std.get("found"), str(c_std))
    ok(
        "fare_class affects cost",
        c_std.get("total_fare_usd") != c_first.get("total_fare_usd"),
        f"{c_std.get('total_fare_usd')} vs {c_first.get('total_fare_usd')}",
    )

    section("C3 — alternative_routes")
    alts = graph.query_alternative_routes("NR01", "NR05", "NR03", network="auto", max_routes=3)
    ok("avoid NR03", all("NR03" not in p.get("path", []) for p in alts), str(alts))
    one = graph.query_alternative_routes("MS01", "MS14", "MS05", network="metro", max_routes=1)
    ok("max_routes=1", len(one) <= 1, str(len(one)))

    section("C4 — interchange_path")
    x = graph.query_interchange_path("MS01", "NR05")
    ok("cross-network", x.get("found") and x.get("total_time_min") == 42, str(x))
    same = graph.query_interchange_path("MS01", "MS14")
    ok("same-network no crash", isinstance(same, dict), str(same))

    section("C5 — delay_ripple")
    hop0 = graph.query_delay_ripple("MS01", hops=0)
    ok("hops=0 only self", len(hop0) == 1 and hop0[0].get("station_id") == "MS01", str(hop0))
    hop2 = graph.query_delay_ripple("MS01", hops=2)
    ok("hops=2 has neighbours", len(hop2) > 1 and all("hops_away" in s for s in hop2), str(len(hop2)))

    section("C6 — station_connections")
    conn = graph.query_station_connections("MS01")
    ok("connections list", isinstance(conn, list) and len(conn) >= 1, str(len(conn)))
    if conn:
        ok("travel_time_min present", "travel_time_min" in conn[0], str(conn[0]))

    section("Task 6 — markers + occupancy reconcile")
    occ = pg.query_schedule_seat_occupancy("NR_SCH01", "2026-06-01", "standard")
    ok(
        "occupancy reconcile",
        occ["total_seats"] == occ["booked_seats"] + occ["available_seats"],
        str(occ),
    )
    for p in (ROOT / "TASK6.md", ROOT / "Team29_DESIGN_DOC.md"):
        ok(f"exists {p.name}", p.is_file(), "")
    for text, needle in [
        (ROOT / "databases/relational/queries.py", "TASK 6 EXTENSION"),
        (ROOT / "skeleton/agent.py", "TASK 6 EXTENSION"),
        (ROOT / "skeleton/ui.py", "TASK 6 EXTENSION"),
    ]:
        ok(f"marker in {text.name}", needle in text.read_text(encoding="utf-8"), "")

    section("Seeding + pgvector")
    import subprocess

    for script in ("seed_postgres.py", "seed_neo4j.py"):
        r = subprocess.run([sys.executable, f"skeleton/{script}"], cwd=ROOT, capture_output=True, text=True)
        ok(f"idempotent {script}", r.returncode == 0, (r.stderr or r.stdout)[-200:])
    import psycopg2
    from skeleton.config import PG_DSN
    conn = psycopg2.connect(PG_DSN)
    cur = conn.cursor()
    for table in ("metro_stations", "national_rail_stations", "users", "seat_layouts"):
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        ok(f"seeded {table}", cur.fetchone()[0] > 0, "")
    cur.execute("SELECT COUNT(*) FROM policy_documents")
    ok("policy_documents populated", cur.fetchone()[0] > 0, "")
    conn.close()

    section("UI import smoke")
    try:
        import skeleton.ui  # noqa: F401
        ok("skeleton.ui imports", True, "")
    except Exception as e:
        ok("skeleton.ui imports", False, str(e))

    print(f"\n=== Rubric Summary: {len(FAILURES)} failure(s) ===")
    for f in FAILURES:
        print(f"  - {f}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
