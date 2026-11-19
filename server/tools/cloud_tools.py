# -*- coding: utf-8 -*-
"""云端（WorkBuddy）配套工具：积分查询。"""
from __future__ import annotations

CLOUD_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "wb_credits",
            "description": (
                "WorkBuddy 云端积分：action=query 查余额与本月用量；action=checkin 查每日签到状态"
                "（今天是否已领、可领多少）；action=claim 立即签到领取积分。"
                "用户问'还剩多少积分/额度'用 query，问'今天签到没/领积分'用 checkin 或 claim。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["query", "checkin", "claim"],
                               "description": "默认 query"},
                },
            },
        },
    },
]


def wb_credits(args: dict, workdir: str, cfg: dict) -> str:
    wb = cfg.get("_wb")
    if wb is None:
        return "云端（WorkBuddy）未接入，无法查询积分。"
    action = str(args.get("action") or "query").lower()
    if action in ("checkin", "status"):
        d = wb.checkin_status()
        if not d.get("ok"):
            return f"签到状态查询失败：{d.get('error')}"
        if not d.get("active"):
            return "当前没有进行中的签到活动。"
        if d.get("today_checked_in"):
            return (f"今天已签到（连签 {d.get('streak_days')} 天），无需再领。"
                    f"活动：{d.get('activity_name')}，截止 {d.get('end_time')}")
        return (f"今天还没签到！可领 {d.get('daily_credit')} 积分"
                f"（连签 {d.get('streak_days')} 天，活动截止 {d.get('end_time')}）。说'领取'即执行。")
    if action == "claim":
        r = wb.daily_checkin()
        if not r.get("ok"):
            return f"签到失败：{r.get('error')}（可能今天已领过）"
        return f"签到成功！今日获得 {r.get('credit')} 积分（连签 {r.get('streak_days')} 天）。"
    data = wb.credits()
    accounts = data.get("accounts") or []
    if not accounts:
        return "未查询到积分信息。"

    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    valid = [a for a in accounts if _num(a.get("cycle_size")) > 0]
    if not valid:
        return "未查询到带额度的积分包。"
    alive = [a for a in valid if _num(a.get("cycle_remain")) > 0]
    used = [a for a in valid if _num(a.get("cycle_remain")) <= 0]
    total = round(sum(_num(a.get("cycle_remain")) for a in valid), 2)
    unit = valid[0].get("unit") or "credits"
    lines = [f"可用合计 {total:g} {unit}（{len(alive)} 个可用额度包）"]
    for a in alive:
        cyc = f"，至 {str(a.get('cycle_end') or '')[:10]}" if a.get("cycle_end") else ""
        lines.append(
            f"- {a['package']}：本月剩余 {a.get('cycle_remain')}/{a.get('cycle_size')} {a.get('unit') or unit}{cyc}"
        )
    if used:
        lines.append(f"（另有 {len(used)} 个已用尽的礼包，未计入明细）")
    return "WorkBuddy 积分：\n" + "\n".join(lines)
