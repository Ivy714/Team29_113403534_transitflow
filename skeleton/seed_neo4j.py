"""
TransitFlow — Neo4j seeding
=============================
1. Applies constraints from databases/graph/seed.cypher
2. Loads metro_stations.json and national_rail_stations.json
3. Creates MetroStation / NationalRailStation nodes and all links
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from neo4j import GraphDatabase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skeleton.config import (
    DATA_DIR,
    INTERCHANGE_WALKING_TIME_MIN,
    METRO_BASE_FARE_USD,
    METRO_PER_STOP_RATE_USD,
    NEO4J_DATABASE,
    NEO4J_PASSWORD,
    NEO4J_URI,
    NEO4J_USER,
    PROJECT_ROOT,
    RAIL_FIRST_BASE_FARE_USD,
    RAIL_FIRST_PER_STOP_RATE_USD,
    RAIL_STANDARD_BASE_FARE_USD,
    RAIL_STANDARD_PER_STOP_RATE_USD,
)

SEED_CYPHER = PROJECT_ROOT / "databases" / "graph" / "seed.cypher"
METRO_JSON = DATA_DIR / "metro_stations.json"
RAIL_JSON = DATA_DIR / "national_rail_stations.json"


def _load_json(path: Path) -> list[dict]:
    if not path.exists():
        print(f"Error: missing {path}")
        sys.exit(1)
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _run_cypher_file(session, path: Path) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    statements = []
    buffer: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        buffer.append(line)
        if stripped.endswith(";"):
            statements.append("\n".join(buffer).rstrip().rstrip(";"))
            buffer = []
    if buffer:
        statements.append("\n".join(buffer))

    for stmt in statements:
        session.run(stmt)


def seed() -> None:
    metro_stations = _load_json(METRO_JSON)
    rail_stations = _load_json(RAIL_JSON)

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        with driver.session(database=NEO4J_DATABASE) as session:
            print("Clearing existing graph...")
            session.run("MATCH (n) DETACH DELETE n")

            print("Applying schema constraints from seed.cypher...")
            _run_cypher_file(session, SEED_CYPHER)

            print(f"Creating {len(metro_stations)} MetroStation nodes...")
            for s in metro_stations:
                session.run(
                    """
                    MERGE (n:MetroStation {station_id: $id})
                    SET n.name = $name,
                        n.lines = $lines,
                        n.is_interchange_metro = $is_metro_ix,
                        n.interchange_metro_lines = $metro_ix_lines,
                        n.is_interchange_national_rail = $is_nr_ix,
                        n.interchange_nr_station_id = $nr_ix_id,
                        n.base_fare_usd = $base_fare,
                        n.per_stop_rate_usd = $per_stop
                    """,
                    id=s["station_id"],
                    name=s["name"],
                    lines=s["lines"],
                    is_metro_ix=s.get("is_interchange_metro", False),
                    metro_ix_lines=s.get("interchange_metro_lines", []),
                    is_nr_ix=s.get("is_interchange_national_rail", False),
                    nr_ix_id=s.get("interchange_national_rail_station_id"),
                    base_fare=METRO_BASE_FARE_USD,
                    per_stop=METRO_PER_STOP_RATE_USD,
                )

            print(f"Creating {len(rail_stations)} NationalRailStation nodes...")
            for s in rail_stations:
                session.run(
                    """
                    MERGE (n:NationalRailStation {station_id: $id})
                    SET n.name = $name,
                        n.lines = $lines,
                        n.is_interchange_national_rail = $is_nr_ix,
                        n.interchange_national_rail_lines = $nr_ix_lines,
                        n.is_interchange_metro = $is_metro_ix,
                        n.interchange_metro_station_id = $metro_ix_id,
                        n.base_fare_standard_usd = $base_std,
                        n.per_stop_rate_standard_usd = $per_stop_std,
                        n.base_fare_first_usd = $base_first,
                        n.per_stop_rate_first_usd = $per_stop_first
                    """,
                    id=s["station_id"],
                    name=s["name"],
                    lines=s["lines"],
                    is_nr_ix=s.get("is_interchange_national_rail", False),
                    nr_ix_lines=s.get("interchange_national_rail_lines", []),
                    is_metro_ix=s.get("is_interchange_metro", False),
                    metro_ix_id=s.get("interchange_metro_station_id"),
                    base_std=RAIL_STANDARD_BASE_FARE_USD,
                    per_stop_std=RAIL_STANDARD_PER_STOP_RATE_USD,
                    base_first=RAIL_FIRST_BASE_FARE_USD,
                    per_stop_first=RAIL_FIRST_PER_STOP_RATE_USD,
                )

            metro_links = 0
            for s in metro_stations:
                for adj in s.get("adjacent_stations", []):
                    session.run(
                        """
                        MATCH (a:MetroStation {station_id: $from_id})
                        MATCH (b:MetroStation {station_id: $to_id})
                        MERGE (a)-[r:METRO_LINK {line: $line}]->(b)
                        SET r.travel_time_min = $time,
                            r.time_weight = $time,
                            r.fare_weight = $fare_weight
                        """,
                        from_id=s["station_id"],
                        to_id=adj["station_id"],
                        line=adj["line"],
                        time=adj["travel_time_min"],
                        fare_weight=METRO_PER_STOP_RATE_USD,
                    )
                    metro_links += 1
            print(f"  METRO_LINK relationships: {metro_links}")

            rail_links = 0
            for s in rail_stations:
                for adj in s.get("adjacent_stations", []):
                    session.run(
                        """
                        MATCH (a:NationalRailStation {station_id: $from_id})
                        MATCH (b:NationalRailStation {station_id: $to_id})
                        MERGE (a)-[r:RAIL_LINK {line: $line}]->(b)
                        SET r.travel_time_min = $time,
                            r.time_weight = $time,
                            r.fare_weight = $fare_weight
                        """,
                        from_id=s["station_id"],
                        to_id=adj["station_id"],
                        line=adj["line"],
                        time=adj["travel_time_min"],
                        fare_weight=RAIL_STANDARD_PER_STOP_RATE_USD,
                    )
                    rail_links += 1
            print(f"  RAIL_LINK relationships: {rail_links}")

            interchange_pairs: set[tuple[str, str]] = set()
            for s in metro_stations:
                if s.get("is_interchange_national_rail") and s.get(
                    "interchange_national_rail_station_id"
                ):
                    interchange_pairs.add(
                        (s["station_id"], s["interchange_national_rail_station_id"])
                    )
            for s in rail_stations:
                if s.get("is_interchange_metro") and s.get("interchange_metro_station_id"):
                    interchange_pairs.add(
                        (s["interchange_metro_station_id"], s["station_id"])
                    )

            ix_count = 0
            walk = INTERCHANGE_WALKING_TIME_MIN
            for metro_id, rail_id in interchange_pairs:
                session.run(
                    """
                    MATCH (m:MetroStation {station_id: $metro_id})
                    MATCH (r:NationalRailStation {station_id: $rail_id})
                    MERGE (m)-[fwd:INTERCHANGE_TO]->(r)
                    SET fwd.walking_time_min = $walk,
                        fwd.time_weight = $walk,
                        fwd.fare_weight = 0
                    MERGE (r)-[rev:INTERCHANGE_TO]->(m)
                    SET rev.walking_time_min = $walk,
                        rev.time_weight = $walk,
                        rev.fare_weight = 0
                    """,
                    metro_id=metro_id,
                    rail_id=rail_id,
                    walk=walk,
                )
                ix_count += 1
            print(f"  INTERCHANGE_TO pairs: {ix_count}")

            counts = session.run(
                """
                MATCH (m:MetroStation) WITH count(m) AS metro
                MATCH (r:NationalRailStation) WITH metro, count(r) AS rail
                MATCH ()-[l:METRO_LINK]->() WITH metro, rail, count(l) AS metro_links
                MATCH ()-[n:RAIL_LINK]->() WITH metro, rail, metro_links, count(n) AS rail_links
                MATCH ()-[i:INTERCHANGE_TO]->() WITH metro, rail, metro_links, rail_links, count(i) AS ix
                RETURN metro, rail, metro_links, rail_links, ix
                """
            ).single()
            print(
                f"Done — MetroStation: {counts['metro']}, "
                f"NationalRailStation: {counts['rail']}, "
                f"METRO_LINK: {counts['metro_links']}, "
                f"RAIL_LINK: {counts['rail_links']}, "
                f"INTERCHANGE_TO: {counts['ix']}"
            )
    finally:
        driver.close()


if __name__ == "__main__":
    seed()
