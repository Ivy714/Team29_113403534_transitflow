"""
TransitFlow — Neo4j Seeder
Run once after starting Docker:
    python skeleton/seed_neo4j.py

Loads station and network data from train-mock-data/:
  - metro_stations.json         — city metro stations and adjacencies
  - national_rail_stations.json — national rail stations and adjacencies

Design your graph schema (node labels, relationship types, properties)
based on the data in these files, then implement the seed() function below.
"""
import json
import os
import sys

sys.path.insert(0, ".")

from neo4j import GraphDatabase
from skeleton.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD

_DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "train-mock-data")
)


def _load(filename):
    with open(os.path.join(_DATA_DIR, filename), encoding="utf-8") as f:
        return json.load(f)


def seed():
    metro_stations = _load("metro_stations.json")
    rail_stations  = _load("national_rail_stations.json")

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    with driver.session() as session:

        # 1. 先清空既有資料
        session.run("MATCH (n) DETACH DELETE n")
        print("  Cleared existing graph data")

        # 2. 建立 捷運站 節點
        print("  Creating MetroStation nodes...")
        for station in metro_stations:
            session.run(
                """
                CREATE (s:MetroStation {
                    station_id: $station_id,
                    name: $name,
                    lines: $lines,
                    type: 'Metro'
                })
                """,
                station_id=station["station_id"],
                name=station["name"],
                lines=station["lines"]
            )

        # 3. 建立 國鐵站 節點
        print("  Creating NationalRailStation nodes...")
        for station in rail_stations:
            session.run(
                """
                CREATE (s:NationalRailStation {
                    station_id: $station_id,
                    name: $name,
                    lines: $lines,
                    type: 'Rail'
                })
                """,
                station_id=station["station_id"],
                name=station["name"],
                lines=station["lines"]
            )

        # 4. 建立 捷運軌道連線 (METRO_LINK)
        print("  Creating Metro links...")
        for station in metro_stations:
            for adj in station.get("adjacent_stations", []):
                session.run(
                    """
                    MATCH (from:MetroStation {station_id: $from_id})
                    MATCH (to:MetroStation {station_id: $to_id})
                    MERGE (from)-[r:METRO_LINK {line: $line}]->(to)
                    SET r.travel_time_min = $time
                    """,
                    from_id=station["station_id"],
                    to_id=adj["station_id"],
                    line=adj["line"],
                    time=adj["travel_time_min"]
                )

        # 5. 建立 國鐵軌道連線 (RAIL_LINK)
        print("  Creating National Rail links...")
        for station in rail_stations:
            for adj in station.get("adjacent_stations", []):
                session.run(
                    """
                    MATCH (from:NationalRailStation {station_id: $from_id})
                    MATCH (to:NationalRailStation {station_id: $to_id})
                    MERGE (from)-[r:RAIL_LINK {line: $line}]->(to)
                    SET r.travel_time_min = $time
                    """,
                    from_id=station["station_id"],
                    to_id=adj["station_id"],
                    line=adj["line"],
                    time=adj["travel_time_min"]
                )

        # 6. 建立 捷運與國鐵之間的站內轉乘通道 (INTERCHANGE_TO) - 修正布林值錯誤
        print("  Creating Interchange relationships...")
        for station in metro_stations:
            interchange = station.get("is_interchange_national_rail")
            # 安全檢查：確保 interchange 是一個字典(dict)，而不是 bool
            if interchange and isinstance(interchange, dict) and interchange.get("yes"):
                session.run(
                    """
                    MATCH (m:MetroStation {station_id: $metro_id})
                    MATCH (r:NationalRailStation {station_id: $rail_id})
                    MERGE (m)-[i1:INTERCHANGE_TO]->(r)
                    SET i1.walking_time_min = $time
                    MERGE (r)-[i2:INTERCHANGE_TO]->(m)
                    SET i2.walking_time_min = $time
                    """,
                    metro_id=station["station_id"],
                    rail_id=interchange["target_station_id"],
                    time=interchange.get("walking_time_min", 5)
                )

    driver.close()