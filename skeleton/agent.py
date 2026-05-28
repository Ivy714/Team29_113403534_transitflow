"""
TransitFlow Agent — Full Production Version
=============================================
Integrates:
  - Neo4j graph DB  (routing, interchange, alternative routes)
  - PostgreSQL       (national rail availability, seats, bookings, user profile)
  - pgvector RAG     (policy document search via embeddings)
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from typing import Any, Optional

from databases.graph import queries as graph
from databases.relational.queries import (
    execute_booking,
    execute_cancellation,
    query_available_seats,
    query_metro_schedules,
    query_national_rail_availability,
    query_national_rail_fare,
    query_policy_vector_search,
    query_user_bookings,
    query_user_profile,
    auto_select_adjacent_seats,
)
from skeleton.config import DATA_DIR
from skeleton.llm_provider import llm

# ── Station index (built from JSON for name→ID mapping) ──────────────────────

_MOCK: dict[str, Any] = {}
_STATION_INDEX: dict[str, str] = {}


def _load_mock() -> None:
    global _STATION_INDEX
    if _MOCK:
        return
    files = {
        "metro_stations": "metro_stations.json",
        "nr_stations": "national_rail_stations.json",
        "refund_policy": "refund_policy.json",
        "travel_policies": "travel_policies.json",
        "booking_rules": "booking_rules.json",
        "ticket_types": "ticket_types.json",
    }
    for key, fname in files.items():
        path = DATA_DIR / fname
        if path.exists():
            _MOCK[key] = json.loads(path.read_text(encoding="utf-8"))

    for s in _MOCK.get("metro_stations", []) + _MOCK.get("nr_stations", []):
        _STATION_INDEX[s["name"].strip().lower()] = s["station_id"]
        _STATION_INDEX[s["station_id"].lower()] = s["station_id"]


def _inject_station_ids(text: str) -> str:
    _load_mock()
    result = text
    for name in sorted(_STATION_INDEX, key=len, reverse=True):
        if len(name) <= 3 and not name.startswith(("ms", "nr")):
            continue
        sid = _STATION_INDEX[name]
        if re.search(rf"(?i)\b{re.escape(name)}\s*\({sid}\)", result):
            continue
        pat = re.compile(re.escape(name), re.IGNORECASE)
        if name.startswith(("ms", "nr")):
            result = pat.sub(sid, result)
        elif pat.search(result):
            result = pat.sub(f"{name} ({sid})", result, count=1)
    return result


def _extract_station_ids(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in re.findall(r"\b(MS\d{2}|NR\d{2})\b", text, re.I):
        u = m.upper()
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _parse_route_endpoints(text: str, ids: list[str]) -> tuple[str, str]:
    m = re.search(
        r"(?:FROM|from)\s+(MS\d{2}|NR\d{2}).*?(?:TO|to)\s+(MS\d{2}|NR\d{2})",
        text,
        re.I,
    )
    if m:
        return m.group(1).upper(), m.group(2).upper()
    if len(ids) >= 2:
        return ids[0], ids[1]
    return ids[0], ids[0]


def _child_passenger(text: str) -> bool:
    return any(
        k in text.lower() for k in ("child", "children", "5-15", "5–15", "兒童", "小孩")
    )


def _delay_minutes(text: str) -> Optional[int]:
    m = re.search(r"(\d+)\s*(?:minutes?|mins?|分鐘)", text, re.I)
    return int(m.group(1)) if m else None


def _extract_date(text: str) -> Optional[str]:
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    return m.group(1) if m else None


def _extract_booking_id(text: str) -> Optional[str]:
    m = re.search(r"\b(BK-[A-Z0-9]{6})\b", text, re.I)
    return m.group(1).upper() if m else None


# ── Format helpers ────────────────────────────────────────────────────────────


def _format_route(data: dict, *, cost_mode: bool = False, child: bool = False) -> str:
    if not data.get("found"):
        return "No route found between those stations."
    fare = float(data.get("total_fare_usd", data.get("total_cost", 0)))
    if child:
        fare = round(fare * 0.5, 2)
        note = " (child half-price)"
    else:
        note = ""
    lines = ["**Route**"]
    if data.get("path"):
        lines.append("  → " + " → ".join(s["name"] for s in data["path"]))
    if data.get("interchange_points"):
        pts = ", ".join(s["name"] for s in data["interchange_points"])
        lines.append(f"  Interchange at: {pts}")
    if cost_mode:
        lines.append(f"  Estimated fare: ${fare:.2f} USD{note}")
    else:
        lines.append(f"  Estimated time: {data.get('total_time_min', '?')} min")
    return "\n".join(lines)


def _format_nr_schedules(
    rows: list[dict], origin: str, dest: str, child: bool = False
) -> str:
    if not rows:
        return f"No national rail services found from {origin} to {dest}."
    lines = [f"**National Rail: {origin} → {dest}**"]
    for r in rows[:6]:
        stops = r.get("stops_travelled", "?")
        fare_info = query_national_rail_fare(r["schedule_id"], "standard", stops)
        fare_val = fare_info["total_fare_usd"] if fare_info else "?"
        if child and fare_info:
            fare_val = round(float(fare_info["total_fare_usd"]) * 0.5, 2)
        first = str(r.get("first_train_time", ""))[:5]
        last = str(r.get("last_train_time", ""))[:5]
        freq = r.get("frequency_min", "?")
        stype = r.get("service_type", "")
        line = r.get("line", "")
        booked = r.get("booked_seats", 0)
        lines.append(
            f"  • **{r['schedule_id']}** {line} ({stype}) | "
            f"First: {first}  Last: {last}  Every: {freq} min | "
            f"Std fare: ${fare_val} | Booked seats today: {booked}"
        )
    return "\n".join(lines)


def _format_metro_schedules(rows: list[dict], origin: str, dest: str) -> str:
    if not rows:
        return f"No metro services found from {origin} to {dest}."
    lines = [f"**Metro: {origin} → {dest}**"]
    for r in rows[:4]:
        stops = r.get("stops_travelled", "?")
        base = float(r.get("base_fare_usd", 0))
        per = float(r.get("per_stop_rate_usd", 0))
        fare = round(base + per * (stops if isinstance(stops, int) else 0), 2)
        first = str(r.get("first_train_time", ""))[:5]
        last = str(r.get("last_train_time", ""))[:5]
        lines.append(
            f"  • **{r['schedule_id']}** Line {r.get('line', '?')} | "
            f"First: {first}  Last: {last} | "
            f"Stops: {stops} | Fare: ${fare}"
        )
    return "\n".join(lines)


def _format_bookings(data: dict, email: str) -> str:
    lines = [f"**Bookings for {email}**"]
    nr = data.get("national_rail", [])
    metro = data.get("metro", [])
    if not nr and not metro:
        lines.append("  No bookings found.")
        return "\n".join(lines)
    for b in nr[:5]:
        lines.append(
            f"  🚂 {b['booking_id']}: {b.get('origin_name', b.get('origin_station_id'))} → "
            f"{b.get('destination_name', b.get('destination_station_id'))} | "
            f"{b['travel_date']} | {b['status']} | ${b['amount_usd']}"
        )
    for t in metro[:5]:
        lines.append(
            f"  🚇 {t['trip_id']}: {t.get('origin_name', t.get('origin_station_id'))} → "
            f"{t.get('destination_name', t.get('destination_station_id'))} | "
            f"{t['travel_date']} | {t['status']} | ${t['amount_usd']}"
        )
    return "\n".join(lines)


# ── Policy / RAG ──────────────────────────────────────────────────────────────


def _policy_reply(msg: str) -> Optional[str]:
    """Use pgvector RAG to answer policy questions."""
    lower = msg.lower()
    policy_keywords = (
        "policy",
        "refund",
        "cancel",
        "compensation",
        "delay",
        "delayed",
        "bicycle",
        "bike",
        "luggage",
        "baggage",
        "pet",
        "규정",
        "退款",
        "補償",
        "行李",
        "攜帶",
        "delayed",
        "entitled",
    )
    if not any(k in lower for k in policy_keywords):
        return None

    try:
        embedding = llm.embed(msg)
        docs = query_policy_vector_search(embedding)
        if not docs:
            return None
        lines = [f"**Policy: {docs[0]['title']}**"]
        lines.append(docs[0]["content"][:800])
        if len(docs) > 1:
            lines.append(
                f"\n_Also relevant: {', '.join(d['title'] for d in docs[1:])}_"
            )
        return "\n".join(lines)
    except Exception:
        # pgvector not available — fall back to LLM
        return None


# ── Booking flow ──────────────────────────────────────────────────────────────


def _handle_booking(msg: str, augmented: str, email: Optional[str]) -> Optional[str]:
    """Multi-step: find schedules → pick seat → execute booking."""
    if not email:
        return "Please log in to make a booking."

    lower = msg.lower()
    ids = _extract_station_ids(augmented)
    travel_date = _extract_date(msg)
    child = _child_passenger(msg)
    fare_class = (
        "first"
        if any(k in lower for k in ("first class", "first-class", "頭等"))
        else "standard"
    )

    if len(ids) < 2:
        return "Please specify origin and destination station IDs (e.g. NR01, NR05)."

    origin, dest = _parse_route_endpoints(augmented, ids)
    if not origin.startswith("NR") or not dest.startswith("NR"):
        return "Booking is currently available for national rail only."

    if not travel_date:
        return f"Please specify a travel date (e.g. {date.today().isoformat()})."

    # 1. Find available schedules
    schedules = query_national_rail_availability(origin, dest, travel_date)
    if not schedules:
        return f"No national rail services from {origin} to {dest} on {travel_date}."

    schedule = schedules[0]
    sid = schedule["schedule_id"]
    stops = schedule["stops_travelled"]

    fare_info = query_national_rail_fare(sid, fare_class, stops)
    fare_usd = float(fare_info["total_fare_usd"]) if fare_info else 0
    if child:
        fare_usd = round(fare_usd * 0.5, 2)

    # 2. Get available seats
    available = query_available_seats(sid, travel_date, fare_class)
    if not available:
        return f"No {fare_class} seats available on {sid} for {travel_date}."

    selected = auto_select_adjacent_seats(available, 1)
    if not selected:
        return "Could not auto-select a seat."
    seat_id = selected[0]

    # 3. Get user_id
    profile = query_user_profile(email)
    if not profile:
        return "User profile not found."
    user_id = profile["user_id"]

    # 4. Execute booking
    ok, result = execute_booking(
        user_id=user_id,
        schedule_id=sid,
        origin_station_id=origin,
        destination_station_id=dest,
        travel_date=travel_date,
        fare_class=fare_class,
        seat_id=seat_id,
    )
    if not ok:
        return f"Booking failed: {result}"

    lines = ["**Booking Confirmed ✅**"]
    lines.append(f"  Booking ID: **{result['booking_id']}**")
    lines.append(f"  Route: {origin} → {dest}")
    lines.append(f"  Date: {travel_date} | Class: {fare_class} | Seat: {seat_id}")
    lines.append(f"  Amount: ${fare_usd:.2f} USD")
    lines.append(f"  Payment ID: {result['payment_id']}")
    return "\n".join(lines)


def _handle_cancellation(msg: str, email: Optional[str]) -> Optional[str]:
    if not email:
        return "Please log in to cancel a booking."
    booking_id = _extract_booking_id(msg)
    if not booking_id:
        return "Please provide a booking ID (e.g. BK-ABC123)."

    profile = query_user_profile(email)
    if not profile:
        return "User profile not found."

    ok, result = execute_cancellation(booking_id, profile["user_id"])
    if not ok:
        return f"Cancellation failed: {result}"

    lines = ["**Booking Cancelled ✅**"]
    lines.append(f"  Booking: {booking_id}")
    lines.append(f"  Original amount: ${result['original_amount_usd']:.2f}")
    lines.append(
        f"  Refund: ${result['refund_amount_usd']:.2f} (admin fee: ${result['admin_fee_usd']:.2f})"
    )
    lines.append(f"  Policy: {result['policy_note']}")
    lines.append(f"  Hours until departure: {result['hours_until_departure']}")
    return "\n".join(lines)


# ── Main data query dispatcher ────────────────────────────────────────────────


def _handle_data_query(
    msg: str, augmented: str, email: Optional[str]
) -> Optional[tuple[str, str]]:
    """Returns (reply, debug_label) or None."""
    lower = msg.lower()
    ids = _extract_station_ids(augmented)

    # Bookings history
    if email and any(
        k in lower
        for k in (
            "my booking",
            "my bookings",
            "show my",
            "show booking",
            "booking history",
            "my trips",
            "我的訂",
            "訂票紀錄",
            "訂單",
        )
    ):
        data = query_user_bookings(email)
        return _format_bookings(data, email), "db=postgres:query_user_bookings"

    # Cancellation
    if any(
        k in lower for k in ("cancel booking", "cancel my booking", "取消訂", "取消")
    ):
        reply = _handle_cancellation(msg, email)
        return reply, "db=postgres:execute_cancellation"

    # Booking
    if any(k in lower for k in ("book me", "make a booking", "book a", "幫我訂")):
        reply = _handle_booking(msg, augmented, email)
        return reply, "db=postgres:execute_booking"

    if len(ids) >= 2:
        origin, dest = _parse_route_endpoints(augmented, ids)
        child = _child_passenger(msg)

        # Alternative routes (closed station)
        if any(
            k in lower for k in ("closed", "alternative", "avoid", "if", "封閉", "避開")
        ):
            avoid = next((x for x in ids if x not in (origin, dest)), None)
            if avoid:
                routes = graph.query_alternative_routes(origin, dest, avoid)
                if not routes:
                    return (
                        f"No alternative routes from {origin} to {dest} avoiding {avoid}.",
                        "db=neo4j:query_alternative_routes",
                    )
                lines = [
                    f"**Alternative routes ({origin} → {dest}, avoiding {avoid})**"
                ]
                for i, legs in enumerate(routes, 1):
                    stops = [legs[0]["from_station_id"]] + [
                        lg["to_station_id"] for lg in legs
                    ]
                    t = legs[0].get("total_time_min", "?")
                    lines.append(f"  {i}. {' → '.join(stops)} (~{t} min)")
                return "\n".join(lines), "db=neo4j:query_alternative_routes"

        # National rail schedule query
        if origin.startswith("NR") and any(
            k in lower
            for k in (
                "train",
                "trains",
                "schedule",
                "service",
                "run",
                "班次",
                "時刻",
            )
        ):
            travel_date = _extract_date(msg)
            rows = query_national_rail_availability(origin, dest, travel_date)
            return _format_nr_schedules(
                rows, origin, dest, child
            ), "db=postgres:query_national_rail_availability"

        # Cross-network routing (must come before same-network branches)
        origin_is_metro = origin.startswith("MS")
        dest_is_metro = dest.startswith("MS")
        if origin_is_metro != dest_is_metro:
            data = graph.query_interchange_path(origin, dest)
            return _format_route(data, child=child), "db=neo4j:query_interchange_path"

        # Fastest / shortest route — Neo4j, not schedule lookup
        if any(
            k in lower
            for k in (
                "fastest",
                "quickest",
                "shortest",
                "route",
                "get from",
                "how do i get",
                "how to get",
                "最快",
            )
        ):
            data = graph.query_shortest_route(origin, dest)
            return _format_route(data, child=child), "db=neo4j:query_shortest_route"

        # Fare / cheapest — Neo4j
        if any(
            k in lower
            for k in (
                "fare",
                "price",
                "cost",
                "cheap",
                "cheapest",
                "多少錢",
                "便宜",
                "票價",
            )
        ):
            data = graph.query_cheapest_route(origin, dest)
            return _format_route(
                data, cost_mode=True, child=child
            ), "db=neo4j:query_cheapest_route"

        # Metro schedule lookup (only when explicitly asking about timetables)
        if (
            origin.startswith("MS")
            and dest.startswith("MS")
            and any(
                k in lower
                for k in (
                    "schedule",
                    "timetable",
                    "first train",
                    "last train",
                    "班次",
                    "時刻",
                )
            )
        ):
            rows = query_metro_schedules(origin, dest)
            return _format_metro_schedules(
                rows, origin, dest
            ), "db=postgres:query_metro_schedules"

        # Default: shortest route via Neo4j
        data = graph.query_shortest_route(origin, dest)
        return _format_route(data, child=child), "db=neo4j:query_shortest_route"

    # Single station: ripple / connections
    if len(ids) == 1:
        if any(
            k in lower for k in ("ripple", "affected", "disruption", "漣漪", "波及")
        ):
            affected = graph.query_delay_ripple(ids[0], hops=2)
            lines = [f"**Ripple effect from {ids[0]}**"]
            for a in affected[:10]:
                lines.append(
                    f"  • {a.get('name')} ({a['station_id']}) — {a.get('hops_away')} hop(s)"
                )
            return (
                "\n".join(lines)
                if affected
                else f"No nearby stations affected by {ids[0]}."
            ), "db=neo4j:query_delay_ripple"

        if any(k in lower for k in ("connection", "connect", "neighbour", "鄰站")):
            conns = graph.query_station_connections(ids[0])
            lines = [f"**Connections from {ids[0]}**"]
            for c in conns[:8]:
                lines.append(
                    f"  • {c['name']} ({c['station_id']}) via {c['relationship']} ({c.get('travel_time_min', '?')} min)"
                )
            return (
                "\n".join(lines) if conns else f"No connection data for {ids[0]}."
            ), "db=neo4j:query_station_connections"

    return None


# ── Entry point ───────────────────────────────────────────────────────────────


def run_agent(
    user_message: str,
    history: list[dict],
    debug: bool = False,
    current_user_email: Optional[str] = None,
) -> tuple:
    """
    Execute one conversational turn.

    Priority order:
      1. Auth-guarded write operations (booking, cancellation) via PostgreSQL
      2. Policy / RAG via pgvector
      3. Structured data queries (schedules, routes, seats) via PostgreSQL + Neo4j
      4. LLM fallback

    Always returns (answer, new_history) or (answer, new_history, debug_text)
    depending on the debug flag.
    """
    msg = user_message.strip()
    lower = msg.lower()
    augmented = _inject_station_ids(msg)
    ids = _extract_station_ids(augmented)
    debug_lines: list[str] = [f"user: {msg[:80]}"]

    def _ret(reply: str, label: str):
        debug_lines.append(label)
        new_h = history + [
            {"role": "user", "content": msg},
            {"role": "assistant", "content": reply},
        ]
        if debug:
            return reply, new_h, "\n".join(debug_lines)
        return reply, new_h

    # 1. Write operations — require login
    if current_user_email:
        # Cancellation
        if any(k in lower for k in ("cancel booking", "cancel my booking", "取消")):
            bk_match = re.search(r"BK-[A-Z0-9]+", msg, re.I)
            if bk_match:
                profile = query_user_profile(current_user_email)
                if not profile:
                    return _ret(
                        "User profile not found.", "db=postgres:query_user_profile:miss"
                    )
                ok, result = execute_cancellation(
                    bk_match.group(0).upper(), profile["user_id"]
                )
                if not ok:
                    return _ret(
                        f"Cancellation failed: {result}",
                        "db=postgres:execute_cancellation:fail",
                    )
                lines = ["**Booking Cancelled ✅**"]
                lines.append(f"  Booking: {bk_match.group(0).upper()}")
                lines.append(
                    f"  Refund: ${result['refund_amount_usd']:.2f}  (admin fee: ${result['admin_fee_usd']:.2f})"
                )
                lines.append(f"  Policy: {result['policy_note']}")
                return _ret("\n".join(lines), "db=postgres:execute_cancellation:ok")

        # Booking
        if any(k in lower for k in ("book me", "make a booking", "book a", "幫我訂")):
            reply = _handle_booking(msg, augmented, current_user_email)
            return _ret(reply, "db=postgres:execute_booking")

    # 2. Policy / RAG (pgvector)
    policy = _policy_reply(msg)
    if policy:
        return _ret(policy, "intent=policy_rag")

    # 3. Structured data queries
    result = _handle_data_query(msg, augmented, current_user_email)
    if result:
        reply, label = result
        return _ret(reply, f"intent=data | {label}")

    # 4. LLM fallback
    system = (
        f"You are the TransitFlow rail assistant. Today is {date.today().isoformat()}. "
        f"Logged-in user: {current_user_email or 'guest'}. "
        "Help with transit queries, schedules, fares, policies, and bookings."
    )
    answer = llm.chat(
        messages=history + [{"role": "user", "content": augmented}],
        system_prompt=system,
    )
    return _ret(answer, "intent=llm_fallback")
