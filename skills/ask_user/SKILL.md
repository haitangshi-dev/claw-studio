# ask_user —— 提问（主界面卡片）

把「问用户」做进 **Claw Agent 主界面**：聊天区（输入框上方）浮出一张卡片，
用户点选项、或自己写一句，答案回到调用者，**同一轮对话不中断、不弹系统窗口**。

---

## 一、两种入口，同一条通道

| 入口 | 怎么用 | 什么时候用 |
|---|---|---|
| **插件工具** `ask_user` | 模型直接调用工具 | 首选。我自己觉得需要你拍板时 |
| **本技能** `skill_run(name="ask_user", args={...})` | stdin 传 JSON | 你说「用技能问」；或脚本/流程里需要人工确认 |

两者写的是同一个文件、走的是同一组端点，所以**界面表现完全一致**：

```
写 <DATA_ROOT>/.claw_ask/<id>.json   status=pending
        │
        │  主界面每 1.2 秒轮询  GET /api/ask/pending
        ▼
   聊天区浮出卡片：徽标「需要你确认」+ 倒计时 + 选项按钮 + 自拟框 + 忽略/选择一项/提交
        │
        │  用户点选 → POST /api/ask/answer        （点忽略 → POST /api/ask/skip）
        ▼
   提问文件 status=answered + answer 落盘
        │
        ▼
   调用方（插件线程 / 本脚本）读到答案 → 返回给模型，同轮对话继续
```

---

## 二、参数（stdin JSON）

```json
{
  "question": "登录失败时用哪种策略？",
  "options": ["直接报错退出", {"label": "重试 3 次再报错", "desc": "更稳但慢一点"}],
  "allow_custom": true,
  "timeout": 90,
  "port": 8090,
  "data_root": null
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `question` | ✅ | 要问的问题，一句话说清 |
| `options` | | 2–6 个候选。字符串，或 `{"label":"选项","desc":"简短说明"}`；也接受 `"甲;乙"` 这种分号串 |
| `allow_custom` | | 是否允许自拟（默认 `true`） |
| `timeout` | | 等界面作答的秒数。默认 90，`0` = 一直等，上限 600。**注意 `skill_run` 自身约 120 秒上限，别填太大** |
| `port` | | 后端端口，默认 `8090`（一般不用填） |
| `data_root` | | 数据根目录（`.claw_ask` 的落点），一般不用填 |

### data_root 自动探测顺序

技能脚本是**独立子进程**，拿不到框架的 `_project_root`，所以按下面顺序找：

1. `args.data_root` / `root` / `project_root`
2. 环境变量 `CLAW_DATA_ROOT`
3. 环境变量 `CLAW_SKILL_DIR` 的**上两级**（`<ROOT>/skills/<name>` → `<ROOT>`）
4. `%LOCALAPPDATA%\Programs\ClawAgent`（安装版默认位置）
5. 当前工作目录

---

## 三、输出（stdout，纯文本）

成功（用户作答）：

```
用户已作答：重试 3 次再报错
（提交时间 2026-10-10 18:35:42）
```

兜底（后端连不上 / 用户点忽略 / 超时）：

```
用户没有在 90 秒内作答。

需要你确认一下：登录失败时用哪种策略？

  1) 直接报错退出
  2) 重试 3 次再报错 —— 更稳但慢一点

直接回我一个序号（比如 2），或者自己写一句你的想法都行。
```

把这段贴进回复，让用户回序号即可 —— **任何情况下都不会卡住会话**。

---

## 四、后端依赖

`server/main.py` 的四个部件：

| 名字 | 作用 |
|---|---|
| `ASK_DIR = DATA_ROOT/.claw_ask` | 提问文件目录 |
| `GET /api/ask/pending` | 界面轮询取待答提问，带 `expires_in` 倒计时；过期自动标 `expired` |
| `POST /api/ask/answer` | 提交答案（`{id, answer}`） |
| `POST /api/ask/skip` | 点「忽略」 |

提问文件字段：

```json
{
  "id": "s261010-183504966",
  "question": "…",
  "options": [{"label": "…", "desc": "…"}],
  "allow_custom": true,
  "status": "pending",          // pending | answered | skipped | expired
  "created_ts": 1791628504.13,
  "created_at": "2026-10-10 18:35:04",
  "ttl": 300,
  "answer": "…",                // status=answered 时有
  "answered_at": "2026-10-10 18:35:42",
  "source": "skill:ask_user"    // 或 "ask_user"（插件）
}
```

> 旧版框架没有这三个端点 → 脚本探测失败会直接走文字兜底，不会报错。

---

## 五、自测

```bash
# 终端里跑（会在界面浮出卡片，点了之后终端打印答案）
echo '{"question":"选哪个？","options":["甲","乙"]}' | python run.py

# Windows 下中文不乱码：
echo '{"question":"选哪个？","options":["甲","乙"]}' | PYTHONIOENCODING=utf-8 python run.py

# 测兜底（指向没人监听的端口，应输出文字清单）
echo '{"question":"选哪个？","options":["甲","乙"],"port":18099}' | python run.py
```

也可直接在对话里说「用技能问我一件事」，我会调 `skill_run`。

---

## 六、历史

1. **v1（10-10 上午）**：tkinter 原生弹窗（独立系统窗口）→ 用户反馈「太丑、不要弹窗」。
2. **v2（10-10 中午）**：改为纯文字列选项清单。
3. **v3（10-10 晚）**：**做进主界面** —— 聊天区浮卡片，点选即答；后端加 `/api/ask/*` 三端点，插件 `ask_user` 改走该通道。
4. **v4（10-10 18:35，当前）**：同一通道封装成**技能**（双入口），三层兜底 + data_root 自动探测，实测卡片通道与文字兜底均通过。
