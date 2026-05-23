"""
TransitFlow — Graph Database Queries
===================================
Handles all Neo4j Cypher queries for pathfinding.
Optimized with LRU caching, robust error handling, and ripple-effect analysis.
"""

from typing import Any, Dict, List
from neo4j import GraphDatabase
from functools import lru_cache
import os

# ── Configuration ─────────────────────────────────────────────────────────────
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7688")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "transitflow")

# Initialize Driver
try:
    _driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
except Exception:
    _driver = None

# ── Core Query Functions ──────────────────────────────────────────────────────

@lru_cache(maxsize=128)
def query_shortest_route(origin_id: str, destination_id: str) -> Dict[str, Any]:
    """使用最短路徑演算法計算兩站間時間最優解"""
    if not _driver: return {"error": "Driver uninitialized."}

    rel_pattern = "METRO_LINK|RAIL_LINK|INTERCHANGE_TO*..20"
    
    cypher_query = f"""
    MATCH p = (start {{station_id: $origin_id}})-[:{rel_pattern}]->(end {{station_id: $destination_id}})
    WITH p, 
         reduce(s = 0, r IN relationships(p) | 
            s + coalesce(properties(r)["travel_time_min"], properties(r)["walking_time_min"], 0)
         ) AS total_time
    RETURN p, total_time
    ORDER BY total_time ASC
    LIMIT 1
    """
    try:
        with _driver.session() as session:
            result = session.run(cypher_query, origin_id=origin_id, destination_id=destination_id)
            record = result.single()
            if not record: return {"found": False, "error": "No route found."}
            
            return {
                "found": True,
                "total_time_min": record["total_time"],
                "path": [{"station_id": n.get("station_id"), "name": n.get("name")} for n in record["p"].nodes]
            }
    except Exception as e:
        return {"error": str(e)}

def query_delay_ripple(station_id: str, depth: int = 2) -> List[Dict[str, Any]]:
    """查詢特定車站延誤時，波及影響的鄰近車站"""
    if not _driver: return []
    
    cypher_query = f"""
    MATCH (s:Station {{station_id: $station_id}})-[r*1..{depth}]-(neighbor:Station)
    RETURN DISTINCT neighbor.station_id AS station_id, 
           neighbor.name AS name, 
           count(r) AS connection_strength
    ORDER BY connection_strength DESC
    """
    try:
        with _driver.session() as session:
            result = session.run(cypher_query, station_id=station_id)
            return [record.data() for record in result]
    except Exception:
        return []
