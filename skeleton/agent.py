"""
TransitFlow Agent — limited to graph DB + train-mock-data JSON.
(Relational / pgvector layers are implemented separately by the team.)
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from typing import Any, Optional

from databases.graph import queries as graph
from skeleton.config import (
    DATA_DIR,
    METRO_BASE_FARE_USD,
    METRO_PER_STOP_RATE_USD,
    RAIL_FIRST_BASE_FARE_USD,
    RAIL_FIRST_PER_STOP_RATE_USD,
    RAIL_STANDARD_BASE_FARE_USD,
    RAIL_STANDARD_PER_STOP_RATE_USD,
)
from skeleton.llm_provider import llm

_MOCK: dict[str, Any] = {}
_STATION_INDEX: dict[str, str] = {}


def _load_mock() -> None:
    global _STATION_INDEX
    if _MOCK:
        return
    files = {
        "users": "registered_users.json",
        "metro_stations": "metro_stations.json",
        "nr_stations": "national_rail_stations.json",
        "metro_schedules": "metro_schedules.json",
        "nr_schedules": "national_rail_schedules.json",
        "seat_layouts": "national_rail_seat_layouts.json",
        "bookings": "bookings.json",
        "metro_trips": "metro_travel_history.json",
        "payments": "payments.json",
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


def _stops_between(stops: list, origin: str, dest: str) -> Optional[int]:
    try:
        n = abs(stops.index(dest) - stops.index(origin))
        return n if n > 0 else None
    except ValueError:
        return None


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
        r"(?:FROM|從)\s+(MS\d{2}|NR\d{2}).*?(?:TO|到|→|->)\s+(MS\d{2}|NR\d{2})",
        text,
        re.I,
    )
    if m:
        return m.group(1).upper(), m.group(2).upper()
    if len(ids) >= 2:
        return ids[0], ids[1]
    return ids[0], ids[0]


# ── JSON-backed lookups (stand-in for relational queries) ─────────────────────

def _json_nr_availability(origin: str, dest: str, travel_date: Optional[str] = None) -> list[dict]:
    _load_mock()
    out = []
    for s in _MOCK.get("nr_schedules", []):
        stops = s["stops_in_order"]
        n = _stops_between(stops, origin, dest)
        if not n:
            continue
        row = {**s, "stops_travelled": n, "origin_station_id": origin, "destination_station_id": dest}
        if travel_date:
            booked = sum(
                1 for b in _MOCK.get("bookings", [])
                if b["schedule_id"] == s["schedule_id"]
                and b.get("travel_date") == travel_date
                and b.get("status") in ("confirmed", "completed")
                and b.get("seat_id")
            )
            row["booked_seats"] = booked
        out.append(row)
    return out


def _json_nr_fare(schedule_id: str, fare_class: str, stops: int) -> Optional[dict]:
    _load_mock()
    for s in _MOCK.get("nr_schedules", []):
        if s["schedule_id"] != schedule_id:
            continue
        rates = s["fare_classes"].get(fare_class.lower())
        if not rates:
            return None
        base = float(rates["base_fare_usd"])
        per = float(rates["per_stop_rate_usd"])
        return {
            "schedule_id": schedule_id,
            "fare_class": fare_class,
            "base_fare_usd": base,
            "per_stop_rate_usd": per,
            "stops_travelled": stops,
            "total_fare_usd": round(base + stops * per, 2),
        }
    return None


def _json_metro_schedules(origin: str, dest: str) -> list[dict]:
    _load_mock()
    out = []
    for s in _MOCK.get("metro_schedules", []):
        stops = s["stops_in_order"]
        n = _stops_between(stops, origin, dest)
        if n:
            out.append({**s, "stops_travelled": n})
    return out


def _json_metro_fare(schedule_id: str, stops: int) -> Optional[dict]:
    _load_mock()
    for s in _MOCK.get("metro_schedules", []):
        if s["schedule_id"] == schedule_id:
            base = float(s["base_fare_usd"])
            per = float(s["per_stop_rate_usd"])
            return {
                "schedule_id": schedule_id,
                "base_fare_usd": base,
                "per_stop_rate_usd": per,
                "stops_travelled": stops,
                "total_fare_usd": round(base + stops * per, 2),
            }
    return None


def _json_user_by_email(email: str) -> Optional[dict]:
    _load_mock()
    for u in _MOCK.get("users", []):
        if u["email"].lower() == email.lower():
            parts = u["full_name"].split(" ", 1)
            return {
                **u,
                "first_name": parts[0],
                "surname": parts[1] if len(parts) > 1 else "",
            }
    return None


def _json_user_bookings(email: str) -> dict:
    user = _json_user_by_email(email)
    if not user:
        return {"national_rail": [], "metro": []}
    uid = user["user_id"]
    rail = [b for b in _MOCK.get("bookings", []) if b["user_id"] == uid]
    metro = [t for t in _MOCK.get("metro_trips", []) if t["user_id"] == uid]
    return {"national_rail": rail, "metro": metro}


def _json_search_policy(query: str) -> list[dict]:
    _load_mock()
    docs: list[tuple[str, str, str]] = []
    for p in _MOCK.get("refund_policy", []):
        docs.append((p.get("label", p["policy_id"]), "refund", json.dumps(p, ensure_ascii=False)))
    docs.append(("Travel policies", "conduct", json.dumps(_MOCK.get("travel_policies", {}), ensure_ascii=False)))
    docs.append(("Booking rules", "booking", json.dumps(_MOCK.get("booking_rules", {}), ensure_ascii=False)))
    docs.append(("Ticket types", "ticket", json.dumps(_MOCK.get("ticket_types", []), ensure_ascii=False)))

    words = set(re.findall(r"\w+", query.lower()))
    scored = []
    for title, cat, content in docs:
        text_l = (title + content).lower()
        score = sum(1 for w in words if len(w) > 2 and w in text_l)
        if score:
            scored.append({"title": title, "category": cat, "content": content[:1200], "score": score})
    scored.sort(key=lambda x: -x["score"])
    return scored[:3]


# ── Intent handlers ───────────────────────────────────────────────────────────

def _child_passenger(text: str) -> bool:
    return any(k in text for k in ("兒童", "小孩", "child", "children", "5-15", "5–15"))


def _delay_minutes(text: str) -> Optional[int]:
    m = re.search(r"(\d+)\s*(?:minutes?|mins?|分鐘)", text, re.I)
    return int(m.group(1)) if m else None


def _refund_reply(msg: str) -> Optional[str]:
    if not any(k in msg.lower() for k in ("refund", "cancel", "退款", "取消", "compensation", "補償")):
        return None
    _load_mock()
    delay = _delay_minutes(msg)
    if delay is not None:
        for p in _MOCK.get("refund_policy", []):
            if p.get("policy_id") != "RF005":
                continue
            for rule in p.get("compensation_rules", []):
                cond = rule.get("condition", "")
                if 30 <= delay < 60 and ("30" in cond or "59" in cond):
                    return f"【RF005 {rule['rule_id']}】{cond} → {rule['compensation']}"
                if 60 <= delay < 120 and "60" in cond:
                    return f"【RF005 {rule['rule_id']}】{cond} → {rule['compensation']}"
                if delay >= 120 and "120" in cond:
                    return f"【RF005 {rule['rule_id']}】{cond} → {rule['compensation']}"
        return "延誤補償請參考 RF005；天災相關請查 RF009。"

    pid = re.search(r"\b(RF\d{3})\b", msg.upper())
    if pid:
        for p in _MOCK.get("refund_policy", []):
            if p["policy_id"] == pid.group(1):
                lines = [f"【{p.get('label', pid.group(1))}】"]
                for w in (p.get("cancellation_windows") or p.get("eligibility_rules") or [])[:5]:
                    lines.append(f"  • {w.get('condition', w.get('label', ''))}")
                return "\n".join(lines)

    hits = _json_search_policy(msg)
    if hits:
        return f"【政策摘要：{hits[0]['title']}】\n{hits[0]['content'][:600]}…"
    return "可詢問 RF001–RF009 或具體退款情境（例如：延誤 45 分鐘）。"


def _luggage_reply(msg: str) -> Optional[str]:
    if not any(k in msg.lower() for k in ("luggage", "行李", "baggage", "攜帶")):
        return None
    _load_mock()
    tp = _MOCK.get("travel_policies", {})
    net = "national_rail" if any(k in msg.lower() for k in ("rail", "國鐵", "train")) else "metro"
    lug = tp.get(net, {}).get("luggage", {})
    return (
        f"【{'國鐵' if net == 'national_rail' else '捷運'}行李】\n"
        f"  • 每人 {lug.get('items_per_passenger', '?')} 件\n"
        f"  • 尺寸：{lug.get('max_dimensions_per_item_cm', lug.get('notes', ''))}\n"
        f"  • {lug.get('placement', '')}"
    )


def _format_route(data: dict, *, cost_mode: bool = False, child: bool = False) -> str:
    if not data.get("found"):
        return "找不到可行路線。"
    fare = float(data.get("total_fare_usd", data.get("total_cost", 0)))
    if child:
        fare = round(fare * 0.5, 2)
        note = "（兒童半價）"
    else:
        note = ""
    lines = ["【路線】"]
    if data.get("path"):
        lines.append("  → " + " → ".join(s["name"] for s in data["path"]))
    if cost_mode:
        lines.append(f"  票價約 ${fare:.2f} USD {note}")
    else:
        lines.append(f"  時間約 {data.get('total_time_min', '?')} 分鐘")
    return "\n".join(lines)


def _handle_data_query(msg: str, augmented: str, email: Optional[str]) -> Optional[str]:
    lower = msg.lower()
    ids = _extract_station_ids(augmented)

    if email and any(k in lower for k in ("my booking", "我的訂", "show booking", "booking history", "訂票紀錄")):
        data = _json_user_bookings(email)
        lines = [f"【{email} 的訂單】"]
        for b in data["national_rail"][:5]:
            lines.append(
                f"  國鐵 {b['booking_id']}: {b['origin_station_id']}→{b['destination_station_id']} "
                f"{b['travel_date']} {b['status']} ${b['amount_usd']}"
            )
        for t in data["metro"][:5]:
            lines.append(
                f"  捷運 {t['trip_id']}: {t['origin_station_id']}→{t['destination_station_id']} "
                f"{t['travel_date']} {t['status']} ${t['amount_usd']}"
            )
        if not data["national_rail"] and not data["metro"]:
            lines.append("  （尚無紀錄）")
        return "\n".join(lines)

    if len(ids) >= 2:
        origin, dest = _parse_route_endpoints(augmented, ids)
        child = _child_passenger(msg)

        closed = any(k in lower for k in ("closed", "封閉", "關閉", "avoid", "避開"))
        if closed:
            avoid = next((x for x in ids if x not in (origin, dest)), None)
            if avoid:
                routes = graph.query_alternative_routes(origin, dest, avoid)
                if not routes:
                    return f"避開 {avoid} 後，{origin}→{dest} 無替代路線。"
                lines = [f"【避開 {avoid} 的替代路線】"]
                for i, legs in enumerate(routes, 1):
                    stops = [legs[0]["from_station_id"]] + [lg["to_station_id"] for lg in legs]
                    lines.append(f"  {i}. {' → '.join(stops)}")
                return "\n".join(lines)

        if any(k in lower for k in ("train", "schedule", "班次", "timetable", "服務")):
            if origin.startswith("NR"):
                rows = _json_nr_availability(origin, dest)
                if not rows:
                    return f"國鐵 {origin}→{dest} 無直達班次。"
                lines = [f"【國鐵班次 {origin}→{dest}】"]
                for r in rows[:4]:
                    fare = _json_nr_fare(r["schedule_id"], "standard", r["stops_travelled"])
                    lines.append(
                        f"  • {r['schedule_id']} {r['line']} {r['service_type']} "
                        f"首班 {r['first_train_time']} 票價標準艙 ${fare['total_fare_usd'] if fare else '?'}"
                    )
                return "\n".join(lines)
            rows = _json_metro_schedules(origin, dest)
            lines = [f"【捷運班次 {origin}→{dest}】"]
            for r in rows[:4]:
                lines.append(f"  • {r['schedule_id']} 線路 {r['line']} 停靠 {len(r['stops_in_order'])} 站")
            return "\n".join(lines) if rows else f"捷運 {origin}→{dest} 無班次。"

        if any(k in lower for k in ("fare", "price", "票價", "多少錢", "cheap", "便宜")):
            if origin.startswith("MS") and dest.startswith("MS"):
                rows = _json_metro_schedules(origin, dest)
                if rows:
                    f = _json_metro_fare(rows[0]["schedule_id"], rows[0]["stops_travelled"])
                    if f and child:
                        f["total_fare_usd"] = round(f["total_fare_usd"] * 0.5, 2)
                    return f"【捷運票價】${f['total_fare_usd'] if f else '?'} USD" if f else None
            data = graph.query_cheapest_route(origin, dest)
            return _format_route(data, cost_mode=True, child=child)

        if any(k in lower for k in ("ripple", "漣漪", "波及", "affected")):
            affected = graph.query_delay_ripple(ids[0], hops=2)
            lines = [f"【{ids[0]} 延誤影響範圍】"]
            for a in affected[:10]:
                lines.append(f"  • {a.get('name')} ({a['station_id']}) {a.get('hops_away')} 跳")
            return "\n".join(lines) if affected else f"{ids[0]} 無鄰近影響站。"

        want_cost = any(k in lower for k in ("cheap", "cheapest", "便宜", "fare", "票價"))
        data = graph.query_cheapest_route(origin, dest) if want_cost else graph.query_shortest_route(origin, dest)
        return _format_route(data, cost_mode=want_cost, child=child)

    if len(ids) == 1 and any(k in lower for k in ("connection", "鄰站", "連接")):
        conns = graph.query_station_connections(ids[0])
        lines = [f"【{ids[0]} 連接】"]
        for c in conns[:8]:
            lines.append(f"  • {c['name']} ({c['station_id']}) {c['relationship']} {c.get('travel_time_min', '')}分")
        return "\n".join(lines) if conns else f"{ids[0]} 無連接資料。"

    if any(k in lower for k in ("policy", "規定", "bicycle", "單車", "pet", "寵物")):
        hits = _json_search_policy(msg)
        if hits:
            return f"【{hits[0]['title']}】\n{hits[0]['content'][:700]}…"
    return None


def run_agent(
    user_message: str,
    history: list[dict],
    debug: bool = False,
    current_user_email: Optional[str] = None,
) -> tuple:
    msg = user_message.strip()
    augmented = _inject_station_ids(msg)
    debug_text = ""

    for handler in (_refund_reply, _luggage_reply):
        reply = handler(msg)
        if reply:
            new_h = history + [{"role": "user", "content": msg}, {"role": "assistant", "content": reply}]
            if debug:
                return reply, new_h, f"intent={handler.__name__}"
            return reply, new_h

    if any(k in msg.lower() for k in ("book", "訂票", "cancel", "取消訂單")) and current_user_email:
        reply = (
            "訂票／取消需使用 PostgreSQL 關聯層（`databases/relational/queries.py`）。"
            "目前此 agent 僅整合 Neo4j 路線與 JSON 查詢；可先查班次、票價與訂單紀錄。"
        )
        new_h = history + [{"role": "user", "content": msg}, {"role": "assistant", "content": reply}]
        if debug:
            return reply, new_h, "booking=requires_relational"
        return reply, new_h

    data_reply = _handle_data_query(msg, augmented, current_user_email)
    if data_reply:
        new_h = history + [{"role": "user", "content": msg}, {"role": "assistant", "content": data_reply}]
        if debug:
            return data_reply, new_h, "intent=json_or_graph"
        return data_reply, new_h

    system = (
        f"你是 TransitFlow 助手。今日 {date.today().isoformat()}。"
        "路線用 Neo4j；班次／票價／政策來自 train-mock-data JSON。"
        f"登入使用者：{current_user_email or '未登入'}。"
    )
    answer = llm.chat(messages=history + [{"role": "user", "content": augmented}], system_prompt=system)
    new_h = history + [{"role": "user", "content": msg}, {"role": "assistant", "content": answer}]
    if debug:
        return answer, new_h, "fallback=llm"
    return answer, new_h
