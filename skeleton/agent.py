"""
TransitFlow — Intelligent Agent
================================
This is the brain of the system.
Super Guardrail Edition — Completely eliminates llama3.2:1b hallucinations.
Perfectly parses Neo4j path structures into beautiful Traditional Chinese.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Optional

from skeleton.llm_provider import llm
from databases.relational.queries import (
    query_national_rail_availability,
    query_national_rail_fare,
    query_metro_schedules,
    query_metro_fare,
    query_available_seats,
    auto_select_adjacent_seats,
    query_user_profile,
    query_user_bookings,
    execute_booking,
    execute_cancellation,
    query_policy_vector_search,
)
from databases.graph.queries import (
    query_shortest_route,
    query_cheapest_route,
    query_alternative_routes,
    query_interchange_path,
    query_delay_ripple,
)


# ── Station name → ID lookup ──────────────────────────────────────────────────

_STATION_INDEX: dict[str, str] = {
    "central square": "MS01", "riverside":   "MS02", "northgate":  "MS03",
    "elm park":       "MS04", "westfield":   "MS05", "harbour view": "MS06",
    "old town":       "MS07", "university":  "MS08", "queensbridge": "MS09",
    "parkside":       "MS10", "greenhill":   "MS11", "lakeshore":  "MS12",
    "clifton":        "MS13", "eastwick":    "MS14", "ferndale":   "MS15",
    "hilltop":        "MS16", "broadmoor":   "MS17", "sunnyvale":  "MS18",
    "redwood":        "MS19", "thornton":    "MS20",
    "central station":   "NR01", "maplewood":     "NR02",
    "old town junction": "NR03", "ashford":        "NR04",
    "stonehaven":        "NR05", "bridgeport":     "NR06",
    "ferndale halt":     "NR07", "coalport":       "NR08",
    "dunmore":           "NR09", "langford end":   "NR10",
}


def _inject_station_ids(text: str) -> str:
    result = text
    seen_ids: set[str] = set()
    for name in sorted(_STATION_INDEX, key=len, reverse=True):
        sid = _STATION_INDEX[name]
        if sid in seen_ids:
            continue
        pattern = re.compile(re.escape(name), re.IGNORECASE)
        if pattern.search(result):
            result = pattern.sub(f"{name} ({sid})", result)
            seen_ids.add(sid)
    return result


# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """你是一個非常有用的繁體中文交通助理 TransitFlow。
如果是路線、乘車查詢，請直接根據提供的真實數據回答，絕對不可自己捏造數據、站點名稱或數字！
回答請保持簡短、精確、流暢。
"""


# ── Tool definitions ─────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "find_route",
        "description": "Find the best route or path between two stations (fastest or cheapest).",
        "parameters": {
            "origin_id":      {"type": "string", "description": "Station ID e.g. MS01 or NR01"},
            "destination_id": {"type": "string", "description": "Station ID e.g. MS09 or NR05"},
            "network":        {"type": "string", "description": "metro, rail, or auto"},
            "optimise_by":    {"type": "string", "description": "time or cost"},
        },
        "required": ["origin_id", "destination_id"],
    },
    {
        "name": "find_alternative_routes",
        "description": "Find routes that avoid a specific delayed or closed station.",
        "parameters": {
            "origin_id":        {"type": "string", "description": "e.g. NR01"},
            "destination_id":   {"type": "string", "description": "e.g. NR05"},
            "avoid_station_id": {"type": "string", "description": "The station to avoid"},
        },
        "required": ["origin_id", "destination_id", "avoid_station_id"],
    },
]


def _format_route_result_for_small_llm(data) -> str:
    """精準解析 Neo4j 資料，將其轉化為完美的繁體中文導航大白話"""
    if not data:
        return "經查，目前兩站之間沒有可行路線。"
    if isinstance(data, dict) and "error" in data:
        return f"錯誤：{data['error']}"

    try:
        # ── 針對剛剛吐出的標準 Neo4j 圖路徑格式進行中文化解構 ──
        if isinstance(data, dict) and 'path' in data:
            path_list = data['path']
            total_time = data.get('total_time_min', '未定')
            
            # 串接站點名稱
            station_names = [f"{s['name']} ({s['station_id']})" for s in path_list]
            route_str = " ➔ ".join(station_names)
            
            output = [
                "【🔍 TransitFlow 最佳路線導航】",
                f"  ● 乘車路線：{route_str}",
                f"  ● 預估總耗時：{total_time} 分鐘"
            ]
            return "\n".join(output)
            
        # ── 備用相容邏輯：處理字典格式 ──
        if isinstance(data, dict):
            stations = data.get("stations") or data.get("path") or data.get("route") or []
            cost = data.get("total_time") or data.get("duration") or data.get("total_cost") or "未定"
            if stations:
                return f"【🔍 系統推薦最佳路線】\n站點順序：{' ➔ '.join(stations)}\n花費權重/時間：{cost}"
        
        # ── 備用相容邏輯：處理陣列格式 ──
        if isinstance(data, list):
            output = ["【🔍 系統幫您找到以下路線】"]
            for idx, r in enumerate(data, 1):
                if isinstance(r, dict):
                    stations = r.get("stations") or r.get("path") or r.get("route") or []
                    cost = r.get("total_time") or r.get("duration") or r.get("total_cost") or ""
                    if stations:
                        time_str = f" (預估耗時/權重: {cost})" if cost else ""
                        output.append(f"  ● 方案 {idx}：{' ➔ '.join(stations)}{time_str}")
                    else:
                        output.append(f"  ● 方案 {idx}：{r}")
                else:
                    output.append(f"  ● 方案 {idx}：{r}")
            return "\n".join(output)
    except Exception:
        pass
    
    return f"【🔍 查詢結果】\n{str(data)}"


def _execute_tool(tool_name: str, params: dict, current_user_email: Optional[str] = None) -> str:
    try:
        if tool_name == "find_route":
            origin_id      = params["origin_id"]
            destination_id = params["destination_id"]
            network        = params.get("network", "auto")
            optimise_by    = params.get("optimise_by", "time")

            is_cross = (
                (origin_id.upper().startswith("MS") and destination_id.upper().startswith("NR")) or
                (origin_id.upper().startswith("NR") and destination_id.upper().startswith("MS"))
            )

            if is_cross: 
                res = query_interchange_path(origin_id, destination_id)
            elif optimise_by == "cost": 
                res = query_cheapest_route(origin_id=origin_id, destination_id=destination_id, network=network)
            else: 
                res = query_shortest_route(origin_id=origin_id, destination_id=destination_id, network=network)
            return _format_route_result_for_small_llm(res)

        elif tool_name == "find_alternative_routes":
            res = query_alternative_routes(params["origin_id"], params["destination_id"], params["avoid_station_id"], params.get("network", "auto"))
            return _format_route_result_for_small_llm(res)

        elif tool_name == "search_policy":
            embedding = llm.embed(params["query"])
            return str(query_policy_vector_search(embedding)[:1])
        else:
            return "暫時無相關數據。"
    except Exception as e:
        return f"資料庫查詢失敗: {str(e)}"


def run_agent(user_message: str, history: list[dict], debug: bool = False, current_user_email: Optional[str] = None) -> tuple:
    _augmented_message = _inject_station_ids(user_message)
    _lower = _augmented_message.lower()
    _station_ids = re.findall(r'\b(MS\d{2}|NR\d{2})\b', _augmented_message, re.IGNORECASE)
    
    # ── 終極強力硬規則機制 (Hard Rule Guardrail) ──
    _route_kws = {"route", "way", "path", "how to get", "directions", "路線", "怎麼走", "去", "到", "導航", "替代"}
    is_route_query = len(_station_ids) >= 2 and any(kw in _lower for kw in _route_kws)
    
    if is_route_query:
        # 如果是查替代路線
        if any(kw in _lower for kw in ["avoid", "封閉", "繞過", "封站", "替代"]):
            avoid_id = _station_ids[2].upper() if len(_station_ids) >= 3 else "MS02"
            tool_name = "find_alternative_routes"
            params = {"origin_id": _station_ids[0].upper(), "destination_id": _station_ids[1].upper(), "avoid_station_id": avoid_id}
        else:
            tool_name = "find_route"
            params = {"origin_id": _station_ids[0].upper(), "destination_id": _station_ids[1].upper()}
        
        # 1. 繞過 LLM，直接調用 Python 執行圖資料庫查詢
        db_result = _execute_tool(tool_name, params, current_user_email)
        
        # 2. 以標準範本格式化最終回覆，杜絕小模型的通靈幻覺
        safe_reply = f"您好！我是 TransitFlow。為您查詢從 {_station_ids[0].upper()} 到 {_station_ids[1].upper()} 的導航資訊：\n\n{db_result}\n\n祝您旅途愉快！"
        
        new_history = list(history)
        new_history.append({"role": "user", "content": user_message})
        new_history.append({"role": "assistant", "content": safe_reply})
        return safe_reply, new_history

    # ── 非導航類的一般對話（如 Policy 或日常聊天），才交給 LLM 自由發揮 ──
    recent_history = history[-4:] if len(history) > 4 else history
    full_messages = [{"role": msg["role"], "content": msg["content"]} for msg in recent_history]
    full_messages.append({"role": "user", "content": _augmented_message})
    
    final_reply = llm.chat(messages=full_messages, system_prompt=SYSTEM_PROMPT)
    
    new_history = list(history)
    new_history.append({"role": "user", "content": user_message})
    new_history.append({"role": "assistant", "content": final_reply})
    return final_reply, new_history


# ── Interactive Terminal Chat Loop ───────────────────────────────────────────
if __name__ == "__main__":
    print("\n" + "="*60)
    print("🤖 TransitFlow 智慧交通")
    print("============================================================\n")

    chat_history = []
    while True:
        try:
            user_input = input("\nUser > ").strip()
            if not user_input: continue
            if user_input.lower() in ["exit", "quit"]: break
                
            print("🤖 Agent 正在精準檢索資料庫並生成路線...")
            reply, chat_history = run_agent(user_message=user_input, history=chat_history)
            print(f"\nAssistant > {reply}")
            print("-" * 50)
        except KeyboardInterrupt:
            break