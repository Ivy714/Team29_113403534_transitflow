from __future__ import annotations
import json
import re
from datetime import date
from databases.graph import queries as graph
from databases.relational import queries as pg
from skeleton.llm_provider import llm


def _extract_station_ids(text: str) -> list[str]:
    return list(set(re.findall(r"\b(MS\d{2}|NR\d{2})\b", text, re.I)))


def run_agent(
    user_message: str,
    history: list[dict],
    debug: bool = False,
    current_user_email: str = None,
) -> tuple:
    msg = user_message.strip()
    lower_msg = msg.lower()
    ids = _extract_station_ids(msg)
    tool_called = "General LLM"
    db_raw_result = None

    # 1. 處理訂票/取消 (需要 Postgres)
    if current_user_email:
        if "cancel booking" in lower_msg or "取消" in lower_msg:
            match = re.search(r"BK-[A-Z0-9]+", msg, re.I)
            if match:
                tool_called = "pg.execute_cancellation"
                db_raw_result = pg.execute_cancellation(
                    match.group(0), pg.query_user_profile(current_user_email)["user_id"]
                )
        elif "book me" in lower_msg and len(ids) >= 2:
            tool_called = "pg.execute_booking"
            avail = pg.query_national_rail_availability(ids[0], ids[1], "2026-06-01")
            if avail:
                seats = pg.query_available_seats(
                    avail[0]["schedule_id"], "2026-06-01", "standard"
                )
                if seats:
                    db_raw_result = pg.execute_booking(
                        pg.query_user_profile(current_user_email)["user_id"],
                        avail[0]["schedule_id"],
                        ids[0],
                        ids[1],
                        "2026-06-01",
                        "standard",
                        seats[0]["seat_id"],
                    )

    # 2. 處理路線查詢 (需要 Neo4j)
    if db_raw_result is None and len(ids) >= 2:
        if "fastest" in lower_msg:
            tool_called = "graph.query_shortest_route"
            db_raw_result = graph.query_shortest_route(ids[0], ids[1])
        elif "how do i get from" in lower_msg:
            tool_called = "graph.query_shortest_route"
            db_raw_result = graph.query_shortest_route(ids[0], ids[1])

    # 3. 處理班次查詢 (需要 Postgres)
    if (
        db_raw_result is None
        and ("trains run from" in lower_msg or "schedule" in lower_msg)
        and len(ids) >= 2
    ):
        tool_called = "pg.query_national_rail_availability"
        db_raw_result = pg.query_national_rail_availability(
            ids[0], ids[1], "2026-06-01"
        )

    # 4. 處理政策 RAG (需要 pgvector)
    if db_raw_result is None and ("policy" in lower_msg or "compensation" in lower_msg):
        tool_called = "pg.query_policy_vector_search"
        vector = llm.get_embedding(msg)
        db_raw_result = pg.query_policy_vector_search(vector)

    # 將結果整理給 LLM
    debug_text = (
        f"Tool: {tool_called}\nResult: {json.dumps(db_raw_result, default=str)}"
        if debug
        else ""
    )
    system_prompt = (
        f"使用以下資訊回答使用者: {json.dumps(db_raw_result, default=str)}"
        if db_raw_result
        else "回答使用者問題。"
    )

    answer = llm.chat(
        messages=history + [{"role": "user", "content": msg}],
        system_prompt=system_prompt,
    )
    return (
        answer,
        history
        + [{"role": "user", "content": msg}, {"role": "assistant", "content": answer}],
        debug_text,
    )
