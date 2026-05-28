# AI Session Context — TransitFlow (Team 113403504)

## Project Overview

TransitFlow queries PostgreSQL (relational + pgvector), Neo4j (graph), and an LLM chat layer in `skeleton/agent.py`.

## Agreed Relational Schema (summary)

- **Stations:** `metro_stations`, `national_rail_stations` (+ line junction tables)
- **Schedules:** `metro_schedules`, `metro_schedule_stops`, `national_rail_schedules`, `national_rail_schedule_stops`, `national_rail_schedule_fares`
- **Seating:** `seat_layouts`, `coaches`, `seats`
- **Users / auth:** `users` (email + salted password + secret Q&A)
- **Transactions:** `journeys`, `bookings`, `metro_trips`, `payments`
- **Vector RAG:** `policy_documents` (chunk_id, metadata JSONB, embedding vector(768))

National rail availability uses `stop_order`: origin must be before destination on the same schedule.

## Agreed Graph Schema

```
Node labels:
- MetroStation {station_id, name, ...}
- NationalRailStation {station_id, name, ...}

Relationship types:
- METRO_LINK {line, travel_time_min, fare weights}
- RAIL_LINK {line, travel_time_min, fare weights}
- INTERCHANGE_TO {walking_time_min}  (metro ↔ national rail, zero fare weight)

Topology loaded by skeleton/seed_neo4j.py from train-mock-data/*.json
Constraints in databases/graph/seed.cypher
```

## Function Signatures (implemented)

See `databases/relational/queries.py` and `databases/graph/queries.py`.

**Task 4 extension:** `query_schedule_seat_occupancy(schedule_id, travel_date, fare_class)` — wired in `agent.py` for questions like “How many seats on NR_SCH01 on 2026-06-15?”

**Policy RAG:** 50 chunks in `train-mock-data/policy_chunks.json`, seeded via `python3 skeleton/seed_vectors.py`.

## Team Decisions Log

- [x] National rail direction: filter with `o_stop.stop_order < d_stop.stop_order` (do not list reverse-direction trains).
- [x] Express pass-through stations: `is_stopping = FALSE`, high `stop_order` (999+) in `national_rail_schedule_stops`.
- [x] Policy RAG: pre-chunked `policy_chunks.json` (from teammate branch 113403501), not raw JSON at query time.
- [x] Agent: rule-based handlers first; LLM fallback only when no DB match.
- [x] Cross-network routing: `query_interchange_path` for “how do I get” MS↔NR; `query_shortest_route` for same-network / fare.

## Prompts That Worked

### Query implementation
```
Implement query_national_rail_availability using _connect() and RealDictCursor.
Require origin stop_order < destination stop_order on national_rail_schedule_stops.
```

### Policy seeding
```
After editing policy_chunks.json, run: python3 skeleton/seed_vectors.py
(Ensure Ollama is running: ollama pull nomic-embed-text)
```
