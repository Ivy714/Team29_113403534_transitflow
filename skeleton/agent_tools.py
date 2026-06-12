"""
LLM tool definitions and execution (README Advanced section).

Used when rule-based routing in ``agent.py`` does not match; results are grounded in DB/JSON.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from databases.graph import queries as graph
from databases.relational import queries as pg
from skeleton.config import DATA_DIR
from skeleton.policy_lookup import search_policy_json

TOOLS: list[dict] = [
    {
        "name": "check_national_rail_availability",
        "description": "List national rail schedules between two NR stations on a travel date.",
        "parameters": {
            "origin_id": {"type": "string", "description": "Origin NR station id e.g. NR01"},
            "destination_id": {"type": "string", "description": "Destination NR station id"},
            "travel_date": {"type": "string", "description": "YYYY-MM-DD"},
        },
        "required": ["origin_id", "destination_id"],
    },
    {
        "name": "query_metro_schedules",
        "description": "List metro schedules between two MS stations.",
        "parameters": {
            "origin_id": {"type": "string"},
            "destination_id": {"type": "string"},
        },
        "required": ["origin_id", "destination_id"],
    },
    {
        "name": "shortest_route",
        "description": "Fastest route between any two station ids (MS or NR).",
        "parameters": {
            "origin_id": {"type": "string"},
            "destination_id": {"type": "string"},
        },
        "required": ["origin_id", "destination_id"],
    },
    {
        "name": "search_policy",
        "description": "Search refund/travel/booking policies from official JSON data.",
        "parameters": {
            "query": {"type": "string", "description": "User policy question"},
        },
        "required": ["query"],
    },
    {
        "name": "alternative_routes",
        "description": "Routes that avoid a closed station id.",
        "parameters": {
            "origin_id": {"type": "string"},
            "destination_id": {"type": "string"},
            "avoid_station_id": {"type": "string"},
        },
        "required": ["origin_id", "destination_id", "avoid_station_id"],
    },
]

TOOLS_SCHEMA = """\
- check_national_rail_availability(origin_id, destination_id, travel_date?)
- query_metro_schedules(origin_id, destination_id)
- shortest_route(origin_id, destination_id)
- search_policy(query)
- alternative_routes(origin_id, destination_id, avoid_station_id)
"""


def execute_tool(
    name: str,
    params: dict[str, Any],
    *,
    user_email: Optional[str] = None,
) -> str:
    """Run one tool and return a plain-text result for the LLM or UI."""
    p = {k: (v.upper() if isinstance(v, str) and len(v) == 4 else v) for k, v in params.items()}

    if name == "check_national_rail_availability":
        rows = pg.query_national_rail_availability(
            p["origin_id"], p["destination_id"], p.get("travel_date")
        )
        if not rows:
            return f"No national rail {p['origin_id']}→{p['destination_id']}."
        lines = [f"National rail {p['origin_id']}→{p['destination_id']}:"]
        for r in rows[:6]:
            lines.append(
                f"  {r['schedule_id']} {r.get('service_type')} dep {r.get('first_train_time')}"
            )
        return "\n".join(lines)

    if name == "query_metro_schedules":
        rows = pg.query_metro_schedules(p["origin_id"], p["destination_id"])
        if not rows:
            return f"No metro {p['origin_id']}→{p['destination_id']}."
        return "\n".join(
            f"  {r['schedule_id']} line {r.get('line')} ({r.get('stops_travelled')} stops)"
            for r in rows[:6]
        )

    if name == "shortest_route":
        data = graph.query_shortest_route(p["origin_id"], p["destination_id"])
        if not data.get("found"):
            return "No route found."
        path = " → ".join(s["name"] for s in data.get("path", []))
        return f"Route: {path}\nTime: {data.get('total_time_min')} min"

    if name == "search_policy":
        text = search_policy_json(p.get("query", ""))
        if text:
            return text
        try:
            from skeleton.llm_provider import llm

            emb = llm.embed(p.get("query", ""))
            docs = pg.query_policy_vector_search(emb)
            if docs:
                return f"【{docs[0]['title']}】\n{docs[0]['content'][:700]}"
        except Exception:
            pass
        return "No matching policy in JSON/RAG. Try asking about RF005 delay, bicycles, or pets."

    if name == "alternative_routes":
        routes = graph.query_alternative_routes(
            p["origin_id"],
            p["destination_id"],
            p["avoid_station_id"],
            network="auto",
        )
        if not routes:
            tp = json.loads((DATA_DIR / "travel_policies.json").read_text(encoding="utf-8"))
            sc = tp.get("metro", {}).get("station_closures", {})
            return (
                f"No graph route {p['origin_id']}→{p['destination_id']} avoiding {p['avoid_station_id']}.\n"
                f"Guidance: {sc.get('unplanned_closures', '')} {sc.get('ticket_handling', '')}"
            )
        lines = [f"Alternatives avoiding {p['avoid_station_id']}:"]
        for i, legs in enumerate(routes, 1):
            stops = [legs[0]["from_station_id"]] + [lg["to_station_id"] for lg in legs]
            t = legs[0].get("total_time_min", "?")
            lines.append(f"  {i}. {' → '.join(stops)} ({t} min)")
        return "\n".join(lines)

    return f"Unknown tool: {name}"
