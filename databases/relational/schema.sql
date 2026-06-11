-- ============================================================
--  TransitFlow PostgreSQL Schema
--  Seed data is loaded separately by: python skeleton/seed_postgres.py
--
--  TWO ROLES:
--    1. Relational  → dual-network transit data you design below
--    2. Vector      → policy documents for RAG (provided — do not modify)
-- ============================================================

-- ============================================================
--  STUDENT TASK — Design and create your relational tables here
--
--  Start from the mock data in train-mock-data/:
--    metro_stations.json, national_rail_stations.json
--    metro_schedules.json, national_rail_schedules.json
--    national_rail_seat_layouts.json
--    registered_users.json
--    bookings.json, metro_travel_history.json
--    payments.json, feedback.json
--
--  Think about:
--    - What tables do you need?
--    - What columns and data types?
--    - Which fields are primary keys? Which are foreign keys?
--    - What constraints make sense?
--
--  Apply your schema with:
--    docker-compose down -v && docker-compose up -d
-- ============================================================

-- =============================================================
--  TransitFlow — Final PostgreSQL Schema
--  Merged from both team versions, keeping the best of each.
--
--  Intentionally excluded from this schema (handled elsewhere):
--    1. Graph routing, adjacency, and network closures → Neo4j
--    2. Policy text storage and semantic Q&A search    → pgvector
-- =============================================================

BEGIN;

-- =============================================================
-- ENUMS
--
-- Custom types used throughout the schema to enforce valid values
-- at the database level, avoiding magic strings in application code.
-- =============================================================

-- Which physical rail network a journey or station belongs to.
CREATE TYPE network_type    AS ENUM ('metro', 'national_rail');

-- Whether a national rail service makes all stops or skips intermediate ones.
CREATE TYPE service_type    AS ENUM ('normal', 'express');

-- Seating tier purchased by the passenger.
CREATE TYPE fare_class AS ENUM ('standard', 'first');

-- The kind of ticket product purchased.
-- 'single'   → one trip in one direction
-- 'return'   → outbound + inbound trip pair
-- 'day_pass' → unlimited metro rides within a calendar day
CREATE TYPE ticket_type AS ENUM ('single', 'return', 'day_pass');

-- Lifecycle states for any journey (booking or metro trip).
-- 'confirmed'  → paid and active
-- 'completed'  → journey has been travelled
-- 'cancelled'  → cancelled by user or operator; refund logic applies
CREATE TYPE journey_status  AS ENUM ('confirmed', 'completed', 'cancelled');

-- Accepted payment instruments.
CREATE TYPE payment_method  AS ENUM ('credit_card', 'debit_card', 'ewallet');

-- Lifecycle states for a payment record.
-- 'paid'     → successfully charged
-- 'refunded' → charge reversed after cancellation
-- 'failed'   → payment attempt unsuccessful
-- 'pending'  → awaiting confirmation (e.g. async payment gateway)
CREATE TYPE payment_status  AS ENUM ('paid', 'refunded', 'failed', 'pending');

-- ISO-style abbreviated day names used for schedule operating-day tables.
CREATE TYPE day_of_week     AS ENUM ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun');

-- =============================================================
-- 1. USERS
--
-- Core passenger identity. Credentials and security questions are
-- stored in separate tables so that sensitive hashed data is never
-- accidentally returned by a broad SELECT * on users.
-- =============================================================

CREATE TABLE users (
    user_id        VARCHAR(10)  PRIMARY KEY,
    first_name     VARCHAR(100) NOT NULL,
    last_name      VARCHAR(100) NOT NULL,
    email          VARCHAR(255) NOT NULL UNIQUE,
    phone          VARCHAR(30),
    date_of_birth  DATE,
    registered_at  TIMESTAMPTZ  NOT NULL,
    is_active      BOOLEAN      NOT NULL DEFAULT TRUE
);

-- Stores the hashed password separately from the user profile.
-- Using Argon2id is enforced by the CHECK constraint so that no other
-- algorithm can be accidentally written by application code.
--
-- Password hashing algorithm: Argon2id (argon2-cffi library)
-- Why Argon2id over MD5 / SHA-1 / SHA-256:
--   MD5 and SHA-* are general-purpose hash functions with no cost factor —
--   a GPU can compute billions of them per second, making brute-force trivial.
--   Argon2id is a memory-hard key-derivation function with a tunable cost
--   factor (time and memory), making each guess orders of magnitude slower.
-- How salt is managed:
--   argon2-cffi automatically generates a unique CSPRNG salt per hash and
--   embeds it inside the hash string (PHC format).  Two users with the same
--   password will therefore produce completely different hash strings, which
--   defeats pre-computed rainbow-table lookups.
--   Because the salt is embedded in password_hash, a separate salt column
--   is not required; password_salt is kept as nullable for legacy compatibility.
CREATE TABLE user_credentials (
    user_id         VARCHAR(10) PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    password_hash   TEXT        NOT NULL,
    -- password_salt is nullable: argon2-cffi embeds the salt inside the hash
    -- string (PHC format), so no separate column is needed for new rows.
    -- The column is retained for schema backward compatibility only.
    password_salt   BYTEA,
    hash_algorithm  TEXT        NOT NULL DEFAULT 'argon2id',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (hash_algorithm = 'argon2id')
);

-- Stores a single security question per user for self-service password reset.
-- The answer is hashed with the same Argon2id approach as the password.
-- secret_answer_salt is nullable for the same reason as user_credentials.password_salt:
-- argon2-cffi embeds the salt inside secret_answer_hash (PHC format).
CREATE TABLE user_security_questions (
    security_question_id  VARCHAR(10) PRIMARY KEY,
    user_id               VARCHAR(10) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    secret_question       VARCHAR(255) NOT NULL,
    secret_answer_hash    TEXT         NOT NULL,
    secret_answer_salt    BYTEA,
    hash_algorithm        TEXT         NOT NULL DEFAULT 'argon2id',
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- =============================================================
-- 2. STATIONS
--
-- Metro and national rail stations are stored in separate tables
-- because they belong to different networks with different properties.
-- Interchange columns link the two networks where a physical
-- connection exists (e.g. a station where passengers can walk
-- between the metro platform and the rail platform).
--
-- The two foreign keys reference each other (circular), so they are
-- added via ALTER TABLE with DEFERRABLE INITIALLY DEFERRED, which
-- allows both rows to be inserted in the same transaction before
-- the constraint is checked.
-- =============================================================

CREATE TABLE metro_stations (
    station_id                            VARCHAR(10)  PRIMARY KEY,
    name                                  VARCHAR(100) NOT NULL,
    is_interchange_metro                  BOOLEAN      NOT NULL DEFAULT FALSE,
    is_interchange_national_rail          BOOLEAN      NOT NULL DEFAULT FALSE,
    interchange_national_rail_station_id  VARCHAR(10)
);

CREATE TABLE national_rail_stations (
    station_id                         VARCHAR(10)  PRIMARY KEY,
    name                               VARCHAR(100) NOT NULL,
    is_interchange_national_rail       BOOLEAN      NOT NULL DEFAULT FALSE,
    is_interchange_metro               BOOLEAN      NOT NULL DEFAULT FALSE,
    interchange_metro_station_id       VARCHAR(10)
);

-- Cross-network FK: metro station → national rail station.
-- DEFERRABLE INITIALLY DEFERRED: the FK is checked at COMMIT, not at each INSERT,
-- allowing both tables to be seeded in a single transaction.
ALTER TABLE metro_stations
    ADD CONSTRAINT fk_metro_interchange_nr
    FOREIGN KEY (interchange_national_rail_station_id)
    REFERENCES national_rail_stations(station_id)
    ON DELETE SET NULL
    DEFERRABLE INITIALLY DEFERRED;

-- Cross-network FK: national rail station → metro station.
ALTER TABLE national_rail_stations
    ADD CONSTRAINT fk_nr_interchange_metro
    FOREIGN KEY (interchange_metro_station_id)
    REFERENCES metro_stations(station_id)
    ON DELETE SET NULL
    DEFERRABLE INITIALLY DEFERRED;

-- Ensures that if is_interchange_national_rail is FALSE, the FK column must be NULL.
-- Prevents inconsistent rows where the flag says "no interchange" but an ID is stored.
ALTER TABLE metro_stations
    ADD CONSTRAINT chk_metro_interchange_nr_consistent
    CHECK (
        (is_interchange_national_rail = FALSE AND interchange_national_rail_station_id IS NULL)
        OR is_interchange_national_rail = TRUE
    );

-- Mirror constraint for the national rail side.
ALTER TABLE national_rail_stations
    ADD CONSTRAINT chk_nr_interchange_metro_consistent
    CHECK (
        (is_interchange_metro = FALSE AND interchange_metro_station_id IS NULL)
        OR is_interchange_metro = TRUE
    );


-- =============================================================
-- 3. STATION LINE MEMBERSHIP
--
-- A station can belong to more than one line (e.g. a junction station
-- served by both M1 and M2). This junction table captures that
-- many-to-many relationship rather than storing a single line column
-- on the station row, which would prevent multi-line membership.
-- =============================================================

-- Maps metro stations to the line(s) they are served by.
-- Allowed values are the four metro lines: M1, M2, M3, M4.
CREATE TABLE metro_station_lines (
    station_id  VARCHAR(10) NOT NULL REFERENCES metro_stations(station_id) ON DELETE CASCADE,
    line        VARCHAR(5)  NOT NULL CHECK (line IN ('M1', 'M2', 'M3', 'M4')),
    PRIMARY KEY (station_id, line)
);

-- Maps national rail stations to the line(s) they are served by.
-- Allowed values are the two rail lines: NR1, NR2.
CREATE TABLE national_rail_station_lines (
    station_id  VARCHAR(10) NOT NULL REFERENCES national_rail_stations(station_id) ON DELETE CASCADE,
    line        VARCHAR(5)  NOT NULL CHECK (line IN ('NR1', 'NR2')),
    PRIMARY KEY (station_id, line)
);

-- =============================================================
-- 4. SCHEDULES
--
-- A schedule represents a repeating timetabled service between
-- two terminal stations. It is NOT a single train departure —
-- it defines the pattern (first/last train, frequency) from which
-- individual departure times can be derived.
--
-- Fare rates for national rail are stored in a separate child table
-- (national_rail_schedule_fares) because the rate differs by fare
-- class (standard vs first). Metro uses a single flat rate per
-- schedule stored inline.
-- =============================================================

CREATE TABLE metro_schedules (
    schedule_id             VARCHAR(20)   PRIMARY KEY,
    line                    VARCHAR(5)    NOT NULL CHECK (line IN ('M1', 'M2', 'M3', 'M4')),
    direction               VARCHAR(20)   NOT NULL,
    origin_station_id       VARCHAR(10)   NOT NULL REFERENCES metro_stations(station_id) ON DELETE RESTRICT,
    destination_station_id  VARCHAR(10)   NOT NULL REFERENCES metro_stations(station_id) ON DELETE RESTRICT,
    first_train_time        TIME          NOT NULL,
    last_train_time         TIME          NOT NULL,
    frequency_min           INTEGER       NOT NULL CHECK (frequency_min > 0),
    base_fare_usd           NUMERIC(8,2)  NOT NULL CHECK (base_fare_usd >= 0),
    per_stop_rate_usd       NUMERIC(8,2)  NOT NULL CHECK (per_stop_rate_usd >= 0),
    CHECK (last_train_time > first_train_time),
    CHECK (origin_station_id <> destination_station_id)
);

CREATE TABLE national_rail_schedules (
    schedule_id             VARCHAR(20)   PRIMARY KEY,
    line                    VARCHAR(5)    NOT NULL CHECK (line IN ('NR1', 'NR2')),
    service_type            service_type  NOT NULL,
    direction               VARCHAR(20)   NOT NULL,
    origin_station_id       VARCHAR(10)   NOT NULL REFERENCES national_rail_stations(station_id) ON DELETE RESTRICT,
    destination_station_id  VARCHAR(10)   NOT NULL REFERENCES national_rail_stations(station_id) ON DELETE RESTRICT,
    first_train_time        TIME          NOT NULL,
    last_train_time         TIME          NOT NULL,
    frequency_min           INTEGER       NOT NULL CHECK (frequency_min > 0),
    CHECK (last_train_time > first_train_time),
    CHECK (origin_station_id <> destination_station_id)
);

-- Stores fare rates per (schedule, fare_class) pair.
-- Separating fares from the schedule row allows a single schedule to have
-- different pricing for standard and first class without duplicating
-- the rest of the schedule data.
CREATE TABLE national_rail_schedule_fares (
    schedule_id        VARCHAR(20)    NOT NULL REFERENCES national_rail_schedules(schedule_id) ON DELETE CASCADE,
    fare_class         fare_class NOT NULL,
    base_fare_usd      NUMERIC(8,2)   NOT NULL CHECK (base_fare_usd >= 0),
    per_stop_rate_usd  NUMERIC(8,2)   NOT NULL CHECK (per_stop_rate_usd >= 0),
    PRIMARY KEY (schedule_id, fare_class)
);

-- =============================================================
-- 5. SCHEDULE STOPS
--
-- Lists every station a schedule calls at, in order.
-- stop_order is 1-based (first stop = 1).
-- travel_time_from_origin_min allows the system to derive arrival
-- times at intermediate stations without storing a full timetable
-- for every departure.
--
-- For national rail, is_stopping = FALSE marks pass-through stations
-- on express services where the train does not open its doors.
-- =============================================================

-- Each row is one station call for a metro schedule.
-- UNIQUE (schedule_id, station_id) prevents a station appearing twice on one route.
CREATE TABLE metro_schedule_stops (
    schedule_id                   VARCHAR(20)  NOT NULL REFERENCES metro_schedules(schedule_id) ON DELETE CASCADE,
    station_id                    VARCHAR(10)  NOT NULL REFERENCES metro_stations(station_id) ON DELETE RESTRICT,
    stop_order                    INTEGER      NOT NULL CHECK (stop_order > 0),
    travel_time_from_origin_min   INTEGER      NOT NULL CHECK (travel_time_from_origin_min >= -1),
    PRIMARY KEY (schedule_id, stop_order),
    UNIQUE (schedule_id, station_id)
);

-- Same structure as metro but includes is_stopping for express pass-through logic.
CREATE TABLE national_rail_schedule_stops (
    schedule_id                   VARCHAR(20)  NOT NULL REFERENCES national_rail_schedules(schedule_id) ON DELETE CASCADE,
    station_id                    VARCHAR(10)  NOT NULL REFERENCES national_rail_stations(station_id) ON DELETE RESTRICT,
    stop_order                    INTEGER      NOT NULL CHECK (stop_order > 0),
    travel_time_from_origin_min   INTEGER      NOT NULL CHECK (travel_time_from_origin_min >= -1),
    is_stopping                   BOOLEAN      NOT NULL DEFAULT TRUE,
    PRIMARY KEY (schedule_id, stop_order),
    UNIQUE (schedule_id, station_id)
);

-- =============================================================
-- 6. SCHEDULE OPERATING DAYS
--
-- Stores which days of the week each schedule runs.
-- Using a separate junction table (rather than a boolean column per day
-- on the schedule row) makes it easy to query "all schedules running
-- on Saturday" without scanning seven columns.
-- =============================================================

-- One row per (schedule, day) pair for metro services.
CREATE TABLE metro_schedule_operates_on (
    schedule_id  VARCHAR(20)  NOT NULL REFERENCES metro_schedules(schedule_id) ON DELETE CASCADE,
    day_of_week  day_of_week  NOT NULL,
    PRIMARY KEY (schedule_id, day_of_week)
);

-- Same pattern for national rail services.
CREATE TABLE national_rail_schedule_operates_on (
    schedule_id  VARCHAR(20)  NOT NULL REFERENCES national_rail_schedules(schedule_id) ON DELETE CASCADE,
    day_of_week  day_of_week  NOT NULL,
    PRIMARY KEY (schedule_id, day_of_week)
);

-- =============================================================
-- 7. SEAT INVENTORY
--
-- Physical seat layout for national rail trains only.
-- Metro trips do not assign seats (open seating).
--
-- Hierarchy: seat_layout (one per schedule)
--              └── coaches (carriages within that layout, each with a fare class)
--                    └── seats (individual seats within a coach, identified by row + column)
--
-- The three-column composite FK (layout_id, coach, seat_id) is used in
-- bookings to pin a booking to a specific physical seat.
-- =============================================================

-- One layout per national rail schedule; acts as the parent grouping for coaches.
CREATE TABLE seat_layouts (
    layout_id    VARCHAR(10) PRIMARY KEY,
    schedule_id  VARCHAR(20) NOT NULL UNIQUE REFERENCES national_rail_schedules(schedule_id) ON DELETE CASCADE
);

-- A coach (carriage) within a layout. fare_class determines whether seats
-- in this coach are sold as standard or first-class.
CREATE TABLE coaches (
    layout_id   VARCHAR(10)     NOT NULL REFERENCES seat_layouts(layout_id) ON DELETE CASCADE,
    coach       VARCHAR(5)      NOT NULL,
    fare_class  fare_class NOT NULL,
    PRIMARY KEY (layout_id, coach)
);

-- An individual seat within a coach.
-- seat_row + seat_column (e.g. row 3, column 'A') together identify the physical position.
CREATE TABLE seats (
    layout_id    VARCHAR(10)  NOT NULL,
    coach        VARCHAR(5)   NOT NULL,
    seat_id      VARCHAR(5)   NOT NULL,
    seat_row     INTEGER      NOT NULL CHECK (seat_row > 0),
    seat_column  VARCHAR(2)   NOT NULL,
    PRIMARY KEY (layout_id, coach, seat_id),
    FOREIGN KEY (layout_id, coach) REFERENCES coaches(layout_id, coach) ON DELETE CASCADE
);

-- =============================================================
-- 8. TICKET CATALOGUE
--
-- Defines the ticket products the operator offers and which
-- networks each product is valid on. Keeping this as a table
-- (rather than hardcoding in application logic) makes it easy
-- to add new product types without a schema change.
-- =============================================================

-- One row per ticket product type.
CREATE TABLE ticket_types (
    ticket_type   ticket_type PRIMARY KEY,
    display_name  TEXT NOT NULL,
    description   TEXT
);

-- Maps each ticket type to the network(s) it is valid on.
-- 'single' and 'return' are valid on national rail only.
-- 'day_pass' is valid on metro only.
-- This table makes that per-network validity explicit and queryable.
CREATE TABLE ticket_type_networks (
    ticket_type  ticket_type NOT NULL REFERENCES ticket_types(ticket_type) ON DELETE CASCADE,
    network      network_type    NOT NULL,
    PRIMARY KEY (ticket_type, network)
);

-- =============================================================
-- 9. JOURNEYS — shared supertype table
--
-- Both national rail bookings (BK-*) and metro trips (MT-*) are
-- types of journey. Rather than duplicating payment and feedback
-- foreign keys into each child table (which would require polymorphic
-- FKs or CHECK-guarded nullable columns), we use a single journeys
-- table as the supertype.
--
-- payments and feedback both reference journeys(journey_id), giving
-- them a single clean FK target regardless of which network the
-- journey is on.
--
-- The CHECK constraint enforces the ID prefix convention:
--   metro journeys   → journey_id starts with 'MT'
--   national rail    → journey_id starts with 'BK'
-- This makes it easy to identify the network from the ID alone.
-- =============================================================

CREATE TABLE journeys (
    journey_id   VARCHAR(20)     PRIMARY KEY,
    network      network_type    NOT NULL,
    user_id      VARCHAR(10)     NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    ticket_type  ticket_type NOT NULL REFERENCES ticket_types(ticket_type) ON DELETE RESTRICT,
    amount_usd   NUMERIC(8,2)    NOT NULL CHECK (amount_usd >= 0),
    status       journey_status  NOT NULL,
    CHECK (
        (network = 'metro'        AND journey_id LIKE 'MT%') OR
        (network = 'national_rail' AND journey_id LIKE 'BK%')
    )
);

-- =============================================================
-- 10. BOOKINGS — national rail child table
--
-- Each row is one seat reservation on a national rail service.
-- booking_id is a FK to journeys (not a standalone PK) so that
-- payments and feedback can reference the parent journey without
-- knowing whether it is a booking or a metro trip.
--
-- The three-part FK (layout_id, coach, seat_id) links to the
-- physical seat in the seat inventory tables.
--
-- The UNIQUE constraint on (schedule_id, travel_date, departure_time,
-- coach, seat_id) prevents double-booking the same seat on the same
-- departure.
-- =============================================================

CREATE TABLE bookings (
    booking_id              VARCHAR(20)     PRIMARY KEY REFERENCES journeys(journey_id) ON DELETE CASCADE,
    schedule_id             VARCHAR(20)     NOT NULL REFERENCES national_rail_schedules(schedule_id) ON DELETE RESTRICT,
    origin_station_id       VARCHAR(10)     NOT NULL REFERENCES national_rail_stations(station_id) ON DELETE RESTRICT,
    destination_station_id  VARCHAR(10)     NOT NULL REFERENCES national_rail_stations(station_id) ON DELETE RESTRICT,
    travel_date             DATE            NOT NULL,
    departure_time          TIME            NOT NULL,
    fare_class              fare_class NOT NULL,
    layout_id               VARCHAR(10)     NOT NULL,
    coach                   VARCHAR(5)      NOT NULL,
    seat_id                 VARCHAR(5)      NOT NULL,
    stops_travelled         INTEGER         NOT NULL CHECK (stops_travelled > 0),
    booked_at               TIMESTAMPTZ     NOT NULL,
    travelled_at            TIMESTAMPTZ,
    FOREIGN KEY (layout_id, coach, seat_id) REFERENCES seats(layout_id, coach, seat_id) ON DELETE RESTRICT,
    UNIQUE (schedule_id, travel_date, departure_time, coach, seat_id),
    CHECK (origin_station_id <> destination_station_id),
    CHECK (travelled_at IS NULL OR travelled_at::date >= travel_date)
);

-- =============================================================
-- 11. METRO TRIPS — metro child table
--
-- Each row represents a metro journey taken (or purchased) by a user.
-- Metro does not assign seats, so there is no seat FK here.
--
-- day_pass_ref links a trip to the day pass journey that covers it,
-- allowing the system to validate whether a trip is pre-paid by a pass.
-- It is nullable because single-ticket metro trips have no pass.
--
-- stops_travelled is nullable here (unlike bookings) because for tap-in
-- tap-out systems the destination may not be known at purchase time.
-- =============================================================

CREATE TABLE metro_trips (
    trip_id                 VARCHAR(20)  PRIMARY KEY REFERENCES journeys(journey_id) ON DELETE CASCADE,
    schedule_id             VARCHAR(20)  NOT NULL REFERENCES metro_schedules(schedule_id) ON DELETE RESTRICT,
    origin_station_id       VARCHAR(10)  NOT NULL REFERENCES metro_stations(station_id) ON DELETE RESTRICT,
    destination_station_id  VARCHAR(10)  NOT NULL REFERENCES metro_stations(station_id) ON DELETE RESTRICT,
    travel_date             DATE         NOT NULL,
    day_pass_ref            VARCHAR(20) REFERENCES journeys(journey_id) ON DELETE SET NULL,
    stops_travelled         INTEGER      CHECK (stops_travelled IS NULL OR stops_travelled > 0),
    purchased_at            TIMESTAMPTZ  NOT NULL,
    travelled_at            TIMESTAMPTZ,
    CHECK (origin_station_id <> destination_station_id),
    CHECK (travelled_at IS NULL OR travelled_at::date >= travel_date)
);

-- =============================================================
-- 12. PAYMENTS
--
-- One payment record per journey. References journeys(journey_id)
-- rather than bookings or metro_trips directly, so the FK works
-- for both network types without a polymorphic workaround.
--
-- The partial unique index (idx_payments_one_paid_per_journey below)
-- enforces that at most one payment per journey can be in 'paid' status,
-- preventing accidental double-charging.
--
-- The CHECK constraint requires paid_at to be populated when a payment
-- has actually been processed (status 'paid' or 'refunded'), and
-- allows paid_at to be NULL for terminal failure or pending states.
-- =============================================================

CREATE TABLE payments (
    payment_id  VARCHAR(20)     PRIMARY KEY,
    journey_id  VARCHAR(20)     NOT NULL REFERENCES journeys(journey_id) ON DELETE RESTRICT,
    amount_usd  NUMERIC(8,2)    NOT NULL CHECK (amount_usd >= 0),
    method      payment_method  NOT NULL,
    status      payment_status  NOT NULL,
    paid_at     TIMESTAMPTZ,
    CHECK (
    (status IN ('paid', 'refunded') AND paid_at IS NOT NULL) OR
    (status IN ('failed', 'pending'))
)
);

-- =============================================================
-- 13. FEEDBACK
--
-- Post-journey ratings and comments. One feedback row per
-- (journey, user) pair — enforced by the UNIQUE constraint —
-- so a user cannot submit multiple reviews for the same journey.
-- =============================================================

CREATE TABLE feedback (
    feedback_id   VARCHAR(20)  PRIMARY KEY,
    journey_id    VARCHAR(20)  NOT NULL REFERENCES journeys(journey_id) ON DELETE RESTRICT,
    user_id       VARCHAR(10)  NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    rating        INTEGER      NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment       TEXT,
    submitted_at  TIMESTAMPTZ  NOT NULL,
    UNIQUE (journey_id, user_id)
);

-- =============================================================
-- VIEWS
--
-- Three read-only convenience views used by the application layer.
-- Views are not materialised — they are re-evaluated on every query.
-- =============================================================

-- View 1: active_users
-- Filters out suspended accounts so that application queries do not
-- need to remember to add WHERE is_active = TRUE everywhere.
CREATE VIEW active_users AS
    SELECT * FROM users WHERE is_active = TRUE;

-- View 2: seat_availability
-- Joins seat inventory with bookings to show, for each seat on each
-- schedule, whether it is booked on a given travel date and departure.
-- Used by the booking flow to present available seats to the user.
-- NOTE: a seat is considered available only if no confirmed booking exists;
-- cancelled bookings release the seat (handled via journeys.status in queries).
CREATE VIEW seat_availability AS
    SELECT
        s.layout_id,
        s.coach,
        s.seat_id,
        s.seat_row,
        s.seat_column,
        c.fare_class,
        sl.schedule_id,
        b.travel_date,
        b.departure_time,
        CASE WHEN b.seat_id IS NOT NULL THEN TRUE ELSE FALSE END AS is_booked
    FROM   seat_layouts sl
    JOIN   coaches  c ON c.layout_id  = sl.layout_id
    JOIN   seats    s ON s.layout_id  = c.layout_id AND s.coach = c.coach
    LEFT JOIN bookings b ON
        b.layout_id      = s.layout_id
        AND b.coach      = s.coach
        AND b.seat_id    = s.seat_id
        AND b.schedule_id = sl.schedule_id;

-- View 3: schedule_fare_summary
-- Pre-computes total fares for every (schedule, fare_class, stop_count)
-- combination up to 50 stops, using generate_series.
-- Avoids repeating the base_fare + per_stop_rate * stops arithmetic
-- in every application query.
CREATE VIEW schedule_fare_summary AS
    SELECT
        f.schedule_id,
        f.fare_class,
        generate_series(1, 50) AS stops,
        f.base_fare_usd + (f.per_stop_rate_usd * generate_series(1, 50)) AS total_fare_usd
    FROM national_rail_schedule_fares f;

-- =============================================================
-- INDEXES
--
-- Indexes are added on columns that appear frequently in WHERE
-- clauses or JOIN conditions in the application query layer.
-- The partial unique index on payments prevents double-charging.
-- =============================================================

-- journeys: common filters are by user and by status
CREATE INDEX idx_journeys_user              ON journeys(user_id);
CREATE INDEX idx_journeys_status            ON journeys(status);

-- bookings: seat lookups always filter by schedule + date; origin/dest used in history queries
CREATE INDEX idx_bookings_schedule_date     ON bookings(schedule_id, travel_date);
CREATE INDEX idx_bookings_origin_dest       ON bookings(origin_station_id, destination_station_id, travel_date);

-- metro_trips: same pattern as bookings
CREATE INDEX idx_metro_trips_schedule_date  ON metro_trips(schedule_id, travel_date);
CREATE INDEX idx_metro_trips_origin_dest    ON metro_trips(origin_station_id, destination_station_id, travel_date);

-- payments: looked up by journey when displaying booking details or processing refunds
CREATE INDEX idx_payments_journey           ON payments(journey_id);

-- schedule stops: station_id is used to find which schedules serve a given station
CREATE INDEX idx_nr_stops_station  ON national_rail_schedule_stops(station_id);
CREATE INDEX idx_metro_stops_station ON metro_schedule_stops(station_id);

-- feedback: supports queries like "all reviews for journey X" or "all reviews by user Y"
CREATE INDEX idx_feedback_journey           ON feedback(journey_id);
CREATE INDEX idx_feedback_user              ON feedback(user_id);

-- day_pass_ref: used when validating whether a metro trip is covered by an active pass
CREATE INDEX idx_metro_trips_day_pass       ON metro_trips(day_pass_ref) WHERE day_pass_ref IS NOT NULL;

-- Partial unique index: only one payment per journey may be in 'paid' status at a time.
-- Using a partial index (rather than a table constraint) means failed/pending/refunded
-- rows are not restricted — only the single active paid record is protected.
CREATE UNIQUE INDEX idx_payments_one_paid_per_journey
    ON payments(journey_id)
    WHERE status = 'paid';


-- ============================================================
--  VECTOR SCHEMA  (RAG / Help Desk) — do not modify
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS policy_documents (
    id              SERIAL PRIMARY KEY,
    chunk_id        VARCHAR(150) UNIQUE,
    title           VARCHAR(200) NOT NULL,
    category        VARCHAR(50) NOT NULL,
    document_type   VARCHAR(50),
    policy_id       VARCHAR(100),
    content         TEXT NOT NULL,
    metadata        JSONB,
    embedding       vector(768),
    source_file     VARCHAR(200),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast cosine similarity search
CREATE INDEX IF NOT EXISTS idx_policy_embedding
ON policy_documents
USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_policy_metadata
ON policy_documents
USING GIN (metadata);

COMMIT;