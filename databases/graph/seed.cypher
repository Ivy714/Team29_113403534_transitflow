// ==========================================================
// TRANSITFLOW SYSTEM - MASTER SEED SCRIPT
// ==========================================================

// 1. 建立唯一性約束
CREATE CONSTRAINT unique_station_id IF NOT EXISTS FOR (s:Station) REQUIRE s.station_id IS UNIQUE;

// 2. 寫入地鐵網絡節點 (含計價規則)
// Metro: 採計價方式 base_fare + per_stop_rate
MERGE (ms01:Station {station_id: "MS01", name: "Central Square", type: "Metro", lines: ["M1", "M2"], base_fare: 1.5, per_stop_rate: 0.1})
MERGE (ms02:Station {station_id: "MS02", name: "Riverside",      type: "Metro", lines: ["M1"], base_fare: 1.5, per_stop_rate: 0.1})
MERGE (ms03:Station {station_id: "MS03", name: "Northgate",      type: "Metro", lines: ["M1"], base_fare: 1.5, per_stop_rate: 0.1})
MERGE (ms04:Station {station_id: "MS04", name: "Elm Park",       type: "Metro", lines: ["M1", "M3"], base_fare: 1.5, per_stop_rate: 0.1})
MERGE (ms05:Station {station_id: "MS05", name: "Westfield",      type: "Metro", lines: ["M2", "M3"], base_fare: 1.5, per_stop_rate: 0.1})

// 3. 寫入國家鐵路網絡節點 (含票價屬性)
// Rail: 票價規則參考 National Rail 規則
MERGE (nr01:Station {station_id: "NR01", name: "Central Station",   type: "Rail", line: "NR1", base_fare: 5.0})
MERGE (nr02:Station {station_id: "NR02", name: "Maplewood",         type: "Rail", line: "NR1", base_fare: 5.0})
MERGE (nr03:Station {station_id: "NR03", name: "Old Town Junction", type: "Rail", line: "NR1", base_fare: 5.0})
MERGE (nr04:Station {station_id: "NR04", name: "Ashford",           type: "Rail", line: "NR2", base_fare: 5.0})

// 4. 建立地鐵路線關係 (包含行程時間與區段費用)
MERGE (ms01)-[:METRO_LINK {travel_time_min: 3, line: "M1", segment_cost: 0.1}]->(ms02)
MERGE (ms02)-[:METRO_LINK {travel_time_min: 2, line: "M1", segment_cost: 0.1}]->(ms03)
MERGE (ms03)-[:METRO_LINK {travel_time_min: 4, line: "M1", segment_cost: 0.1}]->(ms04)
MERGE (ms01)-[:METRO_LINK {travel_time_min: 5, line: "M2", segment_cost: 0.1}]->(ms05)
MERGE (ms05)-[:METRO_LINK {travel_time_min: 6, line: "M3", segment_cost: 0.1}]->(ms04)

// 5. 建立鐵路路線關係
MERGE (nr01)-[:RAIL_LINK {travel_time_min: 12, line: "NR1", segment_cost: 2.0}]->(nr02)
MERGE (nr02)-[:RAIL_LINK {travel_time_min: 8,  line: "NR1", segment_cost: 2.0}]->(nr03)
MERGE (nr03)-[:RAIL_LINK {travel_time_min: 15, line: "NR2", segment_cost: 2.0}]->(nr04)

// 6. 建立轉乘關係 (INTERCHANGE)
// 轉乘不額外計價，但需消耗步行時間
MERGE (ms01)-[:INTERCHANGE_TO {walking_time_min: 5, cost: 0.0}]->(nr01)
MERGE (nr01)-[:INTERCHANGE_TO {walking_time_min: 5, cost: 0.0}]->(ms01)

// 7. 標記轉乘站點屬性以符合 JSON 規格
// 捷運站對應火車站資訊
SET ms01.is_interchange_national_rail = true
SET ms01.interchange_national_rail_station_id = "NR01"
SET nr01.is_interchange_metro = true
SET nr01.interchange_metro_station_id = "MS01";