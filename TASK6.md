# Task 6 Extension — Schedule Seat Occupancy

Team **113403504** optional database extension for per-schedule capacity reporting.

## Motivation

Passengers and staff often ask how full a specific train is on a given date. The course
schema already tracks individual seat bookings, but there was no single query that
returned **total vs booked vs available** seats for a schedule. This extension adds that
aggregate view and wires it into the agent.

## Files modified or added

| File | Change |
|------|--------|
| `databases/relational/queries.py` | Added `query_schedule_seat_occupancy(schedule_id, travel_date, fare_class)` |
| `skeleton/agent.py` | Rule-based handler for “How many seats on NR_SCH01 …?” style questions |
| `TASK6.md` | This manifest |

## Database operation

Function: **`query_schedule_seat_occupancy`**

- Counts all seats in `seats` / `coaches` / `seat_layouts` for the schedule and fare class.
- Reuses `query_available_seats` to count unbooked seats (respects `seat_occupies_slot` and cancelled journeys).
- Returns `{total_seats, booked_seats, available_seats}`.

## Example query

```python
from databases.relational import queries as pg
occ = pg.query_schedule_seat_occupancy("NR_SCH01", "2026-06-01", "standard")
# {'schedule_id': 'NR_SCH01', 'travel_date': '2026-06-01', 'fare_class': 'standard',
#  'total_seats': 18, 'booked_seats': 2, 'available_seats': 16}
```

## Agent example

> How many standard seats are available on NR_SCH01 on 2026-06-15?

Expected reply includes total / booked / available counts from PostgreSQL.

## Testing

Run after seeding:

```bash
python3 skeleton/validate_integration.py
python3 -c "from databases.relational import queries as pg; print(pg.query_schedule_seat_occupancy('NR_SCH01','2026-06-01'))"
```
