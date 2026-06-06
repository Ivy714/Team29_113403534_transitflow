"""
TransitFlow — Neo4j Graph Database Layer
=========================================
Pathfinding and network analysis for the dual metro + national rail graph.

This module intentionally keeps two responsibilities together:
1) route discovery with graph traversal (shortest/weighted/alternative paths),
2) transport-specific projection logic (time, fare, interchange reporting).

The query surface is consumed by `skeleton/agent.py`, so functions return
JSON-serializable Python structures rather than Neo4j-native objects.
"""

from __future__ import annotations

from typing import Any, Optional

from neo4j import GraphDatabase
from neo4j.graph import Node, Path

from skeleton.config import (
    INTERCHANGE_WALKING_TIME_MIN,
    METRO_BASE_FARE_USD,
    METRO_PER_STOP_RATE_USD,
    NEO4J_PASSWORD,
    NEO4J_URI,
    NEO4J_USER,
    RAIL_FIRST_BASE_FARE_USD,
    RAIL_FIRST_PER_STOP_RATE_USD,
    RAIL_STANDARD_BASE_FARE_USD,
    RAIL_STANDARD_PER_STOP_RATE_USD,
)

try:
    _driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        _driver.verify_connectivity()
    except Exception:
        pass
except Exception:
    _driver = None


def _session():
    """Create a Neo4j session; verify connectivity and rebuild driver if stale."""
    global _driver
    if not _driver:
        raise RuntimeError("Neo4j driver is not initialized.")
    try:
        _driver.verify_connectivity()
    except Exception:
        try:
            _driver.close()
        except Exception:
            pass
        _driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        _driver.verify_connectivity()
    return _driver.session()


def _is_metro(station_id: str) -> bool:
    return station_id.upper().startswith("MS")


def _infer_network(
    origin_id: str, destination_id: str, network: str
) -> str:
    """
    Resolve the effective network scope for a query.

    - explicit `metro`/`rail` is honored as-is,
    - `auto` becomes:
      - `metro` when both endpoints are metro stations,
      - `rail` when both endpoints are rail stations,
      - `cross` when endpoints span both networks.
    """
    if network in ("metro", "rail"):
        return network
    if _is_metro(origin_id) and _is_metro(destination_id):
        return "metro"
    if not _is_metro(origin_id) and not _is_metro(destination_id):
        return "rail"
    return "cross"


def _rel_pattern(network: str) -> str:
    """Map network mode to the relationship pattern used in Cypher traversals."""
    if network == "metro":
        return "METRO_LINK"
    if network == "rail":
        return "RAIL_LINK"
    return "METRO_LINK|RAIL_LINK|INTERCHANGE_TO"


def _node_label(station_id: str) -> str:
    return "MetroStation" if _is_metro(station_id) else "NationalRailStation"


def _station_dict(node: Node) -> dict[str, Any]:
    """Normalize a Neo4j station node to a stable API response payload."""
    labels = list(node.labels)
    network = "metro" if "MetroStation" in labels else "rail"
    lines = node.get("lines")
    if lines is None and node.get("line"):
        lines = [node.get("line")]
    return {
        "station_id": node.get("station_id"),
        "name": node.get("name"),
        "lines": list(lines or []),
        "network": network,
    }


def _path_legs(path: Path) -> list[dict[str, Any]]:
    """Convert a Neo4j Path into ordered edge-level leg dictionaries."""
    nodes = list(path.nodes)
    legs: list[dict[str, Any]] = []
    for idx, rel in enumerate(path.relationships):
        legs.append(
            {
                "from_station_id": nodes[idx].get("station_id"),
                "to_station_id": nodes[idx + 1].get("station_id"),
                "relationship": rel.type,
                "line": rel.get("line"),
                "travel_time_min": rel.get("time_weight"),
            }
        )
    return legs


def _path_time(path: Path) -> int:
    """Aggregate total path duration from relationship `time_weight` values."""
    return int(
        sum(
            rel.get("time_weight") or 0
            for rel in path.relationships
        )
    )


def _stops_fare(
    station_ids: list[str],
    fare_class: str = "standard",
) -> float:
    """
    Compute stops-based fare from station sequence.

    Fare policy details:
    - Metro and rail are charged as separate tickets.
    - Each segment uses `base + (stops * per_stop_rate)`.
    - Rail supports both `standard` and `first` class pricing.
    """
    metro_ids = [sid for sid in station_ids if _is_metro(sid)]
    rail_ids = [sid for sid in station_ids if not _is_metro(sid)]
    total = 0.0

    if metro_ids:
        stops = max(0, len(metro_ids) - 1)
        total += METRO_BASE_FARE_USD + stops * METRO_PER_STOP_RATE_USD

    if rail_ids:
        stops = max(0, len(rail_ids) - 1)
        if fare_class == "first":
            total += RAIL_FIRST_BASE_FARE_USD + stops * RAIL_FIRST_PER_STOP_RATE_USD
        else:
            total += RAIL_STANDARD_BASE_FARE_USD + stops * RAIL_STANDARD_PER_STOP_RATE_USD

    return round(total, 2)


def _find_shortest_path(
    origin_id: str,
    destination_id: str,
    network: str,
    avoid_station_ids: Optional[set[str] | list[str]] = None,
) -> Optional[Path]:
    """
    Get an unweighted shortest path with optional station exclusions.

    This is the compatibility fallback when weighted procedures are unavailable
    or when callers explicitly prefer hop-based shortest-path semantics.
    """
    if not _driver:
        return None

    net = _infer_network(origin_id, destination_id, network)
    rels = _rel_pattern(net)
    start_label = _node_label(origin_id)
    end_label = _node_label(destination_id)

    avoid_clause = ""
    params: dict[str, Any] = {
        "origin_id": origin_id,
        "destination_id": destination_id,
    }
    if avoid_station_ids:
        avoid_list = sorted({s.upper() for s in avoid_station_ids})
        avoid_clause = "AND NONE(n IN nodes(p) WHERE n.station_id IN $avoid_ids)"
        params["avoid_ids"] = avoid_list

    # Small transit graph — cap hops to keep shortestPath fast (avoids *..25 enumeration).
    max_hops = 15 if net != "cross" else 20
    cypher = f"""
    MATCH (start:{start_label} {{station_id: $origin_id}}),
          (end:{end_label} {{station_id: $destination_id}})
    MATCH p = shortestPath(
        (start)-[:{rels}*..{max_hops}]-(end)
    )
    WHERE p IS NOT NULL {avoid_clause}
    RETURN p
    LIMIT 1
    """

    with _session() as session:
        record = session.run(cypher, **params).single()
        return record["p"] if record else None


def _find_weighted_path(
    origin_id: str,
    destination_id: str,
    network: str,
    weight_property: str = "time_weight",
    avoid_station_id: Optional[str] = None,
) -> Optional[tuple[Path, float]]:
    """
    Find the minimum-cost path for a relationship weight property.

    Preferred path engine:
    - APOC dijkstra for weighted optimization.
    Fallback:
    - plain shortest path plus local re-weighting calculation.
    """
    if not _driver:
        return None

    net = _infer_network(origin_id, destination_id, network)
    rels = _rel_pattern(net)
    start_label = _node_label(origin_id)
    end_label = _node_label(destination_id)

    avoid_clause = ""
    params: dict[str, Any] = {
        "origin_id": origin_id,
        "destination_id": destination_id,
    }
    if avoid_station_id:
        avoid_clause = "WHERE NONE(n IN nodes(path) WHERE n.station_id = $avoid_id)"
        params["avoid_id"] = avoid_station_id

    apoc_cypher = f"""
    MATCH (start:{start_label} {{station_id: $origin_id}}),
          (end:{end_label} {{station_id: $destination_id}})
    CALL apoc.algo.dijkstra(
        start, end,
        '{rels}',
        '{weight_property}'
    ) YIELD path, weight
    {avoid_clause}
    RETURN path, weight
    ORDER BY weight ASC
    LIMIT 1
    """

    with _session() as session:
        try:
            record = session.run(apoc_cypher, **params).single()
            if record:
                return record["path"], float(record["weight"])
        except Exception:
            pass

    path = _find_shortest_path(
        origin_id,
        destination_id,
        network,
        {avoid_station_id} if avoid_station_id else None,
    )
    if not path:
        return None
    weight = (
        _path_time(path)
        if weight_property == "time_weight"
        else sum(rel.get("fare_weight") or 0 for rel in path.relationships)
    )
    return path, float(weight)


def _route_result(
    path: Path,
    *,
    fare_class: str = "standard",
) -> dict[str, Any]:
    """Build the canonical route response structure shared by public APIs."""
    stations = [_station_dict(n) for n in path.nodes]
    station_ids = [s["station_id"] for s in stations]
    return {
        "found": True,
        "origin_id": station_ids[0],
        "destination_id": station_ids[-1],
        "total_time_min": _path_time(path),
        "total_fare_usd": _stops_fare(station_ids, fare_class),
        "path": stations,
        "legs": _path_legs(path),
        "interchange_points": [
            s for s in stations if s["station_id"].startswith("MS")
            and any(
                leg["relationship"] == "INTERCHANGE_TO"
                for leg in _path_legs(path)
                if leg["from_station_id"] == s["station_id"]
                or leg["to_station_id"] == s["station_id"]
            )
        ],
    }


def query_shortest_route(
    origin_id: str,
    destination_id: str,
    network: str = "auto",
) -> dict:
    """
    Find the fastest path between two stations (minimum travel time).

    Args:
        origin_id: e.g. "MS01" or "NR01"
        destination_id: e.g. "MS14" or "NR05"
        network: "metro", "rail", or "auto"

    Returns:
        dict with found, origin_id, destination_id, total_time_min, path, legs
    """
    if not _driver:
        return {"found": False, "error": "Neo4j driver is not initialized."}

    origin_id = origin_id.upper()
    destination_id = destination_id.upper()

    result = _find_weighted_path(
        origin_id, destination_id, network, weight_property="time_weight"
    )
    if not result:
        return {"found": False, "origin_id": origin_id, "destination_id": destination_id}

    path, _ = result
    data = _route_result(path)
    data["origin_id"] = origin_id
    data["destination_id"] = destination_id
    return data


def query_cheapest_route(
    origin_id: str,
    destination_id: str,
    network: str = "auto",
    fare_class: str = "standard",
) -> dict:
    """
    Find the cheapest path using stops-based fare rules from ticket_types.json.

    Args:
        fare_class: "standard" or "first" (national rail only)

    Returns:
        dict with found, total_fare_usd, path, legs
    """
    if not _driver:
        return {"found": False, "error": "Neo4j driver is not initialized."}

    origin_id = origin_id.upper()
    destination_id = destination_id.upper()

    result = _find_weighted_path(
        origin_id, destination_id, network, weight_property="fare_weight"
    )
    if not result:
        return {"found": False, "origin_id": origin_id, "destination_id": destination_id}

    path, _ = result
    data = _route_result(path, fare_class=fare_class)
    data["origin_id"] = origin_id
    data["destination_id"] = destination_id
    data["total_cost"] = data["total_fare_usd"]
    return data


def query_alternative_routes(
    origin_id: str,
    destination_id: str,
    avoid_station_id: str,
    network: str = "auto",
    max_routes: int = 3,
) -> list[list[dict]]:
    """
    Find routes that avoid a closed or delayed station.

    Uses repeated shortestPath (not full path enumeration) so queries finish
    quickly on the small course graph.

    Returns:
        List of routes; each route is a list of leg dicts.
        The first leg includes `total_time_min` so consumers can quickly rank
        alternatives without re-summing all edges.
    """
    if not _driver:
        return []

    origin_id = origin_id.upper()
    destination_id = destination_id.upper()
    avoid_station_id = avoid_station_id.upper()
    max_routes = max(1, min(max_routes, 5))

    avoid: set[str] = {avoid_station_id}
    routes: list[list[dict]] = []
    seen_signatures: set[tuple[str, ...]] = set()

    for _ in range(max_routes):
        path = _find_shortest_path(origin_id, destination_id, network, avoid)
        if not path:
            break
        sig = tuple(n.get("station_id") for n in path.nodes)
        if sig in seen_signatures:
            break
        seen_signatures.add(sig)
        legs = _path_legs(path)
        if legs:
            legs[0]["total_time_min"] = _path_time(path)
        routes.append(legs)
        # Block intermediate nodes so the next iteration finds a different path.
        for node in path.nodes[1:-1]:
            sid = node.get("station_id")
            if sid:
                avoid.add(sid)

    if routes or _infer_network(origin_id, destination_id, network) != "rail":
        return routes

    # Same-network rail search found nothing — try cross-network only when endpoints differ.
    if network == "auto" and not _is_metro(origin_id) and not _is_metro(destination_id):
        cross_avoid: set[str] = {avoid_station_id}
        for _ in range(max_routes):
            path = _find_shortest_path(origin_id, destination_id, "cross", cross_avoid)
            if not path:
                break
            sig = tuple(n.get("station_id") for n in path.nodes)
            if sig in seen_signatures:
                break
            seen_signatures.add(sig)
            legs = _path_legs(path)
            if legs:
                legs[0]["total_time_min"] = _path_time(path)
            routes.append(legs)
            for node in path.nodes[1:-1]:
                sid = node.get("station_id")
                if sid:
                    cross_avoid.add(sid)

    return routes


def query_interchange_path(origin_id: str, destination_id: str) -> dict:
    """
    Cross-network path (metro ↔ national rail) via INTERCHANGE_TO.

    Returns:
        dict with found, path, interchange_points, total_time_min
    """
    origin_id = origin_id.upper()
    destination_id = destination_id.upper()

    if _infer_network(origin_id, destination_id, "auto") != "cross":
        return {
            "found": False,
            "error": "Both stations are on the same network; use query_shortest_route instead.",
            "origin_id": origin_id,
            "destination_id": destination_id,
        }

    cypher = """
    MATCH (start {station_id: $origin_id}), (end {station_id: $destination_id})
    MATCH p = shortestPath(
        (start)-[:METRO_LINK|RAIL_LINK|INTERCHANGE_TO*..25]-(end)
    )
    WHERE ANY(r IN relationships(p) WHERE type(r) = 'INTERCHANGE_TO')
    WITH p,
         reduce(t = 0, r IN relationships(p) | t + coalesce(r.time_weight, 0)) AS total_time
    RETURN p, total_time
    ORDER BY total_time ASC
    LIMIT 1
    """

    if not _driver:
        return {"found": False, "error": "Neo4j driver is not initialized."}

    with _session() as session:
        record = session.run(
            cypher, origin_id=origin_id, destination_id=destination_id
        ).single()

    if not record:
        return {"found": False, "origin_id": origin_id, "destination_id": destination_id}

    data = _route_result(record["p"])
    data["total_time_min"] = int(record["total_time"])
    data["requires_interchange"] = True
    return data


def query_delay_ripple(delayed_station_id: str, hops: int = 2) -> list[dict]:
    """
    Stations within N hops of a disrupted station.

    Returns:
        List of {station_id, name, hops_away, lines_affected, network}
    """
    if not _driver:
        return []

    delayed_station_id = delayed_station_id.upper()
    hops = max(0, min(hops, 5))

    if hops == 0:
        label = _node_label(delayed_station_id)
        cypher = f"""
        MATCH (n:{label} {{station_id: $station_id}})
        RETURN
            n.station_id AS station_id,
            n.name AS name,
            0 AS hops_away,
            coalesce(n.lines, [n.line]) AS lines_affected,
            CASE WHEN 'MetroStation' IN labels(n) THEN 'metro' ELSE 'rail' END AS network
        """
        with _session() as session:
            row = session.run(cypher, station_id=delayed_station_id).single()
            return [dict(row)] if row else []

    cypher = f"""
    MATCH (origin {{station_id: $station_id}})
    MATCH (origin)-[rels:METRO_LINK|RAIL_LINK|INTERCHANGE_TO*1..{hops}]-(affected)
    WHERE affected.station_id <> $station_id
    WITH affected, min(size(rels)) AS hops_away,
         [r IN rels | r.line] AS line_list
    RETURN DISTINCT
        affected.station_id AS station_id,
        affected.name AS name,
        hops_away,
        coalesce(affected.lines, [affected.line]) AS lines_affected,
        CASE WHEN 'MetroStation' IN labels(affected) THEN 'metro' ELSE 'rail' END AS network
    ORDER BY hops_away, station_id
    """

    with _session() as session:
        return [dict(row) for row in session.run(cypher, station_id=delayed_station_id)]


def query_station_connections(station_id: str) -> list[dict]:
    """
    List direct neighbours and link metadata from a station.

    Args:
        station_id: e.g. "MS01" or "NR01"
    """
    if not _driver:
        return []

    station_id = station_id.upper()
    label = _node_label(station_id)

    cypher = f"""
    MATCH (s:{label} {{station_id: $station_id}})-[r]-(n)
    RETURN
        n.station_id AS station_id,
        n.name AS name,
        type(r) AS relationship,
        r.line AS line,
        coalesce(r.travel_time_min, r.walking_time_min) AS travel_time_min,
        CASE WHEN startNode(r) = s THEN 'outbound' ELSE 'inbound' END AS direction,
        CASE WHEN 'MetroStation' IN labels(n) THEN 'metro' ELSE 'rail' END AS network
  ORDER BY relationship, station_id
    """

    with _session() as session:
        return [dict(row) for row in session.run(cypher, station_id=station_id)]


class TransitQueryManager:
    """
    Backward-compatible facade expected by agent/tests.

    This class intentionally delegates to module-level functions so that callers
    can keep an object-oriented usage style while query logic stays functional.
    """

    def __init__(self):
        self.driver = _driver

    def close(self):
        pass

    def query_shortest_route(self, origin_id: str, destination_id: str, network: str = "auto"):
        return query_shortest_route(origin_id, destination_id, network)

    def query_cheapest_route(
        self,
        origin_id: str,
        destination_id: str,
        network: str = "auto",
        fare_class: str = "standard",
    ):
        return query_cheapest_route(origin_id, destination_id, network, fare_class)

    def query_alternative_routes(self, *args, **kwargs):
        return query_alternative_routes(*args, **kwargs)

    def query_interchange_path(self, origin_id: str, destination_id: str):
        return query_interchange_path(origin_id, destination_id)

    def query_delay_ripple(self, station_id: str, depth: int = 2, hops: int | None = None):
        return query_delay_ripple(station_id, hops=hops if hops is not None else depth)

    def query_station_connections(self, station_id: str):
        return query_station_connections(station_id)

    def get_station_details(self, station_id: str) -> dict:
        if not _driver:
            return {}
        label = _node_label(station_id.upper())
        with _session() as session:
            record = session.run(
                f"MATCH (s:{label} {{station_id: $sid}}) RETURN s",
                sid=station_id.upper(),
            ).single()
        return dict(record["s"]) if record else {}
