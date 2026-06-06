# Task 6 Extension — Seat Occupancy & Trip History UI

Team **113403504** optional extension (database + substantial UI).

## Motivation

1. **Capacity questions** — “How full is NR_SCH01 on 2026-06-15?” need aggregated seat counts, not a seat list.
2. **Trip history** — Logged-in users benefit from a scannable bookings table the chat cannot persistently display.

## Files modified or added

| File | Change |
|------|--------|
| `databases/relational/queries.py` | `query_schedule_seat_occupancy(schedule_id, travel_date, fare_class)` |
| `skeleton/agent.py` | Rule-based handler for seat-occupancy chat questions |
| `skeleton/ui.py` | **My Bookings** tab (`query_user_bookings`) + **Seat Capacity** tab (direct DB lookup) |
| `skeleton/validate_integration.py` | Automated test for occupancy query + idempotent booking |
| `TASK6.md` | This manifest |
| `Team113403504_DESIGN_DOC.md` | Section 7 documentation |

## Database operation: `query_schedule_seat_occupancy`

1. Count total seats in `seats` → `coaches` → `seat_layouts` for schedule + fare class.
2. Count available via existing `query_available_seats` (respects active bookings).
3. Return `{total_seats, booked_seats, available_seats}` where `booked = total − available`.

## Example (Python shell)

```python
from databases.relational import queries as pg
pg.query_schedule_seat_occupancy("NR_SCH01", "2026-06-01", "standard")
```

## Example (UI — live demo for TA)

1. `python3 skeleton/ui.py`
2. Open **Seat Capacity** tab → schedule `NR_SCH01`, date `2026-06-01`, class `standard` → **Look up occupancy**
3. Login as `alice.tan@email.com` / `alice1990` → **My Bookings** tab → **Refresh**

## Example (Agent)

> How many standard seats are available on NR_SCH01 on 2026-06-15?

## Testing

```bash
python3 skeleton/validate_integration.py   # includes Task 6 occupancy check
python3 skeleton/ui.py                     # manual UI demo
```
