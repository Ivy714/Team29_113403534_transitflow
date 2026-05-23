// Deprecated: seeding is now done via skeleton/seed_neo4j.py
// which loads data directly from train-mock-data/ JSON files.
//
// If you prefer Cypher-file seeding, implement your graph schema here.
// Run with: python skeleton/seed_neo4j.py (or via the Neo4j Browser)

// =========================================================================
// TransitFlow Graph Schema Design (For Reference)
// =========================================================================
// Nodes:
//   (:MetroStation {station_id, name, lines})
//   (:NationalRailStation {station_id, name, lines})
//
// Relationships:
//   (:MetroStation)-[:METRO_LINK {line, travel_time_min}]->(:MetroStation)
//   (:NationalRailStation)-[:RAIL_LINK {line, travel_time_min}]->(:NationalRailStation)
//   (:NationalRailStation)-[:INTERCHANGE_TO]->(:MetroStation)
