"""
TransitFlow conversational agent
==============================

Rule-based router that answers passenger questions by calling:

- **PostgreSQL** — schedules, fares, bookings, payments, policy RAG (pgvector)
- **Neo4j** — shortest/cheapest routes, alternatives, interchange, ripple, connections
- **train-mock-data JSON** — station name lookup and policy fallbacks when RAG is unavailable
- **LLM (Ollama/Gemini)** — only when no structured handler matches

Design goals (course README):
- National rail availability respects travel direction (origin stop before destination).
- Logged-in users can book/cancel national rail and metro via PostgreSQL booking functions.
- Policy questions prefer vector search over ``policy_chunks.json`` embeddings.

TASK 6 EXTENSION: seat occupancy handler — see ``TASK6.md``.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any, Optional

from databases.graph import queries as graph
from databases.relational import queries as pg
from databases.relational.queries import auto_select_adjacent_seats
from skeleton.config import DATA_DIR
from skeleton.agent_tools import TOOLS, execute_tool
from skeleton.llm_provider import llm
from skeleton.policy_lookup import search_policy_json

# Lazy-built map: lowercase station name or id -> canonical station_id (MS## / NR##)
_STATION_INDEX: dict[str, str] = {}


def _load_station_index() -> None:
    """Build ``_STATION_INDEX`` once from metro and national rail station JSON files."""
    global _STATION_INDEX
    if _STATION_INDEX:
        return
    for fname in ("metro_stations.json", "national_rail_stations.json"):
        path = DATA_DIR / fname
        if not path.exists():
            continue
        for s in json.loads(path.read_text(encoding="utf-8")):
            _STATION_INDEX[s["name"].strip().lower()] = s["station_id"]
            _STATION_INDEX[s["station_id"].lower()] = s["station_id"]


def _inject_station_ids(text: str) -> str:
    """
    Replace station names in free text with ``Name (ID)`` hints for the LLM and regex parsers.

    Example: ``Central Square`` -> ``Central Square (MS01)`` when not already annotated.
    """
    _load_station_index()
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
    """Return unique station IDs in order of first appearance (MS## / NR##)."""
    seen: set[str] = set()
    ordered: list[str] = []
    for m in re.findall(r"\b(MS\d{2}|NR\d{2})\b", text, re.I):
        sid = m.upper()
        if sid not in seen:
            seen.add(sid)
            ordered.append(sid)
    return ordered


def _extract_schedule_id(text: str) -> Optional[str]:
    """Extract a national rail schedule id such as ``NR_SCH01`` from the message."""
    m = re.search(r"\b(NR_SCH\d+)\b", text, re.I)
    return m.group(1).upper() if m else None


def _parse_route_endpoints(text: str, ids: list[str]) -> tuple[str, str]:
    """
    Resolve origin/destination from explicit FROM/TO phrasing or the first two IDs.

    Falls back to ``(ids[0], ids[0])`` when only one id is present.
    """
    m = re.search(
        r"(?:FROM|從)\s+(MS\d{2}|NR\d{2}).*?(?:TO|到|→|->)\s+(MS\d{2}|NR\d{2})",
        text,
        re.I,
    )
    if m:
        return m.group(1).upper(), m.group(2).upper()
    if len(ids) >= 2:
        return ids[0], ids[1]
    return ids[0], ids[0]


def _extract_travel_date(text: str) -> str:
    """Parse ``YYYY-MM-DD`` from the message or default to a demo date."""
    m = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    return m.group(1) if m else "2026-06-01"


def _extract_booking_id(text: str) -> Optional[str]:
    """Parse journey ids: ``BK-XXXX``, ``BK001``, ``MT-XXXX``, or ``MT009``."""
    m = re.search(r"\b(BK-[A-Z0-9]+|BK\d{3,}|MT-[A-Z0-9]+|MT\d{3,})\b", text, re.I)
    return m.group(1).upper() if m else None


def _extract_avoid_station(text: str, origin: str, dest: str, ids: list[str]) -> Optional[str]:
    """
    Identify which station the user wants to avoid (closed / blocked).

    Prefers explicit patterns like ``station (NR03) is closed`` so name injection
    (e.g. Old Town -> MS07) does not override the parenthesised id.
    """
    m = re.search(
        r"station\s*\((MS\d{2}|NR\d{2})\)\s+is\s+(?:closed|shut|封閉|關閉)",
        text,
        re.I,
    )
    if m:
        return m.group(1).upper()
    m = re.search(
        r"\b(MS\d{2}|NR\d{2})\b[^.]{0,60}?\b(?:is\s+)?(?:closed|shut|封閉|關閉)",
        text,
        re.I,
    )
    if m:
        sid = m.group(1).upper()
        if sid not in (origin, dest):
            return sid
    m = re.search(
        r"(?:closed|shut|封閉|關閉)[^.]{0,60}?\b(MS\d{2}|NR\d{2})\b",
        text,
        re.I,
    )
    if m:
        return m.group(1).upper()
    for sid in ids:
        if sid not in (origin, dest):
            return sid
    return None


def _policy_query_text(msg: str, lower: str) -> str:
    """Rewrite the user message into a richer embedding query for pgvector RAG."""
    if any(k in lower for k in ("bicycle", "bike", "腳踏車", "自行車")):
        if any(k in lower for k in ("national", "rail", "國鐵", "train")):
            return "national rail bicycle foldable standard peak hour policy"
        if "metro" in lower or "捷運" in msg:
            return "metro bicycle foldable standard not permitted policy"
    if any(k in lower for k in ("pet", "dog", "cat", "寵物", "狗", "貓")):
        net = "national rail" if any(k in lower for k in ("rail", "國鐵", "train")) else "metro"
        return f"{net} pet animal carrier policy"
    return msg


def _policy_search(query: str) -> list[dict]:
    """JSON keyword match first, then pgvector RAG when embeddings are available."""
    json_hit = search_policy_json(query)
    if json_hit:
        return [{"title": "Policy (train-mock-data JSON)", "content": json_hit, "similarity": 1.0}]
    try:
        emb = llm.embed(query)
        docs = pg.query_policy_vector_search(emb)
        if docs:
            return docs
    except Exception:
        pass
    return []


def _format_policy_docs(docs: list[dict], limit: int = 900) -> str:
    """Format the top policy hit for chat display."""
    if not docs:
        return ""
    lines = [f"【{docs[0]['title']}】", docs[0]["content"][:limit]]
    if docs[0].get("similarity") is not None:
        lines.append(f"\n(similarity {float(docs[0]['similarity']):.2f})")
    return "\n".join(lines)


def _rf005_rule_for_delay(minutes: int) -> Optional[dict]:
    """Return the RF005 compensation rule that applies to ``minutes`` (from JSON)."""
    if minutes < 30:
        return None
    for p in json.loads((DATA_DIR / "refund_policy.json").read_text(encoding="utf-8")):
        if p.get("policy_id") != "RF005":
            continue
        for rule in p.get("compensation_rules", []):
            rid = rule.get("rule_id", "")
            if minutes >= 120 and rid == "RF005_R3":
                return rule
            if 60 <= minutes < 120 and rid == "RF005_R2":
                return rule
            if 30 <= minutes < 60 and rid == "RF005_R1":
                return rule
    return None


def _format_refund_delay(minutes: int) -> str:
    """Deterministic RF005 delay compensation from ``refund_policy.json`` (matches mock data)."""
    rule = _rf005_rule_for_delay(minutes)
    if not rule:
        if minutes < 30:
            return (
                "【RF005】Delays under 30 minutes are not eligible for delay compensation "
                "under operator-fault rules."
            )
        return "See RF005 for delay compensation; RF009 for natural disaster disruption."
    claim = rule.get("how_to_claim", "Submit via app or customer service within 28 days.")
    return (
        f"【RF005 — {rule['rule_id']}】\n"
        f"  Condition: {rule['condition']}\n"
        f"  Compensation: {rule['compensation']}\n"
        f"  How to claim: {claim}"
    )


def _format_route(data: dict, *, cost_mode: bool = False, child: bool = False) -> str:
    """Pretty-print a graph route result (time or fare)."""
    if not data.get("found"):
        return "No route found."
    fare = float(data.get("total_fare_usd", data.get("total_cost", 0)))
    if child:
        fare = round(fare * 0.5, 2)
        note = " (child half fare)"
    else:
        note = ""
    lines = ["【Route】"]
    if data.get("path"):
        lines.append("  → " + " → ".join(s["name"] for s in data["path"]))
    if cost_mode:
        lines.append(f"  Fare approx. ${fare:.2f} USD{note}")
    else:
        lines.append(f"  Time approx. {data.get('total_time_min', '?')} min{note}")
    return "\n".join(lines)


def _format_booking_result(ok: bool, res: Any) -> str:
    """Format ``execute_booking`` success/failure for the chat UI."""
    if not ok:
        return f"Booking failed: {res}"
    return (
        f"【Booking confirmed】\n"
        f"  booking_id: {res.get('booking_id')}\n"
        f"  schedule: {res.get('schedule_id')}\n"
        f"  route: {res.get('origin_station_id')} → {res.get('destination_station_id')}\n"
        f"  date: {res.get('travel_date')}\n"
        f"  class: {res.get('fare_class')}\n"
        f"  seat: {res.get('coach')}{res.get('seat_id')}\n"
        f"  amount: ${res.get('amount_usd')} USD\n"
        f"  status: {res.get('status')}"
    )


def _format_metro_booking_result(ok: bool, res: Any) -> str:
    """Format ``execute_metro_booking`` success/failure."""
    if not ok:
        return f"Metro booking failed: {res}"
    return (
        f"【Metro ticket confirmed】\n"
        f"  trip_id: {res.get('trip_id')}\n"
        f"  schedule: {res.get('schedule_id')}\n"
        f"  route: {res.get('origin_station_id')} → {res.get('destination_station_id')}\n"
        f"  date: {res.get('travel_date')}\n"
        f"  type: {res.get('ticket_type')}\n"
        f"  amount: ${res.get('amount_usd')} USD\n"
        f"  status: {res.get('status')}"
    )


def _format_cancel_result(ok: bool, res: Any) -> str:
    """Format ``execute_cancellation`` success/failure for the chat UI."""
    if not ok:
        return f"Cancellation failed: {res}"
    return (
        f"【Cancellation complete】\n"
        f"  booking_id: {res.get('booking_id')}\n"
        f"  original: ${res.get('original_amount_usd')} USD\n"
        f"  refund: ${res.get('refund_amount_usd')} USD\n"
        f"  admin fee: ${res.get('admin_fee_usd')} USD\n"
        f"  policy: {res.get('policy_note')}"
    )


def _format_seat_occupancy(occ: dict) -> str:
    """Format Task 4 ``query_schedule_seat_occupancy`` result."""
    return (
        f"【Seat occupancy — {occ['schedule_id']} on {occ['travel_date']} "
        f"({occ['fare_class']})】\n"
        f"  total seats: {occ['total_seats']}\n"
        f"  booked: {occ['booked_seats']}\n"
        f"  available: {occ['available_seats']}"
    )


def _handle_booking_cancel(msg: str, augmented: str, email: Optional[str]) -> Optional[str]:
    """
    Authenticated write path: national rail booking and cancellation.

    Returns None when the message is not a book/cancel intent.
    """
    if not email:
        if any(k in msg.lower() for k in ("book", "訂票", "cancel", "取消")):
            return "Please log in (Login button, top right) before booking or cancelling."
        return None

    profile = pg.query_user_profile(email)
    if not profile:
        return "User profile not found. Please log in again."

    lower = msg.lower()
    uid = profile["user_id"]

    # --- Cancellation (exclude generic "cancellation policy" questions) ---
    if any(k in lower for k in ("cancel", "取消")) and not any(
        k in lower for k in ("policy", "政策")
    ):
        bid = _extract_booking_id(augmented)
        if not bid:
            return "Please provide a booking/trip id, e.g. Cancel booking BK-XXXX or MT-XXXX"
        ok, res = pg.execute_cancellation(bid, uid)
        return _format_cancel_result(ok, res)

    # --- New national rail booking ---
    not_viewing = not any(
        k in lower for k in ("my booking", "show my", "booking history", "我的訂單")
    )
    wants_book = not_viewing and (
        any(
            k in lower
            for k in ("book me", "make a booking", "book a ticket", "訂票", "幫我訂", "buy a ticket")
        )
        or re.search(r"\bbook\b", lower)
    )

    if wants_book:
        ids = _extract_station_ids(augmented)
        if len(ids) < 2:
            return "Booking requires origin and destination, e.g. Book NR01 to NR05 on 2026-06-01"

        origin, dest = _parse_route_endpoints(augmented, ids)
        travel_date = _extract_travel_date(augmented)

        # --- Metro online booking (MS stations) ---
        if origin.startswith("MS") and dest.startswith("MS"):
            ticket_type = "day_pass" if "day pass" in lower or "day_pass" in lower else "single"
            rows = pg.query_metro_schedules(origin, dest)
            if not rows:
                return f"No metro service {origin}→{dest}."
            schedule_id = rows[0]["schedule_id"]
            m = re.search(r"\b(MS_SCH\d+)\b", augmented, re.I)
            if m:
                schedule_id = m.group(1).upper()
            ok, res = pg.execute_metro_booking(
                user_id=uid,
                schedule_id=schedule_id,
                origin_station_id=origin,
                destination_station_id=dest,
                travel_date=travel_date,
                ticket_type=ticket_type,
            )
            return _format_metro_booking_result(ok, res)

        if not (origin.startswith("NR") and dest.startswith("NR")):
            return "Online booking supports national rail (NR) or metro (MS) station pairs."

        fare_class = "first" if "first" in lower else "standard"

        avail = pg.query_national_rail_availability(origin, dest, travel_date)
        if not avail:
            return f"No national rail service {origin}→{dest} on {travel_date}."

        last_error = ""
        for row in avail:
            schedule_id = row["schedule_id"]
            seats = pg.query_available_seats(schedule_id, travel_date, fare_class)
            if not seats:
                continue

            seat_ids = [s["seat_id"] for s in seats]
            if "any" in lower or "auto" in lower:
                seat_ids = auto_select_adjacent_seats(seats, 1) or seat_ids

            for seat_id in seat_ids:
                ok, res = pg.execute_booking(
                    user_id=uid,
                    schedule_id=schedule_id,
                    origin_station_id=origin,
                    destination_station_id=dest,
                    travel_date=travel_date,
                    fare_class=fare_class,
                    seat_id=seat_id,
                    ticket_type="return" if "return" in lower else "single",
                )
                if ok:
                    return _format_booking_result(ok, res)
                last_error = str(res)
                if "already have booking" in last_error.lower():
                    return f"Booking failed: {last_error}"
                if "already booked" in last_error.lower() or "just taken" in last_error.lower():
                    continue

        if last_error:
            return f"Booking failed: {last_error}"
        return f"No {fare_class} seats left for {origin}→{dest} on {travel_date}."

    return None


def _handle_data_query(msg: str, augmented: str, email: Optional[str]) -> Optional[str]:
    """
    Read-only handlers: bookings, policies, schedules, routes, payments, seat occupancy.

    Returns None when no rule matches (caller may fall back to LLM).
    """
    lower = msg.lower()
    ids = _extract_station_ids(augmented)

    # --- User booking history (PostgreSQL) ---
    if email and any(
        k in lower
        for k in ("my booking", "my bookings", "show my", "booking history", "我的訂", "訂單")
    ):
        data = pg.query_user_bookings(email)
        lines = [f"【Bookings for {email}】"]
        for b in data["national_rail"][:6]:
            lines.append(
                f"  NR {b['booking_id']}: {b.get('origin_name', b.get('origin_station_id'))}"
                f"→{b.get('destination_name', b.get('destination_station_id'))} "
                f"{b['travel_date']} {b['status']} ${b['amount_usd']}"
            )
        for t in data["metro"][:6]:
            lines.append(
                f"  Metro {t['trip_id']}: {t.get('origin_name')}→{t.get('destination_name')} "
                f"{t['travel_date']} {t['status']} ${t['amount_usd']}"
            )
        if not data["national_rail"] and not data["metro"]:
            lines.append("  (no records)")
        return "\n".join(lines)

    # --- Task 4: seat occupancy for a specific schedule + date ---
    sched = _extract_schedule_id(augmented)
    if sched and any(
        k in lower
        for k in (
            "seat", "seats", "occupancy", "available", "remaining", "capacity",
            "空位", "座位", "剩餘",
        )
    ):
        travel_date = _extract_travel_date(augmented)
        fare_class = "first" if "first" in lower else "standard"
        occ = pg.query_schedule_seat_occupancy(sched, travel_date, fare_class)
        return _format_seat_occupancy(occ)

    # --- Delay compensation (RAG first, then JSON RF005) ---
    delay = None
    m = re.search(r"(\d+)\s*(?:minutes?|mins?|分鐘)", msg, re.I)
    if m:
        delay = int(m.group(1))
    if delay is not None and any(k in lower for k in ("delay", "compensation", "延誤", "補償")):
        # Prefer JSON tier lookup when minutes are explicit (RAG can mis-rank similar chunks).
        answer = _format_refund_delay(delay)
        if _rf005_rule_for_delay(delay):
            return answer
        rag = _policy_search(f"delay compensation {delay} minutes refund policy")
        if rag:
            return _format_policy_docs(rag)
        return answer

    # --- Payment status for a booking id ---
    bid = _extract_booking_id(augmented)
    if bid and any(k in lower for k in ("payment", "paid", "refund status", "付款", "支付")):
        pay = pg.query_payment_info(bid)
        if pay:
            return (
                f"【Payment — {bid}】\n"
                f"  payment_id: {pay.get('payment_id')}\n"
                f"  amount: ${pay.get('amount_usd')} USD\n"
                f"  method: {pay.get('method')}\n"
                f"  status: {pay.get('status')}\n"
                f"  paid_at: {pay.get('paid_at')}"
            )
        return f"No payment record for {bid}."

    # --- Luggage policy (RAG + JSON fallback) ---
    if any(k in lower for k in ("luggage", "行李", "baggage")):
        docs = _policy_search(
            "metro luggage policy" if "metro" in lower or "捷運" in msg else "national rail luggage"
        )
        if docs:
            return _format_policy_docs(docs, 800)
        tp = json.loads((DATA_DIR / "travel_policies.json").read_text(encoding="utf-8"))
        net = "national_rail" if any(k in lower for k in ("rail", "國鐵", "train")) else "metro"
        lug = tp.get(net, {}).get("luggage", {})
        return (
            f"【Luggage — {net}】\n"
            f"  items per passenger: {lug.get('items_per_passenger', '?')}\n"
            f"  {lug.get('max_dimensions_per_item_cm', lug.get('notes', ''))}"
        )

    # --- General policy / refund / bicycle / pets ---
    policy_kw = (
        "policy", "refund", "政策", "退款", "bicycle", "bike", "寵物", "pet",
        "compensation", "補償",
    )
    if any(k in lower for k in policy_kw):
        docs = _policy_search(_policy_query_text(msg, lower))
        if docs:
            return _format_policy_docs(docs)
        if any(k in lower for k in ("bicycle", "bike", "腳踏車", "自行車")):
            tp = json.loads((DATA_DIR / "travel_policies.json").read_text(encoding="utf-8"))
            net = "national_rail" if any(k in lower for k in ("national", "rail", "國鐵", "train")) else "metro"
            bikes = tp.get(net, {}).get("bicycles", {})
            fold = bikes.get("foldable_bicycles", {})
            std = bikes.get("standard_bicycles", {})
            lines = [f"【Bicycle policy — {net}】"]
            if fold:
                lines.append(
                    f"  Foldable: {'yes' if fold.get('permitted') else 'no'} — "
                    f"{fold.get('conditions', fold.get('notes', ''))}"
                )
            if std:
                lines.append(
                    f"  Standard: {'yes' if std.get('permitted') else 'no'} — "
                    f"{std.get('conditions') or std.get('reason', '')}"
                )
            return "\n".join(lines)

    if len(ids) >= 2:
        origin, dest = _parse_route_endpoints(augmented, ids)
        child = any(k in lower for k in ("child", "兒童", "小孩"))

        # --- Timetable / availability (direction-correct for national rail) ---
        schedule_kw = (
            "trains run from", "train from", "trains from", "trains run",
            "train", "schedule", "班次", "timetable", "服務", "departures",
        )
        if any(k in lower for k in schedule_kw) and not any(
            k in lower for k in ("route", "fastest", "shortest", "怎麼去", "how do i get")
        ):
            if origin.startswith("NR"):
                rows = pg.query_national_rail_availability(origin, dest)
                if not rows:
                    return f"No national rail {origin}→{dest}."
                lines = [f"【National rail {origin}→{dest}】"]
                for r in rows[:4]:
                    fare = pg.query_national_rail_fare(
                        r["schedule_id"], "standard", r.get("stops_travelled", 1)
                    )
                    t = r.get("first_train_time", "")
                    if hasattr(t, "strftime"):
                        t = t.strftime("%H:%M")
                    lines.append(
                        f"  • {r['schedule_id']} {r.get('line')} {r.get('service_type')} "
                        f"departs {t} standard ${fare['total_fare_usd'] if fare else '?'}"
                    )
                return "\n".join(lines)
            rows = pg.query_metro_schedules(origin, dest)
            lines = [f"【Metro {origin}→{dest}】"]
            for r in rows[:4]:
                fare = pg.query_metro_fare(r["schedule_id"], r.get("stops_travelled", 1))
                extra = f" ${fare['total_fare_usd']}" if fare else ""
                lines.append(f"  • {r['schedule_id']} line {r.get('line')}{extra}")
            return "\n".join(lines) if rows else f"No metro {origin}→{dest}."

        # --- Avoid closed station (Neo4j alternative routes) ---
        closed = any(k in lower for k in ("closed", "封閉", "關閉", "avoid", "避開"))
        if closed:
            avoid = _extract_avoid_station(augmented, origin, dest, ids)
            if avoid:
                network = "rail" if origin.startswith("NR") and dest.startswith("NR") else "auto"
                routes = graph.query_alternative_routes(origin, dest, avoid, network=network)
                if not routes and network == "rail":
                    routes = graph.query_alternative_routes(
                        origin, dest, avoid, network="auto"
                    )
                if not routes:
                    guidance = search_policy_json(
                        f"station {avoid} closed alternative routes"
                    ) or ""
                    base = (
                        f"No alternative route {origin}→{dest} avoiding {avoid}. "
                        f"The NR1 corridor has no rail bypass of {avoid} in the network map."
                    )
                    if guidance:
                        return f"{base}\n\n{guidance}"
                    return base
                lines = [f"【Routes avoiding {avoid}】"]
                for i, legs in enumerate(routes, 1):
                    stops = [legs[0]["from_station_id"]] + [lg["to_station_id"] for lg in legs]
                    lines.append(f"  {i}. {' → '.join(stops)}")
                return "\n".join(lines)

        # --- Cross-network "how do I get" (explicit interchange path) ---
        cross = (origin.startswith("MS") and dest.startswith("NR")) or (
            origin.startswith("NR") and dest.startswith("MS")
        )
        if cross and any(
            k in lower for k in ("how do i get", "how to get", "get from", "怎麼去", "怎麼走")
        ):
            data = graph.query_interchange_path(origin, dest)
            if data.get("found"):
                return _format_route(data, cost_mode=False, child=child)

        # --- Delay ripple from a station (Neo4j) ---
        if any(k in lower for k in ("ripple", "漣漪", "波及")):
            affected = graph.query_delay_ripple(ids[0], hops=2)
            lines = [f"【Delay ripple from {ids[0]}】"]
            for a in affected[:10]:
                lines.append(f"  • {a.get('name')} ({a.get('station_id')})")
            return "\n".join(lines) if affected else f"No ripple neighbours for {ids[0]}."

        # --- Shortest or cheapest route (Neo4j) ---
        want_cost = any(k in lower for k in ("cheap", "cheapest", "便宜", "fare", "票價", "多少錢"))
        data = (
            graph.query_cheapest_route(origin, dest)
            if want_cost
            else graph.query_shortest_route(origin, dest)
        )
        return _format_route(data, cost_mode=want_cost, child=child)

    # --- Single-station connections (Neo4j) ---
    if len(ids) == 1 and any(k in lower for k in ("connection", "鄰站", "連接")):
        conns = graph.query_station_connections(ids[0])
        lines = [f"【Connections from {ids[0]}】"]
        for c in conns[:8]:
            lines.append(
                f"  • {c.get('name')} ({c.get('station_id')}) "
                f"{c.get('relationship')} {c.get('travel_time_min', '')} min"
            )
        return "\n".join(lines) if conns else f"No connections for {ids[0]}."

    return None


def _try_llm_tool_calls(augmented: str, email: Optional[str]) -> Optional[str]:
    """README Advanced: LLM picks a tool; we execute it against DB/JSON (grounded)."""
    if not llm.ollama_available():
        return None
    try:
        calls = llm.ollama_tool_call(
            [],
            TOOLS,
            augmented,
            system_prompt=(
                "Pick at most one TransitFlow tool to answer the user. "
                "Use exact station ids from the message."
            ),
        )
    except Exception:
        return None
    if not calls:
        return None

    parts: list[str] = []
    for call in calls[:2]:
        name = call.get("name", "")
        params = call.get("params") or {}
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except json.JSONDecodeError:
                params = {}
        parts.append(execute_tool(name, params, user_email=email))
    return "\n\n".join(parts) if parts else None


def run_agent(
    user_message: str,
    history: list[dict],
    debug: bool = False,
    current_user_email: Optional[str] = None,
) -> tuple:
    """
    Execute one chat turn.

    Args:
        user_message: Raw user text from the UI.
        history: Prior LLM messages ``[{"role": "user"|"assistant", "content": ...}, ...]``.
        debug: When True, return a third element describing which handler ran.
        current_user_email: Logged-in user email, or None for guest.

    Returns:
        ``(reply, updated_history)`` or ``(reply, updated_history, debug_info)``.
    """
    msg = user_message.strip()
    augmented = _inject_station_ids(msg)

    for handler_name, handler in (
        ("booking_cancel", lambda: _handle_booking_cancel(msg, augmented, current_user_email)),
        ("data", lambda: _handle_data_query(msg, augmented, current_user_email)),
    ):
        reply = handler()
        if reply:
            new_h = history + [{"role": "user", "content": msg}, {"role": "assistant", "content": reply}]
            if debug:
                return reply, new_h, f"intent={handler_name}"
            return reply, new_h

    tool_answer = _try_llm_tool_calls(augmented, current_user_email)
    if tool_answer:
        new_h = history + [{"role": "user", "content": msg}, {"role": "assistant", "content": tool_answer}]
        if debug:
            return tool_answer, new_h, "fallback=llm_tools"
        return tool_answer, new_h

    system = (
        f"You are TransitFlow assistant. Today is {date.today().isoformat()}. "
        f"User: {current_user_email or 'guest'}. "
        "Answer only from TransitFlow data shown to you. "
        "If you lack database results, say you cannot confirm and suggest a specific query. "
        "Never invent schedules, fares, or policy rules."
    )
    answer = llm.chat(messages=history + [{"role": "user", "content": augmented}], system_prompt=system)
    new_h = history + [{"role": "user", "content": msg}, {"role": "assistant", "content": answer}]
    if debug:
        return answer, new_h, "fallback=llm"
    return answer, new_h
