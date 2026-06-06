# Work Allocation Report — Team 113403504

> Submit via EEClass as `Team113403504_WORK_ALLOCATION.md`.

---

## 1. Team Members

| Full Name | Student ID | GitHub Username | Email |
|-----------|-----------|----------------|-------|
| Ying Shiuan | 113403504 | Ivy714 | *(team email)* |
| ssuyu | 113403501 | ssuyuchen | *(team email)* |
| 林昀希 | 113403534 | *(GitHub username)* | *(team email)* |

---

## 2. Task Ownership

### Code Repository

| Task | Primary Owner | Supporting Member(s) | Notes |
|------|--------------|---------------------|-------|
| **Task 1** — Relational schema (`schema.sql`) | 113403501 / 113403504 | All | Merged schema from teammate branches |
| **Task 2a** — Availability & fare queries | 113403504 | 113403501 | Direction-correct NR availability |
| **Task 2b** — Seat & user queries | 113403504 | 113403501 | Includes `query_schedule_seat_occupancy` extension |
| **Task 2c** — Write operations | 113403504 | — | Atomic booking + payment |
| **Task 2d** — Authentication | 113403504 | — | Argon2id hashing |
| **Task 3** — PostgreSQL seeding | 113403501 | 113403504 | `seed_postgres.py` |
| **Task 4** — Neo4j graph & seeding | 113403534 | 113403504 | `seed_neo4j.py`, `seed.cypher` |
| **Task 5** — Neo4j query functions | 113403534 | 113403504 | Six routing functions |
| **Task 6** *(extension)* — Seat occupancy | 113403504 | — | See `TASK6.md` |
| Policy RAG chunks + `seed_vectors.py` | 113403501 | 113403504 | 59 policy chunks |
| Agent + UI integration | 113403504 | 113403534 | Rule-based router + Gradio |

### Design Document

| Section | Primary Author | Supporting Member(s) | Notes |
|---------|--------------|---------------------|-------|
| Section 1 — ER Diagram | 113403504 | 113403501 | |
| Section 2 — Normalisation | 113403501 | 113403504 | |
| Section 3 — Graph Rationale | 113403534 | 113403504 | |
| Section 4 — Vector / RAG | 113403501 | 113403504 | |
| Section 5 — AI Tool Evidence | 113403504 | All | |
| Section 6 — Reflection | 113403504 | All | |
| Section 7 — Extension | 113403504 | — | Seat occupancy |

---

## 3. Estimated Contribution Percentages

| Member | Estimated % | Brief justification |
|--------|-----------|---------------------|
| Ying Shiuan (113403504) | 40% | Schema merge, queries, agent, integration tests, rubric fixes |
| ssuyu (113403501) | 35% | Policy JSON/chunks, pgvector seeding, relational seed data |
| 林昀希 (113403534) | 25% | Neo4j graph design, routing queries, UI reference |
| **Total** | **100%** | |

---

## 4. Mid-Project Changes

| Change | Original plan | Revised plan | Reason |
|--------|--------------|-------------|--------|
| Policy storage | Embed raw JSON at query time | Pre-chunked `policy_chunks.json` | Teammate branch 113403501 delivered structured chunks |
| Password hash | SHA-256 mock for demo | Argon2id | Course static-code rubric requirement |
| Agent scope | LLM-first | Rule-based first, LLM fallback | Reliable README demo queries |

If nothing else changed beyond the above, no further revisions.

---

## 5. Team Declaration

We confirm that this work allocation accurately reflects how responsibilities were divided within our team.

| Name | Signature / Typed name | Date |
|------|----------------------|------|
| Ying Shiuan | | 2026-06-06 |
| ssuyu | | |
| 林昀希 | | |
