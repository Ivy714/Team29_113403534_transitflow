"""
TransitFlow — Neo4j Seeding Script (Full Business Logic Edition)
=============================================================
功能：
1. 自動讀取專案根目錄下的 train-mock-data/metro_stations.json
2. 完整部署站點節點、METRO_LINK 關係
3. 注入商業邏輯：自動將 base_fare, per_stop_rate 與 segment_cost 寫入節點與關係
4. 確保與 queries.py 的邏輯完全串接
"""

import json
import os
import sys
from neo4j import GraphDatabase

def seed_from_json():
    json_path = "train-mock-data/metro_stations.json"
    
    if not os.path.exists(json_path):
        print(f"❌ 錯誤：找不到檔案 {json_path}")
        sys.exit(1)

    with open(json_path, 'r', encoding='utf-8') as f:
        stations = json.load(f)

    uri = os.getenv("NEO4J_URI", "bolt://localhost:7688")
    user = os.getenv("NEO4J_USER", "neo4j")
    pwd = os.getenv("NEO4J_PASSWORD", "transitflow")
    
    driver = GraphDatabase.driver(uri, auth=(user, pwd))

    try:
        with driver.session(database="neo4j") as session:
            print("🧹 正在清理舊資料...")
            session.run("MATCH (n) DETACH DELETE n")
            
            # 1. 建立車站節點 (注入 base_fare 與 per_stop_rate 商業邏輯)
            print(f"🏗️ 正在建立 {len(stations)} 個車站節點...")
            for s in stations:
                session.run("""
                    MERGE (n:Station {station_id: $id})
                    SET n.name = $name, 
                        n.lines = $lines, 
                        n.type = 'Metro',
                        n.is_nr_interchange = $is_nr,
                        n.base_fare = 1.5,
                        n.per_stop_rate = 0.1
                """, id=s['station_id'], 
                     name=s['name'], 
                     lines=s['lines'], 
                     is_nr=s.get('is_interchange_national_rail', False))

            # 2. 建立 METRO_LINK 關係 (注入 segment_cost，與 queries.py 邏輯一致)
            print("🔗 正在建立 METRO_LINK 關係...")
            for s in stations:
                for adj in s['adjacent_stations']:
                    session.run("""
                        MATCH (a:Station {station_id: $id1}), (b:Station {station_id: $id2})
                        MERGE (a)-[:METRO_LINK {
                            line: $line, 
                            travel_time_min: $time,
                            segment_cost: 0.1
                        }]->(b)
                    """, id1=s['station_id'], 
                         id2=adj['station_id'], 
                         line=adj['line'], 
                         time=adj['travel_time_min'])

            # 3. 建立轉乘關係 (保持轉乘站點定義)
            print("🚉 正在建立 INTERCHANGE_TO 轉乘站點...")
            for s in stations:
                if s.get('is_interchange_national_rail'):
                    nr_id = s['interchange_national_rail_station_id']
                    session.run("""
                        MATCH (m:Station {station_id: $m_id})
                        MERGE (nr:Station {station_id: $nr_id})
                        SET nr.type = 'Rail', 
                            nr.name = 'National Rail Station',
                            nr.base_fare = 5.0,
                            nr.is_nr_interchange = true
                        MERGE (m)-[:INTERCHANGE_TO {walking_time_min: 5, cost: 0.0}]->(nr)
                        MERGE (nr)-[:INTERCHANGE_TO {walking_time_min: 5, cost: 0.0}]->(m)
                    """, m_id=s['station_id'], nr_id=nr_id)

        print("✅ 全地圖節點、轉乘邏輯與票價結構已成功部署！")
    
    except Exception as e:
        print(f"❌ 部署過程中發生錯誤: {e}")
    
    finally:
        driver.close()

if __name__ == "__main__":
    seed_from_json()