# -*- coding: utf-8 -*-
"""办公室运行器：主管拆解 → 员工执行 → 主管验收，消息平铺回主聊天。

架构约定（用户拍板）：
  - 主聊天就是老板办公室的群聊：老板、Claw、主管、员工、框架消息同一条流；
  - 所有角色由 Qoder 驱动（不占本地显存）；
  - 一次 office_dispatch = 一轮「主管拆解 + 员工执行 + 主管验收」；
    无人监管的持续改进由 Claw 的 agent_loop 继续催办（再次 dispatch），
    避免工具调用挂几个小时；
  - 跨部门由主管对主管转接，结果回注老板上下文。

消息不走返回值，而是 POST 到本机 /api/team/message（主聊天 SSE 已支持 sender 渲染）。
"""
from __future__ import annotations

import asyncio
import json
import re
import time

import httpx

from lenient_json import parse_lenient

ROOMS: dict[str, dict] = {
    "dev": {
        "id": "dev",
        "name": "项目开发部",
        "color": "#D9713A",
        "supervisor": "项目主管",
        "staff": ["前端开发员", "全栈设计师", "后端开发", "美术设计"],
    },
    "life": {
        "id": "life",
        "name": "生活部",
        "color": "#2C7F9E",
        "supervisor": "生活主管",
        "staff": ["生活助理", "健康顾问"],
    },
    "memory": {
        "id": "memory",
        "name": "记忆管理部",
        "color": "#6A5A8A",
        "supervisor": "记忆主管",
        "staff": ["记忆整理员"],
    },
}

ASSISTANT = {'name': '老板助理', 'role': '路由', 'color': '#8E6B4A', 'room': '老板办公室', 'room_id': 'office'}
DATA_DIR = None


def set_data_dir(path):
    global DATA_DIR
    DATA_DIR = str(path)
    try:
        load_org()
    except Exception:
        pass


def _org_file():
    import os
    base = DATA_DIR or os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
    return os.path.join(base, 'office_org.json')


def load_org():
    import json, os
    try:
        with open(_org_file(), encoding='utf-8') as f:
            d = json.load(f)
    except (OSError, ValueError):
        return
    rooms = d.get('rooms') or {}
    for rid, staff in rooms.items():
        if rid in ROOMS and isinstance(staff, list):
            ROOMS[rid]['staff'] = [str(x) for x in staff if str(x).strip()]


def save_org():
    import json, os
    os.makedirs(os.path.dirname(_org_file()) or '.', exist_ok=True)
    data = {'rooms': {rid: list(r['staff']) for rid, r in ROOMS.items()}}
    tmp = _org_file() + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _org_file())


def hire(room_id: str, name: str) -> dict:
    room = ROOMS.get(str(room_id or 'dev'))
    if not room:
        raise ValueError('没有这个部门：' + str(room_id))
    n = str(name or '').strip()[:12]
    if not n:
        raise ValueError('员工需要名字')
    if n in room['staff'] or n == room['supervisor']:
        return {'ok': False, 'error': '同名员工已存在', 'staff': room['staff']}
    room['staff'].append(n)
    save_org()
    return {'ok': True, 'room': room['name'], 'staff': room['staff']}


def fire(room_id: str, name: str) -> dict:
    room = ROOMS.get(str(room_id or 'dev'))
    if not room:
        raise ValueError('没有这个部门：' + str(room_id))
    n = str(name or '').strip()
    if n not in room['staff']:
        return {'ok': False, 'error': '没有这个员工', 'staff': room['staff']}
    room['staff'].remove(n)
    save_org()
    return {'ok': True, 'room': room['name'], 'staff': room['staff']}

FRAMEWORK = {"name": "框架", "role": "框架", "color": "#6A5A8A", "room": "框架", "room_id": "framework"}


load_org()


def room_def(room_id: str | None) -> dict:
    return ROOMS.get(str(room_id or "dev")) or ROOMS["dev"]


def _match_staff(name: str, staff: list[str]) -> str:
    """把模型写的员工名对齐到花名册（"前端" → "前端开发员"）。"""
    n = str(name or "").strip()
    if not n:
        return ""
    if n in staff:
        return n
    best = ""
    for s in staff:
        if n in s or s in n:
            if not best or abs(len(s) - len(n)) < abs(len(best) - len(n)):
                best = s
    return best


def _sender(room: dict, name: str, role: str, step: int | None = None, total: int | None = None) -> dict:
    """团队消息发送者标识：部门 + 员工/角色名 + 主题色。

    前端群聊视图据此在每条消息上标注「部门 · 员工」，并按部门着色。
    """
    out = {
        "name": name,
        "role": role,
        "color": room.get("color") or "#D9713A",
        "room": room.get("name") or "",
        "room_id": room.get("id") or "dev",
    }
    if step is not None:
        out["step"] = int(step)
        out["total"] = int(total or step)
    return out


def _clip(text, n: int) -> str:
    s = str(text or "").strip()
    return s if len(s) <= n else s[:n] + "…"


def _extract_json(text: str) -> dict:
    """从模型输出里抠 JSON：代码围栏 → 首尾大括号 → 宽容解析。"""
    s = str(text or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, re.S)
    if m:
        s = m.group(1)
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        pass
    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i:
        try:
            obj = json.loads(s[i:j + 1])
            return obj if isinstance(obj, dict) else {}
        except Exception:
            pass
    try:
        obj, _ = parse_lenient(s)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _supervisor_prompt(room: dict, task: str, workdir: str, permission: str,
                       max_assign: int, handoff_allowed: bool = True) -> str:
    staff = "、".join(room["staff"]) or "（无）"
    handoff = (
        "如果任务明显属于另一个部门，用 handoff 交给对应主管，不要硬做："
        'handoff={"to_room":"dev|life|memory","reason":"...","packet":"交接内容"}。'
        if handoff_allowed else "本轮不要再转接其他部门（handoff 必须为 null）。"
    )
    return (
        f"你是 Claw Agent 的「{room['name']}」主管，管理员工：{staff}。\n"
        f"老板的助理 Claw 转来任务：{task}\n"
        f"工作目录：{workdir}\n权限档位：{permission}（readonly=只读 / standard=沙箱读写 / full=完全）。\n"
        "规则：\n"
        "1. 先用一两句话向老板确认你理解的任务（reply）。\n"
        f"2. 把任务拆成 1-{max_assign} 个员工任务；employee 必须从下列员工名中选：{staff}。\n"
        "   每个 task 写清目标、产出、验收标准；不需要员工时 assignments 留空。\n"
        f"3. {handoff}\n"
        "4. 只输出 JSON，不要代码围栏：\n"
        '{"reply":"...","assignments":[{"employee":"...","task":"..."}],"handoff":null}'
    )


def _employee_prompt(room: dict, employee: str, task: str, workdir: str, permission: str) -> str:
    return (
        f"你是 Claw Agent「{room['name']}」的{employee}。\n"
        f"主管给你的任务：{task}\n"
        f"工作目录：{workdir}\n权限档位：{permission}（只读时不要改文件）。\n"
        "请直接执行（可以用你的文件读写/命令工具），然后简洁给出：\n"
        "1) 实际做了什么（文件路径 / 命令 / 关键改动）\n"
        "2) 结果与验证情况\n"
        "3) 未完成或风险\n"
        "不要扮演主管，不要派活给别人。用中文回答。"
    )


def _synthesis_prompt(room: dict, task: str, results: list[dict], handoff: dict | None) -> str:
    body = []
    for r in results:
        body.append(f"【{r['employee']}】任务：{r['task']}\n结果：{r['result']}")
    if handoff:
        body.append("【转接结果】" + json.dumps(handoff, ensure_ascii=False))
    return (
        f"你是「{room['name']}」主管。老板任务：{task}\n"
        "员工执行结果：\n" + ("\n\n".join(body) if body else "（本轮没有派员工）") + "\n\n"
        "请验收并给老板一份汇报：结论（完成/部分完成/未完成）、关键产物路径、验证情况、仍需改进或下一步。\n"
        "只输出 JSON，不要代码围栏：\n"
        '{"report":"...","done":true,"next_actions":["..."]}'
    )


def _handoff_prompt(target: dict, source: dict, reason: str, packet: str, workdir: str,
                    permission: str, max_assign: int) -> str:
    staff = "、".join(target["staff"]) or "（无）"
    return (
        f"你是「{target['name']}」主管。另一个部门（{source['name']}）把任务转给你。\n"
        f"转接原因：{reason}\n交接内容：{packet}\n"
        f"工作目录：{workdir}\n权限档位：{permission}。\n"
        f"你的员工：{staff}。\n"
        "请先给老板一句确认（reply），再按你的职责拆解员工任务；本轮不要再转接其他部门。\n"
        "只输出 JSON："
        "{\"reply\":\"...\",\"assignments\":[{\"employee\":\"...\",\"task\":\"...\"}],\"handoff\":null}"
        f"（最多 {max_assign} 个员工任务）"
    )


async def _ask(qd, prompt: str, *, workdir: str, permission: str, model: str,
               timeout: float) -> str:
    return await asyncio.wait_for(
        qd.complete(model=model, prompt=prompt, permission=permission,
                    cwd=workdir, use_mcp=False),
        timeout=max(30.0, float(timeout or 300)),
    )


def _qoder_ready(qd) -> bool:
    try:
        st = qd.status() or {}
        return bool(st.get("logged_in"))
    except Exception:
        return True


def _http_poster(port: int, session_id: str, room_id: str):
    base = f"http://127.0.0.1:{int(port)}"

    async def post(sender: dict, text: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                await c.post(base + "/api/team/message", json={
                    "text": str(text or ""),
                    "sender": sender,
                    "session_id": session_id,
                    "team_id": room_id,
                })
        except Exception:
            pass

    return post


async def _route_room(qd, task: str, workdir: str, model: str, timeout: int) -> dict:
    """老板助理只做路由：判断任务归哪个部门。"""
    prompt = (
        "你是老板助理，只做路由，不执行任务。可选部门：\n"
        "- dev 项目开发部：写代码、改文件、前端/后端/美术/工程实现\n"
        "- life 生活部：生活安排、健康、日程、提醒\n"
        "- memory 记忆管理部：记忆整理、知识归档、检索\n"
        f"任务：{task}\n只输出 JSON：{{\"room\":\"dev|life|memory\",\"reason\":\"一句话\"}}"
    )
    try:
        text = await _ask(qd, prompt, workdir=workdir, permission="default", model=model, timeout=min(timeout, 90))
    except Exception:
        return {"room": "dev", "reason": "路由失败，默认开发部"}
    plan = _extract_json(text) or {}
    rid = str(plan.get("room") or "")
    return {"room": rid if rid in ROOMS else "dev", "reason": str(plan.get("reason") or "")}



async def plan_dispatch(*, qd, cfg: dict, session_id: str, task: str, room_id: str,
                        workdir: str, permission: str, post=None) -> dict:
    """只做「路由 + 主管拆解 + 派活」，立即返回，不执行员工任务。"""
    office_cfg = cfg.get("_office") or {}
    model = str(office_cfg.get("model") or "qoder:Auto")
    timeout = float(office_cfg.get("timeout") or 300)
    max_assign = max(1, min(8, int(office_cfg.get("max_assignments") or 4)))

    rid = str(room_id or 'auto')
    if rid.lower() in ('', 'auto'):
        routed = await _route_room(qd, task, workdir, model, timeout)
        rid = routed['room']
        if post is None:
            post = _http_poster(cfg.get("_self_port") or 8090, session_id, rid)
        await post(ASSISTANT, f"路由到「{ROOMS[rid]['name']}」：{routed['reason'] or '按任务归属'}")
    room = room_def(rid)
    if post is None:
        post = _http_poster(cfg.get("_self_port") or 8090, session_id, room["id"])

    sup = room["supervisor"]
    prompt = _supervisor_prompt(room, _clip(task, 3000), workdir, permission, max_assign,
                                handoff_allowed=False)
    text = await _ask(qd, prompt, workdir=workdir, permission=permission, model=model, timeout=timeout)
    plan = _extract_json(text) or {}
    reply = str(plan.get("reply") or "").strip()
    if reply:
        await post(_sender(room, sup, "主管"), _clip(reply, 1200))

    items = []
    for a in (plan.get("assignments") or [])[:max_assign]:
        if not isinstance(a, dict):
            continue
        emp = _match_staff(a.get("employee"), room["staff"])
        t = str(a.get("task") or "").strip()
        if emp and t:
            items.append({"employee": emp, "task": t})
    if not items:
        items = [{"employee": room["staff"][0], "task": task}]
    for i, it in enumerate(items, 1):
        await post(_sender(room, sup, "主管", i, len(items)), f"派给{it['employee']}：{_clip(it['task'], 120)}")
    return {"room": room["id"], "room_name": room["name"], "assignments": items}


async def talk(*, qd, cfg: dict, session_id: str, room_id: str, to: str, text: str,
               workdir: str, permission: str, post=None) -> str:
    """老板助理与某个成员对话（一次调用一次回复）：to=主管 / 员工名。"""
    office_cfg = cfg.get("_office") or {}
    model = str(office_cfg.get("model") or "qoder:Auto")
    timeout = float(office_cfg.get("timeout") or 300)
    room = room_def(room_id)
    if post is None:
        post = _http_poster(cfg.get("_self_port") or 8090, session_id, room["id"])

    name = str(to or "").strip()
    if name in ("主管", "上级", room["supervisor"]):
        name, role = room["supervisor"], "主管"
    else:
        emp = _match_staff(name, room["staff"]) or room["staff"][0]
        name, role = emp, "员工"

    prompt = (f"你是「{room['name']}」的{name}（{role}）。老板助理对你说：{text}\n"
              f"直接在对话里回应并把结果写清楚（结论先行，给出产物路径）。")
    reply = await _ask(qd, prompt, workdir=workdir, permission=permission, model=model,
                       timeout=min(timeout, 150))
    reply = _clip(reply, 4000)
    await post(_sender(room, name, role), reply)
    return reply

async def run_office_cycle(*, qd, cfg: dict, session_id: str, task: str, room_id: str,
                           workdir: str, permission: str, mode: str = "onduty",
                           deadline: float = 0.0, post=None) -> str:
    """执行一轮办公室协作，返回给 Claw 的文本摘要。"""
    office_cfg = cfg.get("_office") or {}
    model = str(office_cfg.get("model") or "qoder:Auto")
    timeout = float(office_cfg.get("timeout") or 300)
    max_assign = max(1, min(8, int(office_cfg.get("max_assignments") or 4)))
    max_review = max(0, min(3, int(office_cfg.get("max_review_rounds") or 1)))
    if post is None:
        post = _http_poster(cfg.get("_self_port") or 8090, session_id, str(room_id or 'dev'))
    if str(room_id or '').lower() in ('', 'auto'):
        routed = await _route_room(qd, task, workdir, model, timeout)
        room_id = routed['room']
        await post(ASSISTANT, f"路由到「{ROOMS[room_id]['name']}」：{routed['reason'] or '按任务归属'}")
    room = room_def(room_id)

    supervisor = room["supervisor"]
    sup_sender = _sender(room, supervisor, "主管")

    def deadline_hit() -> bool:
        return bool(deadline) and time.time() >= deadline

    if deadline_hit():
        msg = "无人监管窗口已结束：主管不再接新任务。"
        await post(FRAMEWORK, msg)
        return msg

    if not await asyncio.to_thread(_qoder_ready, qd):
        msg = "Qoder 未登录或不可用：办公室暂时无法派活。请先在设置里登录 Qoder。"
        await post(FRAMEWORK, msg)
        return msg

    async def plan_and_execute(task_text: str, allow_handoff: bool) -> tuple[str, list[dict], dict | None]:
        prompt = _supervisor_prompt(room, _clip(task_text, 4000), workdir, permission,
                                    max_assign, handoff_allowed=allow_handoff)
        text = await _ask(qd, prompt, workdir=workdir, permission=permission,
                          model=model, timeout=timeout)
        plan = _extract_json(text)
        reply = str(plan.get("reply") or text or "").strip()
        if reply:
            await post(sup_sender, _clip(reply, 4000))

        handoff = plan.get("handoff") if isinstance(plan.get("handoff"), dict) else None
        handoff_result = None
        if handoff and allow_handoff:
            target_id = str(handoff.get("to_room") or "")
            target = ROOMS.get(target_id)
            if target and target_id != room["id"]:
                reason = str(handoff.get("reason") or "")
                packet = str(handoff.get("packet") or "")
                await post(FRAMEWORK, f"主管转接：{room['name']} → {target['name']}（{reason or '任务归属'}）")
                htext = await _ask(
                    qd,
                    _handoff_prompt(target, room, reason, _clip(packet, 3000), workdir,
                                    permission, max_assign),
                    workdir=workdir, permission=permission, model=model, timeout=timeout,
                )
                hplan = _extract_json(htext)
                hreply = str(hplan.get("reply") or htext or "").strip()
                if hreply:
                    await post(_sender(target, target["supervisor"], "主管"), _clip(hreply, 4000))
                hresults = await run_assignments(target, hplan.get("assignments") or [])
                handoff_result = {
                    "target": target["name"],
                    "reply": _clip(hreply, 1200),
                    "results": hresults,
                }

        results = await run_assignments(room, plan.get("assignments") or [])
        if deadline_hit():
            await post(FRAMEWORK, "本轮时间预算用完，先汇总已完成的项。")
        return reply, results, handoff_result

    async def run_assignments(target_room: dict, assignments: list) -> list[dict]:
        """员工任务并行执行（最多 4 个同时跑），谁先完成谁先发言。"""
        out: list[dict] = []
        if not isinstance(assignments, list):
            return out
        items = [a for a in assignments[:max_assign] if isinstance(a, dict)]
        if not items:
            return out
        sem = asyncio.Semaphore(4)
        total = len(items)
        await post(FRAMEWORK, f"并行执行 {total} 项（同时最多 4 个）")

        async def run_one(idx: int, a: dict):
            if deadline_hit():
                await post(FRAMEWORK, "时间预算到，跳过部分任务。")
                return None
            raw_name = str(a.get("employee") or "").strip()
            emp = _match_staff(raw_name, target_room["staff"])
            emp_task = str(a.get("task") or "").strip()
            if not emp:
                emp = target_room["staff"][0]
                await post(FRAMEWORK, f"名单里没有「{raw_name or '空'}」，改派给{emp}")
            if not emp_task:
                return None
            async with sem:
                try:
                    text = await _ask(
                        qd,
                        _employee_prompt(target_room, emp, _clip(emp_task, 3000), workdir, permission),
                        workdir=workdir, permission=permission, model=model, timeout=min(timeout, 120),
                    )
                except Exception as e:
                    text = f"执行失败：{type(e).__name__}: {e}"
                text = _clip(text, 6000)
            await post(_sender(target_room, emp, "员工", idx, total), text)
            return {"employee": emp, "task": emp_task, "result": text}

        gathered = await asyncio.gather(*[run_one(i, a) for i, a in enumerate(items, 1)], return_exceptions=True)
        for r in gathered:
            if isinstance(r, dict):
                out.append(r)
        return out

    async def synthesize(task_text: str, results: list[dict], handoff_result: dict | None) -> tuple[str, bool, list[str]]:
        prompt = _synthesis_prompt(room, _clip(task_text, 3000), results, handoff_result)
        text = await _ask(qd, prompt, workdir=workdir, permission=permission,
                          model=model, timeout=timeout)
        syn = _extract_json(text)
        report = str(syn.get("report") or text or "").strip()
        done = bool(syn.get("done", True))
        next_actions = syn.get("next_actions") if isinstance(syn.get("next_actions"), list) else []
        if report:
            await post(sup_sender, _clip(report, 6000))
        return report, done, [str(x) for x in next_actions if str(x).strip()]

    await post(FRAMEWORK, f"Claw 把任务转给「{room['name']}」主管，团队开始协作。")

    try:
        reply, results, handoff_result = await plan_and_execute(task, allow_handoff=True)
        report, done, next_actions = await synthesize(task, results, handoff_result)

        # 无人监管：窗口没结束且验收仍有改进点 → 再跑一轮返工（最多 max_review 轮）
        review_rounds = 0
        while (mode == "unattended" and not deadline_hit() and review_rounds < max_review
               and (not done or next_actions)):
            review_rounds += 1
            focus = "；".join(next_actions[:6]) or "继续自检：测试、边界、性能、稳定性、文档与清理"
            await post(FRAMEWORK, f"无人监管：窗口未结束，主管开始第 {review_rounds} 轮验收返工。")
            _, results2, handoff2 = await plan_and_execute(f"验收返工：{focus}", allow_handoff=False)
            report, done, next_actions = await synthesize(f"验收返工：{focus}", results2, handoff2)
            if done and not next_actions:
                await post(FRAMEWORK, "无人监管：主管验收通过；窗口内继续保持待命。")
                break
    except asyncio.TimeoutError:
        msg = "办公室执行超时：主管/员工本轮没有在时限内返回。可以再发一次任务继续。"
        await post(FRAMEWORK, msg)
        return msg
    except Exception as e:
        msg = f"办公室执行失败：{type(e).__name__}: {e}"
        await post(FRAMEWORK, msg)
        return msg

    lines = [f"办公室「{room['name']}」一轮结束。", f"主管汇报：{report}"]
    if handoff_result:
        lines.append(f"已转接「{handoff_result['target']}」：{handoff_result['reply']}")
    for r in results:
        lines.append(f"- {r['employee']}：{_clip(r['result'], 800)}")
    if next_actions:
        lines.append("下一步：" + "；".join(next_actions[:6]))
    lines.append("主管和员工的消息已经平铺到老板聊天里。")
    return "\n".join(lines)
