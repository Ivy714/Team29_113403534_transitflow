# AI Session Context — TransitFlow (Team 29)

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

<!-- Add entries as you make decisions. Format: "Decision: X. Why: Y." -->
- [x] National rail direction: filter with `o_stop.stop_order < d_stop.stop_order` (do not list reverse-direction trains).
- [x] Express pass-through stations: `is_stopping = FALSE`, high `stop_order` (999+) in `national_rail_schedule_stops`.
- [x] - [x] Policy RAG: pre-chunked `policy_chunks.json`, not raw JSON at query time.
- [x] Agent: rule-based handlers first; LLM fallback only when no DB match.
- [x] Cross-network routing: `query_interchange_path` for “how do I get” MS↔NR; `query_shortest_route` for same-network / fare.
- [x] PostgreSQL stores structured transit, booking, payment, feedback, and user data.
- [x] Neo4j is responsible for routing, station connectivity, interchange analysis, and shortest-path queries.
- [x] pgvector policy_documents is used for policy semantic search and RAG retrieval.
- [x] Policy documents are pre-processed into policy_chunks.json before embedding generation and vector storage.
- [x] Policy RAG uses booking_rules.json, refund_policy.json, ticket_types.json, and travel_policies.json as authoritative policy sources.
- [x] Metro and National Rail remain separate transport networks with dedicated schedules, fares, and operating rules.

## Prompts That Worked

<!-- Share prompts that produced good output so teammates can reuse them. -->

### Schema Design Prompt
```
Design a PostgreSQL relational schema for the TransitFlow project based on the train-mock-data JSON files.

Please address:

1. Users, user_credentials, authentication data, and user_security_questions
2. Metro stations and National Rail stations
3. Metro schedules and National Rail schedules
4. Schedule stops and operating days
5. National Rail fare classes
6. National Rail seat layouts, coaches, and seats
7. National Rail bookings and Metro trips
8. How payments and feedback support both journey types
9. Preserve the pgvector policy_documents table
10. Primary keys, foreign keys, constraints, indexes, and views
```

### Relational Query Implementation Prompt
```
Implement query_national_rail_availability using _connect() and RealDictCursor.

Requirements:
- Require origin stop_order < destination stop_order on national_rail_schedule_stops
- Do not return reverse-direction services
```

```
Implement the required functions in databases/relational/queries.py based on AI_SESSION_CONTEXT.md and schema.sql.

Requirements:

1. Function signatures must exactly match AI_SESSION_CONTEXT.md
2. Use the _connect() helper
3. Use psycopg2.extras.RealDictCursor
4. All SQL user inputs must use %s placeholders
5. Read-only functions should return [] or None when no data is found
6. Return values must match the docstrings and agent.py tool-calling requirements
```

### Graph Query Implementation Prompt
```
Implement the required functions in databases/graph/queries.py based on AI_SESSION_CONTEXT.md and seed.cypher.

Requirements:

1. Function signatures must exactly match AI_SESSION_CONTEXT.md
2. Use the _driver() helper and Neo4j sessions
3. Use MetroStation and NationalRailStation as node labels
4. Use METRO_LINK, RAIL_LINK, and INTERCHANGE_TO as relationship types
5. Support shortest routes, cheapest routes, alternative routes, interchange paths, delay ripple analysis, and station connectivity queries
6. Return a clear result object or an empty list when no route exists
```

### RAG Policy Chunk Prompt
```
Generate a pgvector-ready policy_chunks.json from:

- booking_rules.json
- refund_policy.json
- travel_policies.json
- ticket_types.json

Requirements:

1. Each chunk should represent a single policy topic
2. Each chunk must have a unique chunk_id
3. Preserve relevant policy metadata
4. Rewrite content into natural language suitable for semantic search
5. Optimize content for RAG retrieval
6. Output must be compatible with seed_vectors.py
```
