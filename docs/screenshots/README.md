# Task 6 — Screenshot assets for Design Doc Section 7

Place PNG files here, then commit so `Team113403504_DESIGN_DOC.md` image links resolve on GitHub.

## Required files

| Filename | What to capture |
|----------|-----------------|
| `task6_my_bookings.png` | **My Bookings** tab after login as `alice.tan@email.com` / `alice1990` — table shows NR + metro rows |
| `task6_seat_capacity.png` | **Seat Capacity** tab — schedule `NR_SCH01`, date `2026-06-01`, class `standard`, after **Look up occupancy** |
| `task6_chat_seats.png` | *(optional)* **Chat** tab — question “How many seats are available on NR_SCH01 on 2026-06-15?” with agent reply |

## How to capture (macOS)

1. `docker compose up -d` and run all three seed scripts.
2. `python3 skeleton/ui.py` → open http://127.0.0.1:7860
3. **Cmd + Shift + 4** → drag to capture each tab.
4. Save files with the exact names above into this folder.

## Verify

Open `Team113403504_DESIGN_DOC.md` in GitHub preview — all three `![](docs/screenshots/...)` images should render.
