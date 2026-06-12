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
-- TransitFlow — Final PostgreSQL Schema
-- Merged from both versions, best of each
-- =============================================================
-- Excluded by design:
--   1. Graph routing / adjacency / closures → Neo4j
--   2. Policy text / semantic Q&A → pgvector
-- =============================================================

BEGIN;

-- =============================================================
-- ENUMS
-- =============================================================

CREATE TYPE network_type    AS ENUM ('metro', 'national_rail');
CREATE TYPE service_type    AS ENUM ('normal', 'express');
CREATE TYPE fare_class AS ENUM ('standard', 'first');
CREATE TYPE ticket_type AS ENUM ('single', 'return', 'day_pass');
CREATE TYPE journey_status  AS ENUM ('confirmed', 'completed', 'cancelled');
CREATE TYPE payment_method  AS ENUM ('credit_card', 'debit_card', 'ewallet');
CREATE TYPE payment_status  AS ENUM ('paid', 'refunded', 'failed', 'pending');
CREATE TYPE day_of_week     AS ENUM ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun');

-- =============================================================
-- 1. USERS
-- PK choice: VARCHAR(10) user_id matches mock JSON (RU01, RU02) for stable
-- cross-layer references with Neo4j and the agent; SERIAL would require a
-- separate business key column.
-- Delete strategy: soft delete — journeys/bookings keep rows; status='cancelled'
-- and bookings.seat_occupies_slot=FALSE release seats without physical DELETE.
-- =============================================================

CREATE TABLE users (
    user_id        VARCHAR(10)  PRIMARY KEY,  -- business key from registered_users.json
    first_name     VARCHAR(100) NOT NULL,
    last_name      VARCHAR(100) NOT NULL,
    email          VARCHAR(255) NOT NULL UNIQUE,
    phone          VARCHAR(30),
    date_of_birth  DATE,
    registered_at  TIMESTAMPTZ  NOT NULL,
    is_active      BOOLEAN      NOT NULL DEFAULT TRUE
);

-- 密碼獨立存放，BYTEA salt
CREATE TABLE user_credentials (
    user_id         VARCHAR(10) PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    password_hash   TEXT        NOT NULL,
    password_salt   BYTEA       NOT NULL,
    hash_algorithm  TEXT        NOT NULL DEFAULT 'argon2id',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (hash_algorithm = 'argon2id')
);

-- 安全問題獨立存放
CREATE TABLE user_security_questions (
    security_question_id  VARCHAR(10) PRIMARY KEY,
    user_id               VARCHAR(10) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    secret_question       VARCHAR(255) NOT NULL,
    secret_answer_hash    TEXT         NOT NULL,
    secret_answer_salt    BYTEA        NOT NULL,
    hash_algorithm        TEXT         NOT NULL DEFAULT 'argon2id',
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- =============================================================
-- 2. STATIONS
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

-- 互相參照，用 INITIALLY DEFERRED
ALTER TABLE metro_stations
    ADD CONSTRAINT fk_metro_interchange_nr
    FOREIGN KEY (interchange_national_rail_station_id)
    REFERENCES national_rail_stations(station_id)
    DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE national_rail_stations
    ADD CONSTRAINT fk_nr_interchange_metro
    FOREIGN KEY (interchange_metro_station_id)
    REFERENCES metro_stations(station_id)
    DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE metro_stations
    ADD CONSTRAINT chk_metro_interchange_nr_consistent
    CHECK (
        (is_interchange_national_rail = FALSE AND interchange_national_rail_station_id IS NULL)
        OR is_interchange_national_rail = TRUE
    );

ALTER TABLE national_rail_stations
    ADD CONSTRAINT chk_nr_interchange_metro_consistent
    CHECK (
        (is_interchange_metro = FALSE AND interchange_metro_station_id IS NULL)
        OR is_interchange_metro = TRUE
    );


-- =============================================================
-- 3. STATION LINE MEMBERSHIP
-- =============================================================

CREATE TABLE metro_station_lines (
    station_id  VARCHAR(10) NOT NULL REFERENCES metro_stations(station_id) ON DELETE CASCADE,
    line        VARCHAR(5)  NOT NULL CHECK (line IN ('M1', 'M2', 'M3', 'M4')),
    PRIMARY KEY (station_id, line)
);

CREATE TABLE national_rail_station_lines (
    station_id  VARCHAR(10) NOT NULL REFERENCES national_rail_stations(station_id) ON DELETE CASCADE,
    line        VARCHAR(5)  NOT NULL CHECK (line IN ('NR1', 'NR2')),
    PRIMARY KEY (station_id, line)
);

-- =============================================================
-- 4. SCHEDULES
-- =============================================================

CREATE TABLE metro_schedules (
    schedule_id             VARCHAR(20)   PRIMARY KEY,
    line                    VARCHAR(5)    NOT NULL CHECK (line IN ('M1', 'M2', 'M3', 'M4')),
    direction               VARCHAR(20)   NOT NULL,
    origin_station_id       VARCHAR(10)   NOT NULL REFERENCES metro_stations(station_id),
    destination_station_id  VARCHAR(10)   NOT NULL REFERENCES metro_stations(station_id),
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
    origin_station_id       VARCHAR(10)   NOT NULL REFERENCES national_rail_stations(station_id),
    destination_station_id  VARCHAR(10)   NOT NULL REFERENCES national_rail_stations(station_id),
    first_train_time        TIME          NOT NULL,
    last_train_time         TIME          NOT NULL,
    frequency_min           INTEGER       NOT NULL CHECK (frequency_min > 0),
    CHECK (last_train_time > first_train_time),
    CHECK (origin_station_id <> destination_station_id)
);

-- 票價獨立成 table
CREATE TABLE national_rail_schedule_fares (
    schedule_id        VARCHAR(20)    NOT NULL REFERENCES national_rail_schedules(schedule_id) ON DELETE CASCADE,
    fare_class         fare_class NOT NULL,
    base_fare_usd      NUMERIC(8,2)   NOT NULL CHECK (base_fare_usd >= 0),
    per_stop_rate_usd  NUMERIC(8,2)   NOT NULL CHECK (per_stop_rate_usd >= 0),
    PRIMARY KEY (schedule_id, fare_class)
);

-- =============================================================
-- 5. SCHEDULE STOPS
-- =============================================================

CREATE TABLE metro_schedule_stops (
    schedule_id                   VARCHAR(20)  NOT NULL REFERENCES metro_schedules(schedule_id) ON DELETE CASCADE,
    station_id                    VARCHAR(10)  NOT NULL REFERENCES metro_stations(station_id),
    stop_order                    INTEGER      NOT NULL CHECK (stop_order > 0),
    travel_time_from_origin_min   INTEGER      NOT NULL CHECK (travel_time_from_origin_min >= -1),
    PRIMARY KEY (schedule_id, stop_order),
    UNIQUE (schedule_id, station_id)
);

CREATE TABLE national_rail_schedule_stops (
    schedule_id                   VARCHAR(20)  NOT NULL REFERENCES national_rail_schedules(schedule_id) ON DELETE CASCADE,
    station_id                    VARCHAR(10)  NOT NULL REFERENCES national_rail_stations(station_id),
    stop_order                    INTEGER      NOT NULL CHECK (stop_order > 0),
    travel_time_from_origin_min   INTEGER      NOT NULL CHECK (travel_time_from_origin_min >= -1),
    is_stopping                   BOOLEAN      NOT NULL DEFAULT TRUE,
    PRIMARY KEY (schedule_id, stop_order),
    UNIQUE (schedule_id, station_id)
);

-- =============================================================
-- 6. SCHEDULE OPERATING DAYS（複合 PK）
-- =============================================================

CREATE TABLE metro_schedule_operates_on (
    schedule_id  VARCHAR(20)  NOT NULL REFERENCES metro_schedules(schedule_id) ON DELETE CASCADE,
    day_of_week  day_of_week  NOT NULL,
    PRIMARY KEY (schedule_id, day_of_week)
);

CREATE TABLE national_rail_schedule_operates_on (
    schedule_id  VARCHAR(20)  NOT NULL REFERENCES national_rail_schedules(schedule_id) ON DELETE CASCADE,
    day_of_week  day_of_week  NOT NULL,
    PRIMARY KEY (schedule_id, day_of_week)
);

-- =============================================================
-- 7. SEAT INVENTORY
-- =============================================================

CREATE TABLE seat_layouts (
    layout_id    VARCHAR(10) PRIMARY KEY,
    schedule_id  VARCHAR(20) NOT NULL UNIQUE REFERENCES national_rail_schedules(schedule_id) ON DELETE CASCADE
);

CREATE TABLE coaches (
    layout_id   VARCHAR(10)     NOT NULL REFERENCES seat_layouts(layout_id) ON DELETE CASCADE,
    coach       VARCHAR(5)      NOT NULL,
    fare_class  fare_class NOT NULL,
    PRIMARY KEY (layout_id, coach)
);

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
-- =============================================================

CREATE TABLE ticket_types (
    ticket_type   ticket_type PRIMARY KEY,
    display_name  TEXT NOT NULL,
    description   TEXT
);

CREATE TABLE ticket_type_networks (
    ticket_type  ticket_type NOT NULL REFERENCES ticket_types(ticket_type) ON DELETE CASCADE,
    network      network_type    NOT NULL,
    PRIMARY KEY (ticket_type, network)
);

-- =============================================================
-- 9. JOURNEYS supertype（解決 polymorphic FK 問題）
-- payments 和 feedback 都指向這裡，不用再猜 BK* 還是 MT*
-- =============================================================

CREATE TABLE journeys (
    journey_id   VARCHAR(20)     PRIMARY KEY,
    network      network_type    NOT NULL,
    user_id      VARCHAR(10)     NOT NULL REFERENCES users(user_id),
    ticket_type  ticket_type NOT NULL REFERENCES ticket_types(ticket_type),
    amount_usd   NUMERIC(8,2)    NOT NULL CHECK (amount_usd >= 0),
    status       journey_status  NOT NULL,
    CHECK (
        (network = 'metro'        AND journey_id LIKE 'MT%') OR
        (network = 'national_rail' AND journey_id LIKE 'BK%')
    )
);

-- =============================================================
-- 10. BOOKINGS（國鐵，繼承 journeys）
-- =============================================================

CREATE TABLE bookings (
    booking_id              VARCHAR(20)     PRIMARY KEY REFERENCES journeys(journey_id) ON DELETE CASCADE,
    schedule_id             VARCHAR(20)     NOT NULL REFERENCES national_rail_schedules(schedule_id),
    origin_station_id       VARCHAR(10)     NOT NULL REFERENCES national_rail_stations(station_id),
    destination_station_id  VARCHAR(10)     NOT NULL REFERENCES national_rail_stations(station_id),
    travel_date             DATE            NOT NULL,
    departure_time          TIME            NOT NULL,
    fare_class              fare_class NOT NULL,
    layout_id               VARCHAR(10)     NOT NULL,
    coach                   VARCHAR(5)      NOT NULL,
    seat_id                 VARCHAR(5)      NOT NULL,
    stops_travelled         INTEGER         NOT NULL CHECK (stops_travelled > 0),
    booked_at               TIMESTAMPTZ     NOT NULL,
    travelled_at            TIMESTAMPTZ,
    seat_occupies_slot      BOOLEAN         NOT NULL DEFAULT TRUE,
    FOREIGN KEY (layout_id, coach, seat_id) REFERENCES seats(layout_id, coach, seat_id),
    CHECK (origin_station_id <> destination_station_id),
    CHECK (travelled_at IS NULL OR travelled_at::date >= travel_date)
);

-- =============================================================
-- 11. METRO TRIPS（捷運，繼承 journeys）
-- =============================================================

CREATE TABLE metro_trips (
    trip_id                 VARCHAR(20)  PRIMARY KEY REFERENCES journeys(journey_id) ON DELETE CASCADE,
    schedule_id             VARCHAR(20)  NOT NULL REFERENCES metro_schedules(schedule_id),
    origin_station_id       VARCHAR(10)  NOT NULL REFERENCES metro_stations(station_id),
    destination_station_id  VARCHAR(10)  NOT NULL REFERENCES metro_stations(station_id),
    travel_date             DATE         NOT NULL,
    day_pass_ref            VARCHAR(20) REFERENCES journeys(journey_id) ON DELETE SET NULL,
    stops_travelled         INTEGER      CHECK (stops_travelled IS NULL OR stops_travelled > 0),
    purchased_at            TIMESTAMPTZ  NOT NULL,
    travelled_at            TIMESTAMPTZ,
    CHECK (origin_station_id <> destination_station_id),
    CHECK (travelled_at IS NULL OR travelled_at::date >= travel_date)
);

-- =============================================================
-- 12. PAYMENTS（指向 journeys，真正的 FK）
-- =============================================================

CREATE TABLE payments (
    payment_id  VARCHAR(20)     PRIMARY KEY,
    journey_id  VARCHAR(20)     NOT NULL REFERENCES journeys(journey_id),
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
-- 13. FEEDBACK（指向 journeys，真正的 FK）
-- =============================================================

CREATE TABLE feedback (
    feedback_id   VARCHAR(20)  PRIMARY KEY,
    journey_id    VARCHAR(20)  NOT NULL REFERENCES journeys(journey_id),
    user_id       VARCHAR(10)  NOT NULL REFERENCES users(user_id),
    rating        INTEGER      NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment       TEXT,
    submitted_at  TIMESTAMPTZ  NOT NULL,
    UNIQUE (journey_id, user_id)
);

-- 視圖 1: 自動過濾停權帳號
CREATE VIEW active_users AS
    SELECT * FROM users WHERE is_active = TRUE;

-- 視圖 2: 即時國鐵空位狀態查詢表
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

-- 視圖 3: 階梯票價預先運算表
CREATE VIEW schedule_fare_summary AS
    SELECT
        f.schedule_id,
        f.fare_class,
        generate_series(1, 50) AS stops,
        f.base_fare_usd + (f.per_stop_rate_usd * generate_series(1, 50)) AS total_fare_usd
    FROM national_rail_schedule_fares f;

-- =============================================================
-- INDEXES
-- =============================================================

CREATE INDEX idx_journeys_user              ON journeys(user_id);
CREATE INDEX idx_journeys_status            ON journeys(status);
CREATE INDEX idx_bookings_schedule_date     ON bookings(schedule_id, travel_date);
CREATE INDEX idx_bookings_origin_dest       ON bookings(origin_station_id, destination_station_id, travel_date);
-- Only active seat holds block rebooking; cancelled bookings release the slot (seat_occupies_slot = FALSE).
CREATE UNIQUE INDEX idx_bookings_active_seat_unique
    ON bookings (schedule_id, travel_date, departure_time, coach, seat_id)
    WHERE seat_occupies_slot = TRUE;
CREATE INDEX idx_metro_trips_schedule_date  ON metro_trips(schedule_id, travel_date);
CREATE INDEX idx_metro_trips_origin_dest    ON metro_trips(origin_station_id, destination_station_id, travel_date);
CREATE INDEX idx_payments_journey           ON payments(journey_id);
CREATE INDEX idx_nr_stops_station  ON national_rail_schedule_stops(station_id);
CREATE INDEX idx_metro_stops_station ON metro_schedule_stops(station_id);
CREATE INDEX idx_feedback_journey           ON feedback(journey_id);
CREATE INDEX idx_feedback_user              ON feedback(user_id);
CREATE INDEX idx_metro_trips_day_pass       ON metro_trips(day_pass_ref) WHERE day_pass_ref IS NOT NULL;
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
    -- 768-dim  → Ollama nomic-embed-text (default)
    -- 3072-dim → Gemini gemini-embedding-001
    embedding       vector(768),
    source_file     VARCHAR(200),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_policy_embedding
    ON policy_documents USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_policy_metadata
    ON policy_documents USING GIN (metadata);

COMMIT;