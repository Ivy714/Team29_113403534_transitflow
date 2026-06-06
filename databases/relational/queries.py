"""
TransitFlow — PostgreSQL / Relational Database Layer
=====================================================
This module handles all queries to PostgreSQL.

TWO ROLES ARE SERVED HERE:
  1. Relational  → dual-network transit (metro + national rail),
                   availability, fares, bookings, seat selection
  2. Vector      → policy document similarity search (pgvector)

TASK 6 EXTENSION: ``query_schedule_seat_occupancy`` — see ``TASK6.md``.
"""

from __future__ import annotations

import json
import random
import string
from datetime import date, datetime, timezone
from typing import Optional

import psycopg2
import psycopg2.extras
from psycopg2 import errorcodes

from skeleton.config import PG_DSN, VECTOR_TOP_K, VECTOR_SIMILARITY_THRESHOLD
from skeleton.password_hash import hash_password, verify_password


def _connect():
    """Return a new psycopg2 connection with autocommit enabled."""
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = True
    return conn


def _gen_booking_id() -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"BK-{suffix}"


def _gen_payment_id() -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"PM-{suffix}"


def _gen_metro_trip_id() -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"MT-{suffix}"


def _day_of_week_enum(travel_date: str) -> str:
    """Map ISO date to schema ``day_of_week`` enum label (mon–sun)."""
    d = date.fromisoformat(travel_date)
    return ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[d.weekday()]


# ── Example ───────────────────────────────────────────────────────────────────


def example_query() -> dict:
    """Example: returns the name of the connected database."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT current_database() AS db;")
            return dict(cur.fetchone())


# ── BOOKING SCHEMA MIGRATION (existing DBs without full docker reset) ─────────


def ensure_booking_seat_schema() -> None:
    """
    Add ``seat_occupies_slot`` and partial unique index so cancelled seats can be rebooked.

    Safe to call on every seed/booking; no-op when already applied.
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                ALTER TABLE bookings
                ADD COLUMN IF NOT EXISTS seat_occupies_slot BOOLEAN NOT NULL DEFAULT TRUE
                """
            )
            cur.execute(
                """
                ALTER TABLE bookings
                DROP CONSTRAINT IF EXISTS bookings_schedule_id_travel_date_departure_time_coach_seat__key
                """
            )
            cur.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_bookings_active_seat_unique
                ON bookings (schedule_id, travel_date, departure_time, coach, seat_id)
                WHERE seat_occupies_slot = TRUE
                """
            )
            cur.execute(
                """
                UPDATE bookings b
                SET seat_occupies_slot = FALSE
                FROM journeys j
                WHERE j.journey_id = b.booking_id
                  AND j.status = 'cancelled'
                  AND b.seat_occupies_slot = TRUE
                """
            )


# ── NATIONAL RAIL AVAILABILITY ────────────────────────────────────────────────


def query_national_rail_availability(
    origin_id: str,
    destination_id: str,
    travel_date: Optional[str] = None,
) -> list[dict]:
    """
    Return national rail schedules that serve both origin and destination
    in the correct order, with seat occupancy for the requested travel date.

    When ``travel_date`` is set, only schedules operating on that weekday
    (``national_rail_schedule_operates_on``) are included.
    """
    day_filter = ""
    if travel_date:
        day_filter = """
        JOIN national_rail_schedule_operates_on op
            ON op.schedule_id = s.schedule_id
            AND op.day_of_week = %s::day_of_week
        """
        params: list = [_day_of_week_enum(travel_date), origin_id, destination_id, travel_date]
    else:
        params = [origin_id, destination_id, "1900-01-01"]

    sql = f"""
        SELECT
            s.schedule_id,
            s.line,
            s.service_type,
            s.direction,
            s.origin_station_id,
            s.destination_station_id,
            s.first_train_time,
            s.last_train_time,
            s.frequency_min,
            o_stop.stop_order        AS origin_stop_order,
            o_stop.travel_time_from_origin_min AS origin_travel_time,
            d_stop.stop_order        AS destination_stop_order,
            d_stop.travel_time_from_origin_min AS destination_travel_time,
            (d_stop.stop_order - o_stop.stop_order) AS stops_travelled,
            COALESCE(booked.booked_seats, 0) AS booked_seats,
            GREATEST(
                COALESCE(capacity.total_seats, 0) - COALESCE(booked.booked_seats, 0),
                0
            ) AS available_seats
        FROM national_rail_schedules s
        {day_filter}
        JOIN national_rail_schedule_stops o_stop
            ON o_stop.schedule_id = s.schedule_id
            AND o_stop.station_id = %s
            AND o_stop.is_stopping = TRUE
        JOIN national_rail_schedule_stops d_stop
            ON d_stop.schedule_id = s.schedule_id
            AND d_stop.station_id = %s
            AND d_stop.is_stopping = TRUE
        AND o_stop.stop_order < d_stop.stop_order
        LEFT JOIN (
            SELECT sl.schedule_id, COUNT(*) AS total_seats
            FROM seat_layouts sl
            JOIN seats s ON s.layout_id = sl.layout_id
            GROUP BY sl.schedule_id
        ) capacity ON capacity.schedule_id = s.schedule_id
        LEFT JOIN (
            SELECT b.schedule_id, COUNT(*) AS booked_seats
            FROM bookings b
            JOIN journeys j ON j.journey_id = b.booking_id
            WHERE b.travel_date = %s
            AND j.status != 'cancelled'
            GROUP BY b.schedule_id
        ) booked ON booked.schedule_id = s.schedule_id
        ORDER BY s.line, s.service_type, s.first_train_time
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, tuple(params))
            return [dict(row) for row in cur.fetchall()]


def query_national_rail_fare(
    schedule_id: str,
    fare_class: str,
    stops_travelled: int,
) -> Optional[dict]:
    """Calculate the fare for a national rail journey."""
    sql = """
        SELECT
            fare_class,
            base_fare_usd,
            per_stop_rate_usd,
            ROUND(base_fare_usd + (per_stop_rate_usd * %s), 2) AS total_fare_usd
        FROM national_rail_schedule_fares
        WHERE schedule_id = %s
        AND   fare_class  = %s
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (stops_travelled, schedule_id, fare_class))
            row = cur.fetchone()
            return dict(row) if row else None


# ── METRO SCHEDULES & FARE ────────────────────────────────────────────────────


def query_metro_schedules(origin_id: str, destination_id: str) -> list[dict]:
    """Return metro schedules serving both origin and destination in correct order."""
    sql = """
        SELECT
            s.schedule_id,
            s.line,
            s.direction,
            s.first_train_time,
            s.last_train_time,
            s.frequency_min,
            s.base_fare_usd,
            s.per_stop_rate_usd,
            o_stop.stop_order        AS origin_stop_order,
            d_stop.stop_order        AS destination_stop_order,
            (d_stop.stop_order - o_stop.stop_order) AS stops_travelled
        FROM metro_schedules s
        JOIN metro_schedule_stops o_stop
            ON o_stop.schedule_id = s.schedule_id
            AND o_stop.station_id = %s
        JOIN metro_schedule_stops d_stop
            ON d_stop.schedule_id = s.schedule_id
            AND d_stop.station_id = %s
        AND o_stop.stop_order < d_stop.stop_order
        ORDER BY s.line, s.first_train_time
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (origin_id, destination_id))
            return [dict(row) for row in cur.fetchall()]


def query_metro_fare(schedule_id: str, stops_travelled: int) -> Optional[dict]:
    """Calculate the metro fare for a single-ticket journey."""
    sql = """
        SELECT
            base_fare_usd,
            per_stop_rate_usd,
            ROUND(base_fare_usd + (per_stop_rate_usd * %s), 2) AS total_fare_usd
        FROM metro_schedules
        WHERE schedule_id = %s
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (stops_travelled, schedule_id))
            row = cur.fetchone()
            return dict(row) if row else None


# ── SEAT SELECTION ────────────────────────────────────────────────────────────


def query_available_seats(
    schedule_id: str,
    travel_date: str,
    fare_class: str,
) -> list[dict]:
    """Return available (unbooked) seats for a national rail journey on a given date."""
    sql = """
        SELECT
            s.seat_id,
            s.coach,
            s.seat_row   AS row,
            s.seat_column AS column,
            c.fare_class
        FROM seat_layouts sl
        JOIN coaches c  ON c.layout_id = sl.layout_id
                       AND c.fare_class = %s
        JOIN seats   s  ON s.layout_id  = c.layout_id
                       AND s.coach      = c.coach
        WHERE sl.schedule_id = %s
        -- exclude seats already booked on this date
        AND NOT EXISTS (
            SELECT 1 FROM bookings b
            WHERE b.schedule_id = sl.schedule_id
            AND   b.travel_date = %s
            AND   b.coach       = s.coach
            AND   b.seat_id     = s.seat_id
            AND   b.seat_occupies_slot = TRUE
        )
        ORDER BY s.coach, s.seat_row, s.seat_column
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (fare_class, schedule_id, travel_date))
            return [dict(row) for row in cur.fetchall()]


def auto_select_adjacent_seats(available_seats: list[dict], count: int) -> list[str]:
    """Select seats as close together as possible (same row preferred)."""
    if not available_seats or count <= 0:
        return []
    if count >= len(available_seats):
        return [s["seat_id"] for s in available_seats[:count]]

    from collections import defaultdict

    rows: dict[int, list[dict]] = defaultdict(list)
    for seat in available_seats:
        rows[seat["row"]].append(seat)

    for row_seats in sorted(rows.values(), key=lambda s: s[0]["row"]):
        if len(row_seats) >= count:
            return [s["seat_id"] for s in row_seats[:count]]

    sorted_seats = sorted(available_seats, key=lambda s: (s["row"], s["column"]))
    return [s["seat_id"] for s in sorted_seats[:count]]


# ── USER & BOOKING QUERIES ────────────────────────────────────────────────────


def query_user_profile(user_email: str) -> Optional[dict]:
    """Return a user's profile by email."""
    sql = """
        SELECT
            user_id,
            first_name,
            last_name,
            first_name || ' ' || last_name AS full_name,
            email,
            phone,
            date_of_birth,
            EXTRACT(YEAR FROM date_of_birth)::INTEGER AS year_of_birth,
            registered_at,
            is_active
        FROM users
        WHERE email = %s
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (user_email,))
            row = cur.fetchone()
            return dict(row) if row else None


def query_user_bookings(user_email: str) -> dict:
    """Return a user's combined booking history (national rail + metro)."""
    # First get user_id from email
    user = query_user_profile(user_email)
    if not user:
        return {"national_rail": [], "metro": []}

    user_id = user["user_id"]

    # National rail bookings
    nr_sql = """
        SELECT
            b.booking_id,
            j.ticket_type,
            j.amount_usd,
            j.status,
            b.schedule_id,
            b.origin_station_id,
            b.destination_station_id,
            s.line,
            s.service_type,
            o_st.name   AS origin_name,
            d_st.name   AS destination_name,
            b.travel_date,
            b.departure_time,
            b.fare_class,
            b.coach,
            b.seat_id,
            b.stops_travelled,
            b.booked_at,
            b.travelled_at
        FROM bookings b
        JOIN journeys j            ON j.journey_id  = b.booking_id
        JOIN national_rail_schedules s ON s.schedule_id = b.schedule_id
        JOIN national_rail_stations o_st ON o_st.station_id = b.origin_station_id
        JOIN national_rail_stations d_st ON d_st.station_id = b.destination_station_id
        WHERE j.user_id = %s
        ORDER BY b.travel_date DESC, b.booked_at DESC
    """

    # Metro trips
    metro_sql = """
        SELECT
            t.trip_id,
            j.ticket_type,
            j.amount_usd,
            j.status,
            t.schedule_id,
            ms.line,
            o_st.name  AS origin_name,
            d_st.name  AS destination_name,
            t.travel_date,
            t.stops_travelled,
            t.purchased_at,
            t.travelled_at,
            t.day_pass_ref
        FROM metro_trips t
        JOIN journeys j         ON j.journey_id = t.trip_id
        JOIN metro_schedules ms ON ms.schedule_id = t.schedule_id
        JOIN metro_stations o_st ON o_st.station_id = t.origin_station_id
        JOIN metro_stations d_st ON d_st.station_id = t.destination_station_id
        WHERE j.user_id = %s
        ORDER BY t.travel_date DESC, t.purchased_at DESC
    """

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(nr_sql, (user_id,))
            national_rail = [dict(row) for row in cur.fetchall()]
            cur.execute(metro_sql, (user_id,))
            metro = [dict(row) for row in cur.fetchall()]

    return {"national_rail": national_rail, "metro": metro}


def query_payment_info(booking_id: str) -> Optional[dict]:
    """Return payment record for a booking or metro trip (journey_id)."""
    sql = """
        SELECT
            p.payment_id,
            p.journey_id,
            p.amount_usd,
            p.method,
            p.status,
            p.paid_at
        FROM payments p
        WHERE p.journey_id = %s
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (booking_id,))
            row = cur.fetchone()
            return dict(row) if row else None


# ── TRANSACTIONAL OPERATIONS ──────────────────────────────────────────────────


def execute_booking(
    user_id: str,
    schedule_id: str,
    origin_station_id: str,
    destination_station_id: str,
    travel_date: str,
    fare_class: str,
    seat_id: str,
    ticket_type: str = "single",
) -> tuple[bool, dict | str]:
    """Create a national rail booking for a logged-in user."""
    ensure_booking_seat_schema()
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # 0. 同一使用者同路线同日已 confirmed → 提示勿重複訂
            cur.execute(
                """
                SELECT b.booking_id FROM bookings b
                JOIN journeys j ON j.journey_id = b.booking_id
                WHERE j.user_id = %s AND j.status = 'confirmed'
                AND b.schedule_id = %s AND b.travel_date = %s
                AND b.origin_station_id = %s AND b.destination_station_id = %s
                LIMIT 1
                """,
                (
                    user_id,
                    schedule_id,
                    travel_date,
                    origin_station_id,
                    destination_station_id,
                ),
            )
            existing = cur.fetchone()
            if existing:
                return (
                    False,
                    f"You already have booking {existing['booking_id']} on {travel_date} "
                    f"for this route. Cancel it first or choose another date.",
                )

            # 1. 確認班次存在
            cur.execute(
                "SELECT schedule_id, service_type FROM national_rail_schedules WHERE schedule_id = %s",
                (schedule_id,),
            )
            schedule = cur.fetchone()
            if not schedule:
                return False, f"Schedule {schedule_id} not found."

            # 2. 確認 origin / destination 在該班次停靠，且順序正確
            cur.execute(
                """
                SELECT stop_order FROM national_rail_schedule_stops
                WHERE schedule_id = %s AND station_id = %s AND is_stopping = TRUE
            """,
                (schedule_id, origin_station_id),
            )
            origin_stop = cur.fetchone()

            cur.execute(
                """
                SELECT stop_order FROM national_rail_schedule_stops
                WHERE schedule_id = %s AND station_id = %s AND is_stopping = TRUE
            """,
                (schedule_id, destination_station_id),
            )
            dest_stop = cur.fetchone()

            if not origin_stop or not dest_stop:
                return False, "Origin or destination not served by this schedule."
            if origin_stop["stop_order"] >= dest_stop["stop_order"]:
                return False, "Origin must come before destination on this route."

            stops_travelled = dest_stop["stop_order"] - origin_stop["stop_order"]

            # 3. 計算票價
            cur.execute(
                """
                SELECT base_fare_usd, per_stop_rate_usd
                FROM national_rail_schedule_fares
                WHERE schedule_id = %s AND fare_class = %s
            """,
                (schedule_id, fare_class),
            )
            fare = cur.fetchone()
            if not fare:
                return (
                    False,
                    f"Fare class '{fare_class}' not available for {schedule_id}.",
                )

            amount = round(
                float(fare["base_fare_usd"])
                + float(fare["per_stop_rate_usd"]) * stops_travelled,
                2,
            )

            # 4. 取得 layout_id
            cur.execute(
                "SELECT layout_id FROM seat_layouts WHERE schedule_id = %s",
                (schedule_id,),
            )
            layout_row = cur.fetchone()
            if not layout_row:
                return False, f"No seat layout found for {schedule_id}."
            layout_id = layout_row["layout_id"]

            # 5. 確認座位存在且屬於正確的 fare_class
            cur.execute(
                """
                SELECT s.seat_id, s.coach, c.fare_class
                FROM seats s
                JOIN coaches c ON c.layout_id = s.layout_id AND c.coach = s.coach
                WHERE s.layout_id = %s AND s.seat_id = %s AND c.fare_class = %s
            """,
                (layout_id, seat_id, fare_class),
            )
            seat = cur.fetchone()
            if not seat:
                return False, f"Seat {seat_id} not found or not in {fare_class} class."

            coach = seat["coach"]

            # 6. 確認座位未被佔用（含已取消但尚未釋放 slot 的舊資料）
            cur.execute(
                """
                SELECT 1 FROM bookings b
                WHERE b.schedule_id = %s AND b.travel_date = %s
                AND b.coach = %s AND b.seat_id = %s
                AND b.seat_occupies_slot = TRUE
            """,
                (schedule_id, travel_date, coach, seat_id),
            )
            if cur.fetchone():
                return False, f"Seat {seat_id} is already booked on {travel_date}."

            # 7. 取得 departure_time
            cur.execute(
                """
                SELECT first_train_time AS departure_time
                FROM national_rail_schedules WHERE schedule_id = %s
            """,
                (schedule_id,),
            )
            dep = cur.fetchone()
            departure_time = dep["departure_time"] if dep else None

            # 8. INSERT journey
            booking_id = _gen_booking_id()
            cur.execute(
                """
                INSERT INTO journeys (journey_id, network, user_id, ticket_type, amount_usd, status)
                VALUES (%s, 'national_rail', %s, %s, %s, 'confirmed')
            """,
                (booking_id, user_id, ticket_type, amount),
            )

            # 9. INSERT booking
            booked_at = datetime.now(timezone.utc)
            cur.execute(
                """
                INSERT INTO bookings
                    (booking_id, schedule_id, origin_station_id, destination_station_id,
                     travel_date, departure_time, fare_class, layout_id, coach, seat_id,
                     stops_travelled, booked_at, travelled_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL)
            """,
                (
                    booking_id,
                    schedule_id,
                    origin_station_id,
                    destination_station_id,
                    travel_date,
                    departure_time,
                    fare_class,
                    layout_id,
                    coach,
                    seat_id,
                    stops_travelled,
                    booked_at,
                ),
            )

            # 10. INSERT payment
            payment_id = _gen_payment_id()
            cur.execute(
                """
                INSERT INTO payments (payment_id, journey_id, amount_usd, method, status, paid_at)
                VALUES (%s, %s, %s, 'credit_card', 'paid', %s)
            """,
                (payment_id, booking_id, amount, booked_at),
            )

            conn.commit()

            return True, {
                "booking_id": booking_id,
                "payment_id": payment_id,
                "schedule_id": schedule_id,
                "origin_station_id": origin_station_id,
                "destination_station_id": destination_station_id,
                "travel_date": travel_date,
                "fare_class": fare_class,
                "coach": coach,
                "seat_id": seat_id,
                "stops_travelled": stops_travelled,
                "amount_usd": amount,
                "status": "confirmed",
                "booked_at": booked_at.isoformat(),
            }

    except psycopg2.IntegrityError as e:
        conn.rollback()
        if e.pgcode == errorcodes.UNIQUE_VIOLATION:
            return (
                False,
                f"Seat {seat_id} was just taken on {travel_date}. Please try another seat.",
            )
        return False, str(e)
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()


def execute_metro_booking(
    user_id: str,
    schedule_id: str,
    origin_station_id: str,
    destination_station_id: str,
    travel_date: str,
    ticket_type: str = "single",
) -> tuple[bool, dict | str]:
    """
    Purchase a metro single ticket (app / online mock) for a logged-in user.

    Uses ``query_metro_schedules`` + ``query_metro_fare``; no seat assignment per booking_rules.json.
    """
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if ticket_type not in ("single", "day_pass"):
                return False, "Metro online booking supports single or day_pass only."

            cur.execute(
                """
                SELECT 1 FROM metro_trips t
                JOIN journeys j ON j.journey_id = t.trip_id
                WHERE j.user_id = %s AND j.status = 'confirmed'
                AND t.origin_station_id = %s AND t.destination_station_id = %s
                AND t.travel_date = %s
                LIMIT 1
                """,
                (user_id, origin_station_id, destination_station_id, travel_date),
            )
            if cur.fetchone():
                return (
                    False,
                    "You already have a confirmed metro trip on this route and date.",
                )

            rows = query_metro_schedules(origin_station_id, destination_station_id)
            row = next((r for r in rows if r["schedule_id"] == schedule_id), None)
            if not row and rows:
                row = rows[0]
                schedule_id = row["schedule_id"]
            if not row:
                return False, f"No metro service {origin_station_id}→{destination_station_id}."

            stops = int(row["stops_travelled"])
            if ticket_type == "day_pass":
                amount = 5.00
            else:
                fare = query_metro_fare(schedule_id, stops)
                if not fare:
                    return False, f"Could not calculate fare for {schedule_id}."
                amount = float(fare["total_fare_usd"])

            trip_id = _gen_metro_trip_id()
            purchased_at = datetime.now(timezone.utc)
            cur.execute(
                """
                INSERT INTO journeys (journey_id, network, user_id, ticket_type, amount_usd, status)
                VALUES (%s, 'metro', %s, %s, %s, 'confirmed')
                """,
                (trip_id, user_id, ticket_type, amount),
            )
            cur.execute(
                """
                INSERT INTO metro_trips
                    (trip_id, schedule_id, origin_station_id, destination_station_id,
                     travel_date, day_pass_ref, stops_travelled, purchased_at, travelled_at)
                VALUES (%s, %s, %s, %s, %s, NULL, %s, %s, NULL)
                """,
                (
                    trip_id,
                    schedule_id,
                    origin_station_id,
                    destination_station_id,
                    travel_date,
                    stops if ticket_type == "single" else None,
                    purchased_at,
                ),
            )
            payment_id = _gen_payment_id()
            cur.execute(
                """
                INSERT INTO payments (payment_id, journey_id, amount_usd, method, status, paid_at)
                VALUES (%s, %s, %s, 'ewallet', 'paid', %s)
                """,
                (payment_id, trip_id, amount, purchased_at),
            )
            conn.commit()
            return True, {
                "trip_id": trip_id,
                "payment_id": payment_id,
                "schedule_id": schedule_id,
                "origin_station_id": origin_station_id,
                "destination_station_id": destination_station_id,
                "travel_date": travel_date,
                "ticket_type": ticket_type,
                "stops_travelled": stops,
                "amount_usd": amount,
                "status": "confirmed",
            }
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()


def execute_cancellation(booking_id: str, user_id: str) -> tuple[bool, dict | str]:
    """
    Cancel a national rail booking or metro trip and calculate refund per policy.

    National rail: RF001 / RF002. Metro: RF003 (single) / RF004 (day_pass).
    """
    journey_id = booking_id.upper()
    if journey_id.startswith("MT"):
        return _execute_metro_cancellation(journey_id, user_id)

    ensure_booking_seat_schema()
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # 1. 取得 booking 資訊
            cur.execute(
                """
                SELECT
                    b.booking_id,
                    j.user_id,
                    j.amount_usd,
                    j.status,
                    b.travel_date,
                    b.departure_time,
                    s.service_type
                FROM bookings b
                JOIN journeys j ON j.journey_id = b.booking_id
                JOIN national_rail_schedules s ON s.schedule_id = b.schedule_id
                WHERE b.booking_id = %s
            """,
                (booking_id,),
            )
            booking = cur.fetchone()

            if not booking:
                return False, f"Booking {booking_id} not found."
            if booking["user_id"] != user_id:
                return False, "You are not authorised to cancel this booking."
            if booking["status"] == "cancelled":
                return False, "Booking is already cancelled."
            if booking["status"] == "completed":
                return False, "Cannot cancel a completed journey."

            # 2. 計算距出發幾小時
            departure_dt = datetime.combine(
                booking["travel_date"],
                booking["departure_time"],
                tzinfo=timezone.utc,
            )
            now = datetime.now(timezone.utc)
            hours_until = (departure_dt - now).total_seconds() / 3600

            # 3. 套用退款政策
            service_type = booking["service_type"]
            amount = float(booking["amount_usd"])

            if service_type == "normal":
                # RF001
                if hours_until >= 48:
                    refund_pct, admin_fee, note = (
                        1.00,
                        0.00,
                        "RF001 W1: full refund (≥48h)",
                    )
                elif hours_until >= 24:
                    refund_pct, admin_fee, note = (
                        0.75,
                        0.50,
                        "RF001 W2: 75% refund (24–48h)",
                    )
                elif hours_until >= 2:
                    refund_pct, admin_fee, note = (
                        0.50,
                        0.50,
                        "RF001 W3: 50% refund (2–24h)",
                    )
                else:
                    refund_pct, admin_fee, note = (
                        0.00,
                        0.00,
                        "RF001 W4: no refund (<2h)",
                    )
            else:
                # RF002 express
                if hours_until >= 48:
                    refund_pct, admin_fee, note = (
                        1.00,
                        1.00,
                        "RF002 W1: full refund, $1 fee (≥48h)",
                    )
                elif hours_until >= 24:
                    refund_pct, admin_fee, note = (
                        0.50,
                        1.00,
                        "RF002 W2: 50% refund, $1 fee (24–48h)",
                    )
                else:
                    refund_pct, admin_fee, note = (
                        0.00,
                        0.00,
                        "RF002 W3: no refund (<24h)",
                    )

            refund_amount = round(max(amount * refund_pct - admin_fee, 0), 2)

            # 4. 更新 journey status
            cur.execute(
                """
                UPDATE journeys SET status = 'cancelled' WHERE journey_id = %s
            """,
                (booking_id,),
            )

            # 4b. 釋放座位 slot，讓同日期同座位可再次訂票
            cur.execute(
                """
                UPDATE bookings SET seat_occupies_slot = FALSE
                WHERE booking_id = %s
                """,
                (booking_id,),
            )

            # 5. 更新 payment status
            cur.execute(
                """
                UPDATE payments SET status = 'refunded'
                WHERE journey_id = %s AND status = 'paid'
            """,
                (booking_id,),
            )

            conn.commit()

            return True, {
                "booking_id": booking_id,
                "original_amount_usd": amount,
                "refund_amount": refund_amount,
                "refund_amount_usd": refund_amount,
                "admin_fee_usd": admin_fee,
                "policy_note": note,
                "hours_until_departure": round(hours_until, 1),
            }

    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()


def _execute_metro_cancellation(trip_id: str, user_id: str) -> tuple[bool, dict | str]:
    """Cancel a metro trip (RF003 / RF004 — full refund before first tap-in)."""
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    t.trip_id,
                    j.user_id,
                    j.amount_usd,
                    j.status,
                    j.ticket_type,
                    t.travel_date,
                    t.travelled_at
                FROM metro_trips t
                JOIN journeys j ON j.journey_id = t.trip_id
                WHERE t.trip_id = %s
                """,
                (trip_id,),
            )
            trip = cur.fetchone()
            if not trip:
                return False, f"Metro trip {trip_id} not found."
            if trip["user_id"] != user_id:
                return False, "You are not authorised to cancel this trip."
            if trip["status"] == "cancelled":
                return False, "Trip is already cancelled."
            if trip["status"] == "completed" or trip["travelled_at"]:
                return False, "Cannot cancel — journey already commenced (RF003/RF004)."

            amount = float(trip["amount_usd"])
            policy = "RF003" if trip["ticket_type"] == "single" else "RF004"
            refund_amount = amount
            cur.execute(
                "UPDATE journeys SET status = 'cancelled' WHERE journey_id = %s",
                (trip_id,),
            )
            cur.execute(
                """
                UPDATE payments SET status = 'refunded'
                WHERE journey_id = %s AND status = 'paid'
                """,
                (trip_id,),
            )
            conn.commit()
            return True, {
                "booking_id": trip_id,
                "original_amount_usd": amount,
                "refund_amount": refund_amount,
                "refund_amount_usd": refund_amount,
                "admin_fee_usd": 0.0,
                "policy_note": f"{policy}: 100% refund before first tap-in",
            }
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()


# ── AUTHENTICATION QUERIES ────────────────────────────────────────────────────


def register_user(
    email: str,
    first_name: str,
    surname: str,
    year_of_birth: int,
    password: str,
    secret_question: str,
    secret_answer: str,
) -> tuple[bool, str]:
    """Register a new user. Returns (True, user_id) or (False, error_message)."""
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            # 確認 email 不重複
            cur.execute("SELECT 1 FROM users WHERE email = %s", (email,))
            if cur.fetchone():
                return False, f"Email {email} is already registered."

            # 產生 user_id
            cur.execute("SELECT COUNT(*) FROM users")
            count = cur.fetchone()[0]
            user_id = f"RU{count + 1:02d}"

            # INSERT user
            registered_at = datetime.now(timezone.utc)
            dob = date(year_of_birth, 1, 1)
            cur.execute(
                """
                INSERT INTO users
                    (user_id, first_name, last_name, email, date_of_birth, registered_at, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, TRUE)
            """,
                (user_id, first_name, surname, email, dob, registered_at),
            )

            # INSERT credentials
            pw_hash, pw_salt = hash_password(password)
            cur.execute(
                """
                INSERT INTO user_credentials
                    (user_id, password_hash, password_salt, hash_algorithm)
                VALUES (%s, %s, %s, 'argon2id')
            """,
                (user_id, pw_hash, pw_salt),
            )

            # INSERT security question
            sq_hash, sq_salt = hash_password(secret_answer.lower())
            cur.execute("SELECT COUNT(*) FROM user_security_questions")
            sq_count = cur.fetchone()[0]
            sq_id = f"SQ{sq_count + 1:03d}"
            cur.execute(
                """
                INSERT INTO user_security_questions
                    (security_question_id, user_id, secret_question,
                     secret_answer_hash, secret_answer_salt, hash_algorithm)
                VALUES (%s, %s, %s, %s, %s, 'argon2id')
            """,
                (sq_id, user_id, secret_question, sq_hash, sq_salt),
            )

            conn.commit()
            return True, user_id

    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()


def login_user(email: str, password: str) -> Optional[dict]:
    """Verify credentials. Returns user dict on success or None on failure."""
    sql = """
        SELECT
            u.user_id,
            u.email,
            u.first_name || ' ' || u.last_name AS full_name,
            u.first_name,
            u.last_name   AS surname,
            u.phone,
            u.date_of_birth,
            EXTRACT(YEAR FROM u.date_of_birth)::INTEGER AS year_of_birth,
            u.is_active,
            uc.password_hash,
            uc.password_salt
        FROM users u
        JOIN user_credentials uc ON uc.user_id = u.user_id
        WHERE u.email = %s
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (email,))
            row = cur.fetchone()
            if not row:
                return None
            if not row["is_active"]:
                return None
            if not verify_password(
                password, row["password_hash"], bytes(row["password_salt"])
            ):
                return None
            # 不回傳 hash/salt
            return {
                "user_id": row["user_id"],
                "email": row["email"],
                "full_name": row["full_name"],
                "first_name": row["first_name"],
                "surname": row["surname"],
                "phone": row["phone"],
                "date_of_birth": row["date_of_birth"],
                "year_of_birth": row["year_of_birth"],
                "is_active": row["is_active"],
            }


def get_user_secret_question(email: str) -> Optional[str]:
    """Return the secret question for a registered email, or None if not found."""
    sql = """
        SELECT usq.secret_question
        FROM user_security_questions usq
        JOIN users u ON u.user_id = usq.user_id
        WHERE u.email = %s
        LIMIT 1
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (email,))
            row = cur.fetchone()
            return row[0] if row else None


def verify_secret_answer(email: str, answer: str) -> bool:
    """Return True if the answer matches the stored secret answer (case-insensitive)."""
    sql = """
        SELECT usq.secret_answer_hash, usq.secret_answer_salt
        FROM user_security_questions usq
        JOIN users u ON u.user_id = usq.user_id
        WHERE u.email = %s
        LIMIT 1
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (email,))
            row = cur.fetchone()
            if not row:
                return False
            stored_hash, stored_salt = row
            return verify_password(answer.lower(), stored_hash, bytes(stored_salt))


def update_password(email: str, new_password: str) -> bool:
    """Update the password for a user. Returns True if updated."""
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM users WHERE email = %s", (email,))
            row = cur.fetchone()
            if not row:
                return False
            user_id = row[0]
            pw_hash, pw_salt = hash_password(new_password)
            cur.execute(
                """
                UPDATE user_credentials
                SET password_hash = %s,
                    password_salt = %s,
                    updated_at    = now()
                WHERE user_id = %s
            """,
                (pw_hash, pw_salt, user_id),
            )
            conn.commit()
            return cur.rowcount > 0
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


# ── VECTOR / RAG QUERIES — do not modify ─────────────────────────────────────


def query_policy_vector_search(
    embedding: list[float], top_k: int = VECTOR_TOP_K
) -> list[dict]:
    """Find the most relevant policy documents for a given query embedding."""
    sql = """
        SELECT
            chunk_id,
            title,
            category,
            document_type,
            policy_id,
            content,
            metadata,
            source_file,
            1 - (embedding <=> %s::vector) AS similarity
        FROM policy_documents
        WHERE 1 - (embedding <=> %s::vector) > %s
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """
    vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                sql, (vec_str, vec_str, VECTOR_SIMILARITY_THRESHOLD, vec_str, top_k)
            )
            return [dict(row) for row in cur.fetchall()]


def query_schedule_seat_occupancy(
    schedule_id: str, travel_date: str, fare_class: str = "standard"
) -> dict:
    """
    Return booked vs available seat counts for a national rail schedule on a date.

    Task 6 extension — useful for capacity / availability questions in the agent.

    Algorithm (why two queries):
    1. ``total_seats`` — COUNT seats joined through coaches/layouts for this schedule
       and fare class (physical capacity from seed data).
    2. ``available_seats`` — len of ``query_available_seats`` which excludes seats
       with active ``bookings.seat_occupies_slot = TRUE`` on that date.
    3. ``booked_seats`` — derived as total − available so the three numbers reconcile.
    """
    # Reuse existing seat-selection logic so occupancy matches booking rules.
    seats = query_available_seats(schedule_id, travel_date, fare_class)
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Total capacity: all physical seats in coaches assigned to this fare class.
            cur.execute(
                """
                SELECT COUNT(*) AS total_seats
                FROM seats s
                JOIN coaches c ON c.layout_id = s.layout_id AND c.coach = s.coach
                JOIN seat_layouts sl ON sl.layout_id = s.layout_id
                WHERE sl.schedule_id = %s AND c.fare_class = %s
                """,
                (schedule_id, fare_class),
            )
            row = cur.fetchone()
    total = int(row["total_seats"]) if row else 0
    available = len(seats)
    booked = max(total - available, 0)
    return {
        "schedule_id": schedule_id,
        "travel_date": travel_date,
        "fare_class": fare_class,
        "total_seats": total,
        "booked_seats": booked,
        "available_seats": available,
    }


def store_policy_document(
    chunk_id: str,
    title: str,
    category: str,
    document_type: str,
    policy_id: str,
    content: str,
    metadata: dict,
    embedding: list[float],
    source_file: str = "",
) -> int:
    """Insert or update a policy chunk with its embedding."""
    sql = """
        INSERT INTO policy_documents (
            chunk_id, title, category, document_type, policy_id,
            content, metadata, embedding, source_file
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::vector, %s)
        ON CONFLICT (chunk_id) DO UPDATE SET
            title = EXCLUDED.title,
            category = EXCLUDED.category,
            document_type = EXCLUDED.document_type,
            policy_id = EXCLUDED.policy_id,
            content = EXCLUDED.content,
            metadata = EXCLUDED.metadata,
            embedding = EXCLUDED.embedding,
            source_file = EXCLUDED.source_file
        RETURNING id
    """
    vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    chunk_id,
                    title,
                    category,
                    document_type,
                    policy_id,
                    content,
                    json.dumps(metadata),
                    vec_str,
                    source_file,
                ),
            )
            return cur.fetchone()[0]
