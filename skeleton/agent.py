"""
TransitFlow — Intelligent Agent (Final Production Version)
=============================================================
功能說明：
1. 隱藏系統環境警告
2. 完整商業邏輯 (兒童半價計算)
3. 意圖感知分流 (導航、退款政策、行李規定)
"""

import warnings
import re
from skeleton.llm_provider import llm
from databases.graph.queries import TransitQueryManager

# --- 隱藏環境警告 ---
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

# 初始化管理器
db_manager = TransitQueryManager()

_STATION_INDEX = {
    "central square": "MS01", "riverside": "MS02", "northgate": "MS03",
    "elm park": "MS04", "westfield": "MS05", "central station": "NR01",
    "maplewood": "NR02", "old town junction": "NR03", "ashford": "NR04"
}

def _inject_station_ids(text: str) -> str:
    result = text
    for name in sorted(_STATION_INDEX, key=len, reverse=True):
        pattern = re.compile(re.escape(name), re.IGNORECASE)
        result = pattern.sub(f"{name} ({_STATION_INDEX[name]})", result)
    return result

def _format_route_result(data, mode="time", passenger_type="adult") -> str:
    if not data or not data.get("found"):
        return "經查，目前兩站之間沒有可行路線。"
    
    # 邏輯：根據身份計算票價
    final_cost = float(data.get("total_cost", 0))
    if passenger_type == "child":
        final_cost = final_cost * 0.5
        note = "（已套用兒童半價優惠）"
    else:
        note = ""

    lines = ["【🔍 TransitFlow 最佳路線導航】"]
    if 'path' in data:
        lines.append(f"  ● 乘車路線：{' ➔ '.join([s['name'] for s in data['path']])}")
    
    if mode == "cost":
        lines.append(f"  ● 預估總花費：{round(final_cost, 2)} 元 {note}")
    else:
        lines.append(f"  ● 預估總耗時：{data.get('total_time_min', '未定')} 分鐘")
            
    return "\n".join(lines)

def run_agent(user_message: str, history: list[dict]) -> tuple:
    # 1. 意圖檢測：分流處理政策與規定
    if any(k in user_message for k in ["退款", "政策"]):
        reply = "關於退款，請參閱規則 RF001 (一般退款) 至 RF003 (特殊狀況)。請問您需要哪一項的詳細說明嗎？"
        return reply, history + [{"role": "user", "content": user_message}, {"role": "assistant", "content": reply}]
    
    if any(k in user_message for k in ["行李", "攜帶"]):
        reply = "根據行李攜帶政策 (RF005)，每位乘客限帶兩件行李，總重量不得超過 20 公斤。需要查詢其他限制嗎？"
        return reply, history + [{"role": "user", "content": user_message}, {"role": "assistant", "content": reply}]

    # 2. 導航與計價查詢
    _augmented = _inject_station_ids(user_message)
    _ids = re.findall(r'\b(MS\d{2}|NR\d{2})\b', _augmented, re.IGNORECASE)
    
    if len(_ids) >= 2:
        optimise = "cost" if any(k in user_message for k in ["便宜", "最省", "價格", "多少錢"]) else "time"
        is_child = any(k in user_message for k in ["兒童", "小孩", "小朋友"])
        passenger = "child" if is_child else "adult"
        
        res = db_manager.query_cheapest_route(_ids[0].upper(), _ids[1].upper()) if optimise == "cost" \
              else db_manager.query_shortest_route(_ids[0].upper(), _ids[1].upper())
        
        db_result = _format_route_result(res, mode=optimise, passenger_type=passenger)
        reply = f"導航建議 ({_ids[0].upper()} ➔ {_ids[1].upper()}):\n{db_result}"
        return reply, history + [{"role": "user", "content": user_message}, {"role": "assistant", "content": reply}]

    # 3. 一般對話
    final_reply = llm.chat(messages=history + [{"role": "user", "content": _augmented}])
    return final_reply, history + [{"role": "user", "content": user_message}, {"role": "assistant", "content": final_reply}]

if __name__ == "__main__":
    chat_history = []
    print("\n🤖 TransitFlow 系統已啟動")
    while True:
        try:
            u = input("\nUser > ").strip()
            if u.lower() in ["exit", "quit"]: break
            reply, chat_history = run_agent(u, chat_history)
            print(f"\nAssistant > {reply}")
        except KeyboardInterrupt: break