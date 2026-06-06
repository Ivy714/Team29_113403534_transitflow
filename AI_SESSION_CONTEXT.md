# AI Session Context — TransitFlow (Team 113403504)

**How to use:** Paste this file at the start of AI coding sessions so generated code matches team contracts.

**Who maintains:** Whoever changes schema or architecture updates this file in the same commit.

---

## Project Overview

TransitFlow queries PostgreSQL (relational + pgvector), Neo4j (graph), and a rule-based/LLM agent in `skeleton/agent.py`. Optional Task 6 adds seat-occupancy analytics and UI panels in `skeleton/ui.py`.

## Tech Stack

- Python 3.9+ (3.11 recommended)
- PostgreSQL + pgvector via `psycopg2` + `RealDictCursor`
- Neo4j via `neo4j` Python driver
- Gradio UI
- LLM: Ollama (default) or Gemini via `.env`

## Coding Conventions

- `snake_case` for Python and SQL identifiers
- Docstrings with `Args:` / `Returns:` on public functions
- Read-only queries return `[]` or `None` — never raise for "not found"
- SQL: always `%s` placeholders
- Passwords: Argon2id via `skeleton/password_hash.py`

## Agreed Relational Schema (summary)

- **Stations:** `metro_stations`, `national_rail_stations` (+ line junction tables)
- **Schedules:** `metro_schedules`, `metro_schedule_stops`, `national_rail_schedules`, `national_rail_schedule_stops`, `national_rail_schedule_fares`
- **Seating:** `seat_layouts`, `coaches`, `seats`
- **Users / auth:** `users`, `user_credentials`, `user_security_questions` (Argon2id)
- **Transactions:** `journeys`, `bookings`, `metro_trips`, `payments`, `feedback`
- **Vector RAG:** `policy_documents` (chunk_id, metadata JSONB, embedding vector(768))

National rail availability uses `stop_order`: origin must be before destination on the same schedule.

## Agreed Graph Schema

```
Node labels:
- MetroStation {station_id, name, lines, ...}
- NationalRailStation {station_id, name, lines, ...}

Relationship types:
- METRO_LINK {line, time_weight, fare_weight}
- RAIL_LINK {line, time_weight, fare_weight}
- INTERCHANGE_TO {walking_time_min, time_weight}

Loaded by skeleton/seed_neo4j.py; constraints in databases/graph/seed.cypher
```

## Function Signatures (implemented)

See `databases/relational/queries.py` and `databases/graph/queries.py`.

**Task 4 / Task 6 extension:** `query_schedule_seat_occupancy(schedule_id, travel_date, fare_class)`

**Policy RAG:** 59 chunks in `train-mock-data/policy_chunks.json` (sync with branch `113403501`), seeded via `python3 skeleton/seed_vectors.py`.

## Team Decisions Log

- [x] National rail direction: `o_stop.stop_order < d_stop.stop_order` (no reverse-direction listings).
- [x] Express pass-through: `is_stopping = FALSE`, high `stop_order` (999+) in `national_rail_schedule_stops`.
- [x] Policy RAG: pre-chunked `policy_chunks.json`, not raw JSON at query time.
- [x] Agent: rule-based handlers first; LLM fallback only when no DB match.
- [x] Cross-network routing: `query_interchange_path` for MS↔NR; `query_shortest_route` for same-network.
- [x] PostgreSQL = structured transit/booking/payment; Neo4j = routing; pgvector = policy RAG.
- [x] Soft delete: `journeys.status = cancelled`, `bookings.seat_occupies_slot = FALSE`.
- [x] Alternative routes: `shortestPath` iteration (not `*..25` enumeration) for performance.

## Prompts That Worked

### Relational availability
```
Implement query_national_rail_availability using _connect() and RealDictCursor.
Require origin stop_order < destination stop_order on national_rail_schedule_stops.
```

### Policy seeding
```
After editing policy_chunks.json, run: python3 skeleton/seed_vectors.py
(Ensure Ollama is running: ollama pull nomic-embed-text)
```

### RAG policy chunks (113403501)
```
Generate pgvector-ready policy_chunks.json from booking_rules, refund_policy,
travel_policies, ticket_types — one topic per chunk with chunk_id and metadata.
```
