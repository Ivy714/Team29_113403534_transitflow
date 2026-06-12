"""
Seed PostgreSQL with all TransitFlow mock data from train-mock-data/.

Usage:
    python skeleton/seed_postgres.py

Run AFTER docker-compose up -d.
Safe to re-run: uses ON CONFLICT DO NOTHING throughout.
"""

import json
import os
import sys

import psycopg2
from psycopg2.extras import execute_values

# argon2-cffi: production-grade Argon2id hashing.
# Each _ph.hash() call generates a fresh CSPRNG salt automatically and embeds
# it in the returned PHC-format string — no separate salt column required.
#from argon2 import PasswordHasher
# ── resolve paths ─────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(PROJECT_DIR, "train-mock-data")

sys.path.insert(0, PROJECT_DIR)
from skeleton import config as cfg
from skeleton.password_hash import hash_password

#_ph = PasswordHasher()


"""def _hash(plaintext: str) -> str:

    Hash plaintext with Argon2id via argon2-cffi.
    The returned PHC string is self-contained and safe to store directly in
    password_hash / secret_answer_hash — no extra salt column needed.

    return _ph.hash(plaintext)
"""

def load(filename):
    with open(os.path.join(DATA_DIR, filename), encoding="utf-8") as f:
        return json.load(f)


def connect():
    return psycopg2.connect(
        host=cfg.PG_HOST,
        port=cfg.PG_PORT,
        dbname=cfg.PG_DB,
        user=cfg.PG_USER,
        password=cfg.PG_PASSWORD,
    )


def insert_many(cur, table, columns, rows):
    """Bulk insert with ON CONFLICT DO NOTHING."""
    if not rows:
        return 0
    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES %s ON CONFLICT DO NOTHING"
    execute_values(cur, sql, rows)
    return cur.rowcount


def upsert_many(cur, table, columns, rows, conflict_target, update_columns):
    """Bulk upsert — refreshes rows when re-seeding after schema/hash changes."""
    if not rows:
        return 0
    sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_columns)
    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES %s "
        f"ON CONFLICT ({conflict_target}) DO UPDATE SET {sets}"
    )
    execute_values(cur, sql, rows)
    return cur.rowcount


# ── layout lookup (built once, used in seed_national_rail_bookings) ───────────
_LAYOUT_LOOKUP: dict[str, str] = {}  # schedule_id → layout_id


def _build_layout_lookup():
    for item in load("national_rail_seat_layouts.json"):
        _LAYOUT_LOOKUP[item["schedule_id"]] = item["layout_id"]


# ── seeders ───────────────────────────────────────────────────────────────────


def seed_metro_stations(cur):
    data = load("metro_stations.json")

    # Seed the metro_stations parent table
    stations = [
        (
            s["station_id"],
            s["name"],
            s["is_interchange_metro"],
            s["is_interchange_national_rail"],
            s.get("interchange_national_rail_station_id"),  # nullable
        )
        for s in data
    ]
    insert_many(
        cur,
        "metro_stations",
        [
            "station_id",
            "name",
            "is_interchange_metro",
            "is_interchange_national_rail",
            "interchange_national_rail_station_id",
        ],
        stations,
    )

    # Expand the 'lines' array so each station-line pair becomes its own row
    lines = [(s["station_id"], line) for s in data for line in s["lines"]]
    insert_many(cur, "metro_station_lines", ["station_id", "line"], lines)
    print(f"  ✓ metro_stations ({len(stations)}) + metro_station_lines ({len(lines)})")


def seed_national_rail_stations(cur):
    data = load("national_rail_stations.json")

    stations = [
        (
            s["station_id"],
            s["name"],
            s["is_interchange_national_rail"],
            s["is_interchange_metro"],
            s.get("interchange_metro_station_id"),  # nullable
        )
        for s in data
    ]
    insert_many(
        cur,
        "national_rail_stations",
        [
            "station_id",
            "name",
            "is_interchange_national_rail",
            "is_interchange_metro",
            "interchange_metro_station_id",
        ],
        stations,
    )

    lines = [(s["station_id"], line) for s in data for line in s["lines"]]
    insert_many(cur, "national_rail_station_lines", ["station_id", "line"], lines)
    print(
        f"  ✓ national_rail_stations ({len(stations)}) + national_rail_station_lines ({len(lines)})"
    )


def seed_metro_schedules(cur):
    data = load("metro_schedules.json")

    # Seed the metro_schedules parent table
    schedules = [
        (
            s["schedule_id"],
            s["line"],
            s["direction"],
            s["origin_station_id"],
            s["destination_station_id"],
            s["first_train_time"],
            s["last_train_time"],
            s["frequency_min"],
            s["base_fare_usd"],
            s["per_stop_rate_usd"],
        )
        for s in data
    ]
    insert_many(
        cur,
        "metro_schedules",
        [
            "schedule_id",
            "line",
            "direction",
            "origin_station_id",
            "destination_station_id",
            "first_train_time",
            "last_train_time",
            "frequency_min",
            "base_fare_usd",
            "per_stop_rate_usd",
        ],
        schedules,
    )

    # metro_schedule_stops — one row per (schedule, station) in travel order
    # PK = (schedule_id, stop_order); UNIQUE (schedule_id, station_id) prevents duplicates
    # Merge stops_in_order (sequence) with travel_time_from_origin_min (elapsed minutes)
    stops = []
    for s in data:
        for order, station_id in enumerate(s["stops_in_order"], start=1):
            travel_time = s["travel_time_from_origin_min"][station_id]
            stops.append((s["schedule_id"], station_id, order, travel_time))
    insert_many(
        cur,
        "metro_schedule_stops",
        ["schedule_id", "station_id", "stop_order", "travel_time_from_origin_min"],
        stops,
    )


    # Expand the operates_on array into individual (schedule_id, day_of_week) rows
    operates = [(s["schedule_id"], day) for s in data for day in s["operates_on"]]
    insert_many(
        cur, "metro_schedule_operates_on", ["schedule_id", "day_of_week"], operates
    )
    print(
        f"  ✓ metro_schedules ({len(schedules)}) + stops ({len(stops)}) + operates_on ({len(operates)})"
    )


def seed_national_rail_schedules(cur):
    data = load("national_rail_schedules.json")

    # Seed the national_rail_schedules parent table; fares are stored separately in national_rail_schedule_fares
    schedules = [
        (
            s["schedule_id"],
            s["line"],
            s["service_type"],
            s["direction"],
            s["origin_station_id"],
            s["destination_station_id"],
            s["first_train_time"],
            s["last_train_time"],
            s["frequency_min"],
        )
        for s in data
    ]
    insert_many(
        cur,
        "national_rail_schedules",
        [
            "schedule_id",
            "line",
            "service_type",
            "direction",
            "origin_station_id",
            "destination_station_id",
            "first_train_time",
            "last_train_time",
            "frequency_min",
        ],
        schedules,
    )

    # national_rail_schedule_fares — one row per (schedule, fare_class) pair
    # The JSON fare_classes dict has 'standard' and 'first' keys; flatten into two rows each
    fares = []
    for s in data:
        for fare_class, rates in s["fare_classes"].items():
            fares.append(
                (
                    s["schedule_id"],
                    fare_class,
                    rates["base_fare_usd"],
                    rates["per_stop_rate_usd"],
                )
            )
    insert_many(
        cur,
        "national_rail_schedule_fares",
        ["schedule_id", "fare_class", "base_fare_usd", "per_stop_rate_usd"],
        fares,
    )

    # national_rail_schedule_stops — stopping and pass-through stations
    # PK = (schedule_id, stop_order); CHECK enforces stop_order > 0
    # is_stopping = TRUE  → station is in stops_in_order (train opens doors)
    # is_stopping = FALSE → station is in passed_through_stations (express pass-through, doors stay closed)
    # Express pass-through stations have no meaningful stop_order, so we assign placeholder values starting at 999
    # This satisfies CHECK stop_order > 0 while keeping UNIQUE (schedule_id, station_id) conflict-free
    stops = []
    for s in data:
        # Stations where the train actually stops (is_stopping = TRUE)
        for order, station_id in enumerate(s["stops_in_order"], start=1):
            travel_time = s["travel_time_from_origin_min"][station_id]
            stops.append((s["schedule_id"], station_id, order, travel_time, True))

        # Pass-through stations for express services — travel_time is set to -1 as a sentinel (not meaningful)
        # IMPORTANT: stop_order CHECK > 0 means 0 and negatives are invalid
        # Solution: assign pass-through stations placeholder stop_orders starting at 999, after all real stops
        pass_order = 999
        for station_id in s.get("passed_through_stations", []):
            stops.append((s["schedule_id"], station_id, pass_order, -1, False))
            pass_order += 1

    insert_many(
        cur,
        "national_rail_schedule_stops",
        [
            "schedule_id",
            "station_id",
            "stop_order",
            "travel_time_from_origin_min",
            "is_stopping",
        ],
        stops,
    )

    operates = [(s["schedule_id"], day) for s in data for day in s["operates_on"]]
    insert_many(
        cur,
        "national_rail_schedule_operates_on",
        ["schedule_id", "day_of_week"],
        operates,
    )
    print(
        f"  ✓ national_rail_schedules ({len(schedules)}) + fares ({len(fares)}) + stops ({len(stops)}) + operates_on ({len(operates)})"
    )


def seed_seat_layouts(cur):
    data = load("national_rail_seat_layouts.json")

    layouts = [(s["layout_id"], s["schedule_id"]) for s in data]
    insert_many(cur, "seat_layouts", ["layout_id", "schedule_id"], layouts)

    coaches = [
        (s["layout_id"], c["coach"], c["fare_class"])
        for s in data
        for c in s["coaches"]
    ]
    insert_many(cur, "coaches", ["layout_id", "coach", "fare_class"], coaches)

    # Flatten the three-level JSON hierarchy (layout → coaches → seats) into individual seat rows
    seats = [
        (s["layout_id"], c["coach"], seat["seat_id"], seat["row"], seat["column"])
        for s in data
        for c in s["coaches"]
        for seat in c["seats"]
    ]
    insert_many(
        cur,
        "seats",
        ["layout_id", "coach", "seat_id", "seat_row", "seat_column"],
        seats,
    )
    print(
        f"  ✓ seat_layouts ({len(layouts)}) + coaches ({len(coaches)}) + seats ({len(seats)})"
    )


def seed_users(cur):
    data = load("registered_users.json")

    # Split full_name on the first space to populate the separate first_name / last_name columns
    users = []
    for u in data:
        parts = u["full_name"].split(" ", 1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else ""
        users.append(
            (
                u["user_id"],
                first_name,
                last_name,
                u["email"],
                u.get("phone"),
                u.get("date_of_birth"),
                u["registered_at"],
                u["is_active"],
            )
        )
    insert_many(
        cur,
        "users",
        [
            "user_id",
            "first_name",
            "last_name",
            "email",
            "phone",
            "date_of_birth",
            "registered_at",
            "is_active",
        ],
        users,
    )

    # Hash each password with Argon2id (matches register_user / login_user).
    creds = []
    for u in data:
        h, salt = hash_password(u["password"])
        creds.append((u["user_id"], h, salt, "argon2id"))
    
    upsert_many(
        cur,
        "user_credentials",
        ["user_id", "password_hash", "password_salt", "hash_algorithm"],
        creds,
        "user_id",
        ["password_hash", "password_salt", "hash_algorithm"],
    )

    # Hash the secret answer with Argon2id (lower-cased to enable case-insensitive verification).
    questions = []
    for i, u in enumerate(data, start=1):
        h, salt = hash_password(u["secret_answer"].lower())
        questions.append(
            (
                f"SQ{i:03d}",
                u["user_id"],
                u["secret_question"],
                h,
                salt,
                "argon2id",
            )
        )
    
    upsert_many(
        cur,
        "user_security_questions",
        [
            "security_question_id",
            "user_id",
            "secret_question",
            "secret_answer_hash",
            "secret_answer_salt",
            "hash_algorithm",
        ],
        questions,
        "security_question_id",
        [
            "secret_question",
            "secret_answer_hash",
            "secret_answer_salt",
            "hash_algorithm",
        ],
    )

    print(
        f"  ✓ users ({len(users)}) + user_credentials ({len(creds)}) + user_security_questions ({len(questions)})"
    )


def seed_national_rail_bookings(cur):
    """
    bookings.json → journeys (network=national_rail) + bookings
    layout_id is looked up from seat_layouts using schedule_id.
    """
    if not _LAYOUT_LOOKUP:
        _build_layout_lookup()

    data = load("bookings.json")

    # Insert journey rows first — bookings has a FK to journeys, so the parent must exist before the child
    journey_rows = [
        (
            b["booking_id"],  # journey_id = booking_id (BK* prefix)
            "national_rail",
            b["user_id"],
            b["ticket_type"],  # 'single' or 'return'
            b["amount_usd"],
            b["status"],
        )
        for b in data
    ]
    insert_many(
        cur,
        "journeys",
        ["journey_id", "network", "user_id", "ticket_type", "amount_usd", "status"],
        journey_rows,
    )

    # Insert booking rows as children of their corresponding journey rows
    bookings = []
    for b in data:
        layout_id = _LAYOUT_LOOKUP.get(b["schedule_id"])
        # Express schedules currently have no seat layout; skip and warn if encountered (all mock data is normal service)
        if layout_id is None:
            print(
                f"  ⚠ WARNING: no layout for schedule {b['schedule_id']}, skipping {b['booking_id']}"
            )
            continue
        bookings.append(
            (
                b["booking_id"],
                b["schedule_id"],
                b["origin_station_id"],
                b["destination_station_id"],
                b["travel_date"],
                b["departure_time"],
                b["fare_class"],
                layout_id,
                b["coach"],
                b["seat_id"],
                b["stops_travelled"],
                b["booked_at"],
                b.get("travelled_at"),  # nullable — None for bookings not yet travelled
            )
        )
    insert_many(
        cur,
        "bookings",
        [
            "booking_id",
            "schedule_id",
            "origin_station_id",
            "destination_station_id",
            "travel_date",
            "departure_time",
            "fare_class",
            "layout_id",
            "coach",
            "seat_id",
            "stops_travelled",
            "booked_at",
            "travelled_at",
        ],
        bookings,
    )
    print(
        f"  ✓ journeys/national_rail ({len(journey_rows)}) + bookings ({len(bookings)})"
    )


def seed_metro_travels(cur):
    """
    metro_travel_history.json → journeys (network=metro) + metro_trips
    Day-pass add-on trips (e.g. MT021–MT024) require special handling:
      - purchased_at is null in JSON → fall back to travelled_at (column is NOT NULL)
      - day_pass_ref points to the parent day-pass journey_id (also MT* prefix)
    """
    data = load("metro_travel_history.json")

    # Insert parent journey rows before child metro_trips rows (FK dependency)
    journey_rows = [
        (
            t["trip_id"],  # journey_id = trip_id (MT* prefix)
            "metro",
            t["user_id"],
            t["ticket_type"],  # 'single' or 'day_pass'
            t["amount_usd"],
            t["status"],
        )
        for t in data
    ]
    insert_many(
        cur,
        "journeys",
        ["journey_id", "network", "user_id", "ticket_type", "amount_usd", "status"],
        journey_rows,
    )

    # Insert metro_trips child rows linked to their parent journey
    trips = []
    for t in data:
        # purchased_at is NOT NULL in the schema; for day-pass add-on trips the field is null in JSON, so fall back to travelled_at
        purchased_at = t.get("purchased_at") or t.get("travelled_at")
        trips.append(
            (
                t["trip_id"],
                t["schedule_id"],
                t["origin_station_id"],
                t["destination_station_id"],
                t["travel_date"],
                t.get(
                    "day_pass_ref"
                ),  # nullable — points to the day-pass journey that covers this trip
                t.get(
                    "stops_travelled"
                ),  # nullable — unknown at purchase time for tap-in/tap-out trips
                purchased_at,
                t.get(
                    "travelled_at"
                ),  # nullable — None for cancelled trips that were never made
            )
        )
    insert_many(
        cur,
        "metro_trips",
        [
            "trip_id",
            "schedule_id",
            "origin_station_id",
            "destination_station_id",
            "travel_date",
            "day_pass_ref",
            "stops_travelled",
            "purchased_at",
            "travelled_at",
        ],
        trips,
    )
    print(f"  ✓ journeys/metro ({len(journey_rows)}) + metro_trips ({len(trips)})")


def seed_payments(cur):
    """
    Loads payments.json and inserts into the payments table.
    The JSON field booking_id maps to payments.journey_id, because the journeys
    supertype unifies both BK* (national rail) and MT* (metro) identifiers.
    """
    data = load("payments.json")

    payments = [
        (
            p["payment_id"],
            p["booking_id"],  # maps to journeys.journey_id
            p["amount_usd"],
            p["method"],
            p["status"],
            p.get("paid_at"),  # nullable — absent for pending or failed payments
        )
        for p in data
    ]
    insert_many(
        cur,
        "payments",
        ["payment_id", "journey_id", "amount_usd", "method", "status", "paid_at"],
        payments,
    )
    print(f"  ✓ payments ({len(payments)})")


def seed_feedback(cur):
    """
    Loads feedback.json and inserts into the feedback table.
    As with payments, the JSON booking_id field maps to journeys.journey_id.
    """
    data = load("feedback.json")

    feedback = [
        (
            f["feedback_id"],
            f["booking_id"],  # maps to journeys.journey_id
            f["user_id"],
            f["rating"],
            f.get("comment"),  # nullable — feedback comment is optional
            f["submitted_at"],
        )
        for f in data
    ]
    insert_many(
        cur,
        "feedback",
        ["feedback_id", "journey_id", "user_id", "rating", "comment", "submitted_at"],
        feedback,
    )
    print(f"  ✓ feedback ({len(feedback)})")


# ── main ──────────────────────────────────────────────────────────────────────


def main():
    print("Connecting to PostgreSQL...")
    sys.path.insert(0, PROJECT_DIR)
    from databases.relational.queries import ensure_booking_seat_schema

    ensure_booking_seat_schema()
    conn = connect()
    conn.autocommit = False
    cur = conn.cursor()

    try:
        print("Seeding tables (dependency order):")
        seed_metro_stations(cur)
        seed_national_rail_stations(cur)
        seed_metro_schedules(cur)
        seed_national_rail_schedules(cur)
        seed_seat_layouts(cur)  # must run before bookings (FK dependency)
        seed_users(cur)  # must run before journeys (FK dependency)
        print("  → Seeding ticket_types...")
        cur.execute("""
            INSERT INTO ticket_types (ticket_type, display_name, description) VALUES 
            ('single', 'Single Ticket', 'One-way trip'),
            ('return', 'Return Ticket', 'Round trip railway'),
            ('day_pass', 'Day Pass', 'Unlimited metro travel for one day')
            ON CONFLICT DO NOTHING;
        """)
        seed_national_rail_bookings(cur)  # journeys(BK*) + bookings
        seed_metro_travels(cur)  # journeys(MT*) + metro_trips
        seed_payments(cur)
        seed_feedback(cur)
        conn.commit()
        print("\n✅ All done. Database seeded successfully.")
    except Exception as e:
        conn.rollback()
        print(f"\n❌ Error: {e}")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
