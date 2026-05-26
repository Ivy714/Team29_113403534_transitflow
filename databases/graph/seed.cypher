// TransitFlow — Neo4j graph schema (constraints & reference)
// Full topology is loaded from train-mock-data/*.json by skeleton/seed_neo4j.py
//
// Node labels: MetroStation, NationalRailStation
// Relationships: METRO_LINK, RAIL_LINK, INTERCHANGE_TO
//
// Fare model (ticket_types.json / metro_schedules.json / national_rail_schedules.json):
//   amount = base_fare_usd + (stops_travelled × per_stop_rate_usd)

// ── Constraints ───────────────────────────────────────────────────────────────
CREATE CONSTRAINT metro_station_id_unique IF NOT EXISTS
FOR (s:MetroStation) REQUIRE s.station_id IS UNIQUE;

CREATE CONSTRAINT nr_station_id_unique IF NOT EXISTS
FOR (s:NationalRailStation) REQUIRE s.station_id IS UNIQUE;

// ── Fare defaults (nodes inherit these when seeded from JSON) ─────────────────
// Metro:  base_fare_usd = 0.80,  per_stop_rate_usd = 0.30
// Rail standard: base = 2.50, per_stop = 1.50
// Rail first:    base = 4.00, per_stop = 2.50
//
// INTERCHANGE_TO: walking_time_min = 5, fare_weight = 0 (separate tickets per network)
