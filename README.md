# Claw Studio

> 一个跑在本机的桌面 AI Agent：能读写文件、执行命令、搜索网页、记住项目上下文与使用偏好，并且可以通过微信对话。

Claw Studio 是一个 **Windows 原生桌面客户端 + 本地 FastAPI 运行时** 的智能体框架。它不是聊天壳子——模型拿到的是真实工具：文件系统、Shell、浏览器、记忆库、技能与插件；任务完成后还能主动回来告诉你。

## 特性

- **原生桌面**：pywebview 承载的 C++ 级别窗口（EdgeChromium 内核），无浏览器标签页，无 Electron 体积。
- **云端推理**：OpenAI 兼容多供应商（DeepSeek / OpenRouter / 自建网关），供应商与 API Key 在专门的「API 供应商」页管理，本地 DPAPI 加密存储。
- **真实工具链**：`run_shell` / `file_*` / `browser_control` / 记忆工具 / 技能 / 插件，带三档权限（只读 / 标准 / 完全）与全量审计日志。
- **四层记忆**：L1 核心事实 → L2 项目知识 → L3 经验 → L4 会话记录，自动衰减、冲突检测与提炼。
- **微信对话入口**：内嵌 iLink Bot，直接在微信里和 Agent 收发消息。
- **定时任务**：Cron / 间隔 / 一次性任务，完成后自动唤醒模型汇报。
- **可定制启动动画**：边播动画边并行加载资源。
- **可审阅的操作闸门**：有副作用的操作在你点头之前不会落地。

## 快速开始

```bash
# 1. 后端依赖
pip install -r requirements.txt

# 2. 前端构建
cd web && npm install && npm run build && cd ..

# 3. 启动（源码模式）
python server/main.py
```

首次启动会生成 `config/config.json`（从 `config/config.example.json` 复制）。在「设置 → API 供应商」里填入你的 API Key 即可开始。

> 需要 Windows 10/11 + WebView2 运行时（Win11 自带）。

## 目录结构

```
server/            FastAPI 后端
  main.py          路由与启动
  agent_loop.py    工具调用循环
  providers/       推理供应商（OpenAI 兼容）
  memory/          四层记忆（SQLite）
  tools/           内置工具
  scheduler.py     定时任务
web/src/           React 18 前端
config/            配置模板与模型档案
skills/            技能（外部脚本）
plugins/           插件（进程内工具）
```

## 隐私与数据

这个项目**不收集、不上传任何使用数据**。所有内容都在你自己的机器上：

| 数据 | 位置 | 说明 |
|---|---|---|
| 设置与供应商 | `data/settings.json` | API Key 用 **Windows DPAPI** 加密，只能在同一 Windows 用户下解开 |
| 会话 | `sessions/*.json` | 一个会话一个文件，不上传 |
| 记忆 | `memory/memory.db` | 本地 SQLite，可随时导出为 Markdown |
| 微信桥接 | `bridge/`（`state/`、`secure/`） | 登录凭证只在本机，`secure/` 权限 700 |
| 工具审计 | `logs/tool-audit.jsonl` | 每次工具调用留痕，自动滚动 |

仓库里只提交**源码与配置模板**（`config/config.example.json`）。运行期目录全部在 `.gitignore` 里，
克隆下来是干净的，不会有任何人的会话、记忆、密钥。

## 发布前自检

```bash
python scripts/check_publish.py
```

它扫描 **git 已跟踪的文件**，检查个人路径（`C:/Users/...`）、API Key 形态、邮箱/手机号、
微信 OpenID，以及"不该入库的路径"（`config/config.json`、`data/`、`sessions/`、`*.db`、`*.exe`）。
退出码非 0 说明还有东西没清干净。

> 注意：如果敏感内容**已经 push 过**，删文件不够——历史里仍然能翻到，需要 `git filter-repo` 重写历史，
> 并立刻吊销对应凭证。

## 许可

MIT
