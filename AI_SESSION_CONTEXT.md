# AI Session Context — TransitFlow

**How to use this file:**
At the start of every AI coding session, paste the full contents of this file as your first message to your AI assistant. This gives the AI the context it needs to produce code that fits your codebase and is consistent with your teammates' work.

**Who maintains this file:**
Whoever makes a schema change or architectural decision updates this file in the same commit. Treat it like a team contract.

---

## Project Overview

TransitFlow is a Python-based AI chat assistant for a fictional transit operator. It queries three databases — PostgreSQL (relational + vector), Neo4j (graph) — and uses an LLM to answer user questions. Our task as students is to design the database schema and implement the query functions in `databases/relational/queries.py` and `databases/graph/queries.py`.

## Tech Stack

- Language: Python 3.11+
- Relational DB: PostgreSQL via `psycopg2` with `RealDictCursor`
- Graph DB: Neo4j via the `neo4j` Python driver
- Vector search: `pgvector` extension (already implemented — do not modify)
- Web UI: Gradio
- LLM: Google Gemini or local Ollama (configured via `.env`)

## Coding Conventions

- **Naming:** `snake_case` for all Python names and SQL identifiers
- **Docstrings:** All functions must have a docstring with `Args:` and `Returns:` sections
- **Return types:** Use type hints. Read-only functions return `list[dict]` or `Optional[dict]`
- **Empty results:** Return `[]` or `None` (as documented), never raise an exception for "not found"
- **SQL:** Use `%s` placeholders for all user inputs — never string-format into SQL
- **Relational pattern:** Use `_connect()` helper + `psycopg2.extras.RealDictCursor`:
  ```python
  with _connect() as conn:
      with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
          cur.execute("SELECT ...", (param,))
          return [dict(row) for row in cur.fetchall()]
  ```
- **Graph pattern:** Use `_driver()` helper + session:
  ```python
  with _driver() as driver:
      with driver.session() as session:
          result = session.run("MATCH ...", station_id=station_id)
          return [dict(record) for record in result]
  ```

## Agreed Relational Schema

<!-- ============================================================
  FILL THIS IN after your team completes the schema design workshop.
  Paste your final CREATE TABLE statements here.
  ============================================================ -->

```sql
-- TODO: paste your final schema.sql contents here after team review
```

## Agreed Graph Schema

<!-- ============================================================
  FILL THIS IN after your team agrees on Neo4j node labels and
  relationship types.
  ============================================================ -->

```
Node labels:
- TODO

Relationship types:
- TODO

Key properties:
- TODO
```

## Function Signatures We Are Implementing

These are fixed contracts. AI-generated code must match these signatures exactly.

### Relational (`databases/relational/queries.py`)

```python
# Read-only
def query_national_rail_availability(origin_id: str, destination_id: str, travel_date: Optional[str] = None) -> list[dict]: ...
def query_national_rail_fare(schedule_id: str, fare_class: str, stops_travelled: int) -> Optional[dict]: ...
def query_metro_schedules(origin_id: str, destination_id: str) -> list[dict]: ...
def query_metro_fare(schedule_id: str, stops_travelled: int) -> Optional[dict]: ...
def query_available_seats(schedule_id: str, travel_date: str, fare_class: str) -> list[dict]: ...
def query_user_profile(user_email: str) -> Optional[dict]: ...
def query_user_bookings(user_email: str) -> dict: ...  # returns {"national_rail": [...], "metro": [...]}
def query_payment_info(booking_id: str) -> Optional[dict]: ...

# Write operations
def execute_booking(user_id, schedule_id, origin_station_id, destination_station_id, travel_date, fare_class, seat_id, ticket_type="single") -> tuple[bool, dict | str]: ...
def execute_cancellation(booking_id: str, user_id: str) -> tuple[bool, dict | str]: ...

# Auth
def register_user(email, first_name, surname, year_of_birth, password, secret_question, secret_answer) -> tuple[bool, str]: ...
def login_user(email: str, password: str) -> Optional[dict]: ...
def get_user_secret_question(email: str) -> Optional[str]: ...
def verify_secret_answer(email: str, answer: str) -> bool: ...
def update_password(email: str, new_password: str) -> bool: ...
```

### Graph (`databases/graph/queries.py`)

```python
def query_shortest_route(origin_id: str, destination_id: str, network: str = "auto") -> dict: ...
def query_cheapest_route(origin_id: str, destination_id: str, network: str = "auto", fare_class: str = "standard") -> dict: ...
def query_alternative_routes(origin_id, destination_id, avoid_station_id, network="auto", max_routes=3) -> list[list[dict]]: ...
def query_interchange_path(origin_id: str, destination_id: str) -> dict: ...
def query_delay_ripple(delayed_station_id: str, hops: int = 2) -> list[dict]: ...
def query_station_connections(station_id: str) -> list[dict]: ...
```

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

### Policy Vector Seeding Prompt
```
After updating policy_chunks.json:

python3 skeleton/seed_vectors.py

Ensure the embedding model is available:

ollama pull nomic-embed-text
```
