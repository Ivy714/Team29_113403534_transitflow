// ==============================================================================
// TransitFlow — Neo4j Schema & Seed Data (配合 A 同學 PostgreSQL 規格版)
// ==============================================================================

// 1. 建立唯一性約束
CREATE CONSTRAINT unique_station_id IF NOT EXISTS
FOR (s:Station) REQUIRE s.station_id IS UNIQUE;

// 2. 寫入地鐵網絡節點 (Metro Stations) - 配合 A 的 M1~M4 規格
MERGE (ms01:Station {station_id: "MS01", name: "Central Square", type: "Metro", lines: ["M1", "M2"]})
MERGE (ms02:Station {station_id: "MS02", name: "Riverside",      type: "Metro", lines: ["M1"]})
MERGE (ms03:Station {station_id: "MS03", name: "Northgate",      type: "Metro", lines: ["M1"]})
MERGE (ms04:Station {station_id: "MS04", name: "Elm Park",       type: "Metro", lines: ["M1", "M3"]})
MERGE (ms05:Station {station_id: "MS05", name: "Westfield",      type: "Metro", lines: ["M2", "M3"]})

// 3. 寫入國家鐵路網絡節點 (National Rail Stations) - 配合 A 的 NR1, NR2 規格
MERGE (nr01:Station {station_id: "NR01", name: "Central Station",   type: "Rail", line: "NR1"})
MERGE (nr02:Station {station_id: "NR02", name: "Maplewood",         type: "Rail", line: "NR1"})
MERGE (nr03:Station {station_id: "NR03", name: "Old Town Junction", type: "Rail", line: "NR1"})
MERGE (nr04:Station {station_id: "NR04", name: "Ashford",           type: "Rail", line: "NR2"})

// 4. 建立地鐵路線關係 (METRO_LINK)
MERGE (ms01)-[:METRO_LINK {travel_time_min: 3, line: "M1"}]->(ms02)
MERGE (ms02)-[:METRO_LINK {travel_time_min: 2, line: "M1"}]->(ms03)
MERGE (ms03)-[:METRO_LINK {travel_time_min: 4, line: "M1"}]->(ms04)
MERGE (ms01)-[:METRO_LINK {travel_time_min: 5, line: "M2"}]->(ms05)
MERGE (ms05)-[:METRO_LINK {travel_time_min: 6, line: "M3"}]->(ms04)

// 5. 建立鐵路路線關係 (RAIL_LINK) - 💡 核心修正：改為 NR1 和 NR2
MERGE (nr01)-[:RAIL_LINK {travel_time_min: 12, line: "NR1"}]->(nr02)
MERGE (nr02)-[:RAIL_LINK {travel_time_min: 8,  line: "NR1"}]->(nr03)
MERGE (nr03)-[:RAIL_LINK {travel_time_min: 15, line: "NR2"}]->(nr04)

// 6. 建立跨網絡轉乘站走路邊 (INTERCHANGE_TO)
MERGE (ms01)-[:INTERCHANGE_TO {walking_time_min: 5}]->(nr01)
MERGE (nr01)-[:INTERCHANGE_TO {walking_time_min: 5}]->(ms01)