"""
Deterministic policy answers from train-mock-data JSON (no embeddings required).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from skeleton.config import DATA_DIR


def _load(name: str) -> list | dict:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def search_policy_json(query: str) -> Optional[str]:
    """
    Keyword search over refund_policy, travel_policies, booking_rules, ticket_types.

    Returns formatted text when a confident match is found, else None.
    """
    lower = query.lower()
    hits: list[tuple[int, str]] = []

    # Delay compensation (RF005)
    if any(k in lower for k in ("delay", "compensation", "延誤", "補償")):
        m = re.search(r"(\d+)\s*(?:minutes?|mins?|分鐘)", query, re.I)
        if m:
            minutes = int(m.group(1))
            for p in _load("refund_policy.json"):
                if p.get("policy_id") != "RF005":
                    continue
                for rule in p.get("compensation_rules", []):
                    rid = rule.get("rule_id", "")
                    ok = (
                        (minutes >= 120 and rid == "RF005_R3")
                        or (60 <= minutes < 120 and rid == "RF005_R2")
                        or (30 <= minutes < 60 and rid == "RF005_R1")
                    )
                    if ok:
                        return (
                            f"【{p['policy_id']} — {rid}】\n"
                            f"{rule['condition']}\n"
                            f"Compensation: {rule['compensation']}\n"
                            f"How to claim: {rule.get('how_to_claim', '')}"
                        )

    # RF010 metro online booking
    if any(k in lower for k in ("metro", "捷運")) and any(
        k in lower for k in ("online", "app", "book", "purchase", "訂")
    ):
        for p in _load("refund_policy.json"):
            if p.get("policy_id") == "RF010":
                return f"【{p['policy_id']} — {p['label']}】\n{p.get('summary', p.get('rules', ''))}"

    # Bicycles
    if any(k in lower for k in ("bicycle", "bike", "腳踏車", "自行車")):
        net = "national_rail" if any(k in lower for k in ("rail", "國鐵", "train")) else "metro"
        tp = _load("travel_policies.json")
        bikes = tp.get(net, {}).get("bicycles", {})
        lines = [f"【Bicycle — {net}】"]
        for key in ("foldable_bicycles", "standard_bicycles"):
            b = bikes.get(key, {})
            if b:
                lines.append(
                    f"  {key}: permitted={b.get('permitted')} — "
                    f"{b.get('conditions') or b.get('notes') or b.get('reason', '')}"
                )
        hits.append((10, "\n".join(lines)))

    # Pets
    if any(k in lower for k in ("pet", "dog", "cat", "寵物")):
        net = "national_rail" if any(k in lower for k in ("rail", "國鐵")) else "metro"
        pets = _load("travel_policies.json").get(net, {}).get("pets", {})
        if pets:
            hits.append((9, f"【Pets — {net}】\n{json.dumps(pets, ensure_ascii=False)[:800]}"))

    # Station closure
    if any(k in lower for k in ("closed", "closure", "封閉", "關閉")):
        sc = (
            _load("travel_policies.json")
            .get("metro", {})
            .get("station_closures", {})
        )
        if sc:
            hits.append(
                (
                    8,
                    "【Station closure guidance】\n"
                    f"  Planned: {sc.get('planned_closures', '')}\n"
                    f"  Unplanned: {sc.get('unplanned_closures', '')}\n"
                    f"  Tickets: {sc.get('ticket_handling', '')}",
                )
            )

    # Refund policy id mention
    for p in _load("refund_policy.json"):
        pid = p.get("policy_id", "")
        if pid and pid.lower() in lower:
            label = p.get("label", pid)
            body = p.get("summary") or p.get("rules") or p.get("notes") or str(p)[:600]
            hits.append((7, f"【{pid} — {label}】\n{body}"))

    if hits:
        hits.sort(key=lambda x: -x[0])
        return hits[0][1]
    return None
