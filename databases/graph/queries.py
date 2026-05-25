from neo4j import GraphDatabase
from typing import Any, Dict, List
import sys
import os

# 修正路徑以確保能正確匯入 skeleton 資料夾下的 config
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
from skeleton import config as settings

class TransitQueryManager:
    """
    全面升級版 TransitQueryManager：
    支援基礎票價、每站費率、轉乘路徑規劃與 Ripple Effect 漣漪效應分析。
    """
    
    def __init__(self):
        try:
            self.driver = GraphDatabase.driver(settings.NEO4J_URI, auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD))
        except Exception as e:
            print(f"Driver Initialization Failed: {e}")
            self.driver = None

    def close(self):
        if self.driver: self.driver.close()

    def query_shortest_route(self, origin_id: str, destination_id: str) -> Dict[str, Any]:
        """精準路徑規劃，累加時間並解析站點資訊"""
        if not self.driver: return {"error": "Neo4j driver offline."}

        cypher_query = """
        MATCH p = (start {station_id: $origin_id})-[:METRO_LINK|RAIL_LINK|INTERCHANGE_TO*..15]->(end {station_id: $destination_id})
        WITH p, 
             reduce(s = 0, r IN relationships(p) | s + coalesce(r.travel_time_min, r.walking_time_min, 0)) AS total_time
        RETURN p, total_time
        ORDER BY total_time ASC LIMIT 1
        """
        
        with self.driver.session() as session:
            record = session.run(cypher_query, origin_id=origin_id, destination_id=destination_id).single()
            if not record: return {"found": False}
            
            path = record["p"]
            stations_path = [{"station_id": n.get("station_id"), "name": n.get("name")} for n in path.nodes]
            return {"found": True, "total_time_min": record["total_time"], "path": stations_path}

    def query_cheapest_route(self, origin_id: str, destination_id: str) -> Dict[str, Any]:
        """
        全面升級版計價：結合起步價與路段費率。
        邏輯：total = 起點的 base_fare + SUM(segment_cost)
        """
        if not self.driver: return {"error": "Neo4j driver offline."}
        
        cypher_query = """
        MATCH p = (start {station_id: $origin_id})-[:METRO_LINK|RAIL_LINK|INTERCHANGE_TO*..15]->(end {station_id: $destination_id})
        WITH p, start,
             reduce(s = 0, r IN relationships(p) | s + coalesce(r.segment_cost, r.cost, 0)) AS total_segment_cost
        RETURN p, (start.base_fare + total_segment_cost) AS total_cost
        ORDER BY total_cost ASC LIMIT 1
        """
        with self.driver.session() as session:
            record = session.run(cypher_query, origin_id=origin_id, destination_id=destination_id).single()
            if not record: return {"found": False}
            
            # 解析路徑回傳
            path = record["p"]
            stations_path = [{"station_id": n.get("station_id"), "name": n.get("name")} for n in path.nodes]
            return {
                "found": True, 
                "total_cost": round(record["total_cost"], 2), 
                "path": stations_path
            }

    def query_delay_ripple(self, station_id: str, depth: int = 2) -> List[Dict[str, Any]]:
        """分析漣漪效應，返回深度內所有受影響站點"""
        if not self.driver: return []
        
        cypher_query = """
        MATCH (s {station_id: $station_id})-[*1..2]-(affected)
        WHERE affected.station_id <> $station_id
        RETURN DISTINCT affected.station_id AS station_id, affected.name AS name, labels(affected)[0] AS type
        """
        with self.driver.session() as session:
            return [record.data() for record in session.run(cypher_query, station_id=station_id)]

    def get_station_details(self, station_id: str) -> Dict[str, Any]:
        """額外擴充：查詢單一車站所有政策與屬性"""
        with self.driver.session() as session:
            record = session.run("MATCH (s {station_id: $sid}) RETURN s", sid=station_id).single()
            return dict(record["s"]) if record else {}