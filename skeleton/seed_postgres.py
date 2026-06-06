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

# ── resolve paths ─────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(PROJECT_DIR, "train-mock-data")

sys.path.insert(0, PROJECT_DIR)
from skeleton import config as cfg
from skeleton.password_hash import hash_password


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

    # metro_stations 主表
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

    # metro_station_lines：lines 陣列拆開，一條線一筆
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

    # metro_schedules 主表
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

    # metro_schedule_stops
    # PK = (schedule_id, stop_order), UNIQUE = (schedule_id, station_id)
    # 合併 stops_in_order（順序）和 travel_time_from_origin_min（時間）
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

    # metro_schedule_operates_on：operates_on 陣列拆開
    operates = [(s["schedule_id"], day) for s in data for day in s["operates_on"]]
    insert_many(
        cur, "metro_schedule_operates_on", ["schedule_id", "day_of_week"], operates
    )
    print(
        f"  ✓ metro_schedules ({len(schedules)}) + stops ({len(stops)}) + operates_on ({len(operates)})"
    )


def seed_national_rail_schedules(cur):
    data = load("national_rail_schedules.json")

    # national_rail_schedules 主表（不含票價，票價另存）
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

    # national_rail_schedule_fares
    # fare_classes 是 {"standard": {...}, "first": {...}}，拆成兩筆
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

    # national_rail_schedule_stops
    # PK = (schedule_id, stop_order), CHECK stop_order > 0
    # is_stopping = TRUE  → stops_in_order（真正停靠）
    # is_stopping = FALSE → passed_through_stations（express 過站）
    # 注意：express 過站沒有 stop_order 資訊，用負數佔位避開 CHECK > 0
    # 用 -1, -2... 讓 UNIQUE (schedule_id, station_id) 不衝突即可
    stops = []
    for s in data:
        # 真正停靠站
        for order, station_id in enumerate(s["stops_in_order"], start=1):
            travel_time = s["travel_time_from_origin_min"][station_id]
            stops.append((s["schedule_id"], station_id, order, travel_time, True))

        # express 過站（沒有 stop_order，travel_time 設 -1 表示無意義）
        # 注意：stop_order CHECK > 0，所以不能用 0 或負數！
        # 解法：把過站的 stop_order 接在停靠站後面繼續編號，用 999 開始
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

    # seats：三層巢狀 layout → coaches → seats 拆平
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

    # users：full_name 用第一個空格切成 first_name / last_name
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

    # user_credentials: argon2id (matches register_user / login_user)
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

    # user_security_questions：secret_question / secret_answer 各一筆
    questions = []
    for i, u in enumerate(data, start=1):
        h, salt = hash_password(u["secret_answer"])
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
    layout_id 從 seat_layouts 反查 schedule_id。
    """
    if not _LAYOUT_LOOKUP:
        _build_layout_lookup()

    data = load("bookings.json")

    # journeys 主表（先 INSERT，bookings 才能 FK 參照）
    journey_rows = [
        (
            b["booking_id"],  # journey_id = booking_id（BK*）
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

    # bookings 子表
    bookings = []
    for b in data:
        layout_id = _LAYOUT_LOOKUP.get(b["schedule_id"])
        # Express 班次沒有 layout（目前 bookings.json 全是 normal，不會發生）
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
                b.get("travelled_at"),  # nullable
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

    Day pass 附加行程（MT021–MT024）特殊處理：
      - purchased_at = null → 用 travelled_at 補（purchased_at NOT NULL）
      - day_pass_ref 指向原始 day_pass 的 journey_id（也是 MT*）
    """
    data = load("metro_travel_history.json")

    # journeys 主表
    journey_rows = [
        (
            t["trip_id"],  # journey_id = trip_id（MT*）
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

    # metro_trips 子表
    trips = []
    for t in data:
        # purchased_at NOT NULL：附加行程原始欄位是 null，改用 travelled_at
        purchased_at = t.get("purchased_at") or t.get("travelled_at")
        trips.append(
            (
                t["trip_id"],
                t["schedule_id"],
                t["origin_station_id"],
                t["destination_station_id"],
                t["travel_date"],
                t.get("day_pass_ref"),  # nullable，指向原始 day_pass journey_id
                t.get("stops_travelled"),  # nullable（day_pass 附加行程是 None）
                purchased_at,
                t.get("travelled_at"),  # nullable（cancelled 的沒有）
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
    payments.json → payments
    booking_id 在 JSON 裡對應的是 journey_id（BK* 或 MT*），
    因為 journeys supertype 已經統一，直接用 journey_id 欄位。
    """
    data = load("payments.json")

    payments = [
        (
            p["payment_id"],
            p["booking_id"],  # 對應 journeys.journey_id
            p["amount_usd"],
            p["method"],
            p["status"],
            p.get("paid_at"),  # nullable（pending/failed 可能沒有）
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
    feedback.json → feedback
    booking_id 同 payments，對應 journeys.journey_id。
    """
    data = load("feedback.json")

    feedback = [
        (
            f["feedback_id"],
            f["booking_id"],  # 對應 journeys.journey_id
            f["user_id"],
            f["rating"],
            f.get("comment"),  # nullable
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
        seed_seat_layouts(cur)  # bookings FK 依賴
        seed_users(cur)  # journeys FK 依賴
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
