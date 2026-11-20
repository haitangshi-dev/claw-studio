# Claw Studio

> 一个跑在本机的桌面 AI Agent：能读写文件、执行命令、搜索网页、记住项目上下文与使用偏好，并且可以通过微信对话。

**Claw Studio 是我之前发布的 [MY_AGENT](https://github.com/haitangshi-dev) 的全面升级版。**
它保留了「本地优先、纯云端推理」的核心思路，但在桌面体验、工具链、记忆系统和远程交互上做了彻底重构——
从 Electron 换成更轻的 pywebview 原生窗口，从单模型单通道换成多供应商热切换，
从「能聊天」进化成「能真正把活干完」。

它不是聊天壳子——模型拿到的是真实工具：文件系统、Shell、浏览器、记忆库、技能与插件；任务完成后还能主动回来告诉你。

## 项目特点

### 🖥️ 更轻的原生桌面
pywebview 承载的 Windows 原生窗口（EdgeChromium / WebView2 内核），无浏览器标签页、无 Electron 的臃肿体积。启动快、占用低，还能自定义启动动画（边播边并行加载资源）。

### ☁️ 灵活的多供应商云端推理
OpenAI 兼容的任意供应商（DeepSeek / OpenRouter / 自建网关都行）可在设置页随时增删、热切换：
- **全自动模型抓取**：填完 Base URL + Key 自动拉 `/models`，还能自动识别上下文长度（如 DeepSeek V4 的 1M）与多模态能力，也支持手动填自定义模型名
- **Key 本地加密**：API Key 用 Windows DPAPI 加密存储，只有当前 Windows 用户能解开
- **多模态图片直传**：支持图片的模型直接收 `image_url` 内容块，不再降级成文字描述

### 🛠️ 真正能干活的工具链
`run_shell` / `file_*` / `browser_control` / 记忆工具 / 技能 / 插件一应俱全，带三档权限（只读 / 标准 / 完全）与全量审计日志。有副作用的操作会先弹闸门等你点头，不会偷偷落地。

### 🧠 四层记忆系统
L1 核心事实 → L2 项目知识 → L3 经验 → L4 会话记录，自动衰减、冲突检测与提炼。它记得你的项目上下文和使用偏好，跨会话持续积累。

### 💬 不止在电脑前——微信也能聊
内嵌 iLink Bot 桥接，直接在微信里和 Agent 收发消息。配合定时任务（Cron / 间隔 / 一次性），任务完成后自动唤醒模型，主动把结果汇报到微信。

### 🔒 隐私优先
不收集、不上传任何使用数据。所有会话、记忆、密钥都留在你自己的机器上（详见下方「隐私与数据」）。仓库只提交源码与配置模板，克隆下来是干净的。

## 运行需求与模型选型

Claw Studio 内置 **32 个工具**（Shell、文件读写/搜索、记忆管理、浏览器控制、技能与插件管理等）。这些工具由模型通过 **function calling** 调用，所以选型时可以多留意模型的**工具调用准确率**与**指令遵循能力**——下面列出一些公开跑分，供你按自己的使用场景参考。

### 两种运行模式

| 模式 | 硬件要求 | 说明 |
|---|---|---|
| **云端模式**（推荐） | 几乎无要求，能上网即可 | 在设置页填任意 OpenAI 兼容供应商的 Key（DeepSeek / OpenRouter / 阿里云百炼等），模型在云端跑 |
| **本地模式** | 取决于模型参数量 | 模型在本机推理，需要 GPU 显存；支持 llama.cpp / Ollama / LM Studio（GGUF） |

### Qwen3.5 中小参数模型选型（数据来自 Artificial Analysis / OpenRouter，截至 2026-11）

| 模型 | 参数 | 上下文 | 工具调用 BFCL v4 | GPQA | 指令遵循 IFEval | 显存参考 |
|---|---|---|---|---|---|---|
| Qwen3.5-2B | 2B | 32K | 43.6% | 51.6% | 78.6% | 极低，8GB 内存可跑 |
| Qwen3.5-4B | 4B | 64K | 42.1% | ~55% | ~83% | 约 3GB+ 显存 |
| **Qwen3.5-9B** | 9B | **262K** | **66.1%** | **81.7%** | **91.5%** | INT4 ≈ 4.5GB / INT8 ≈ 9GB |
| Qwen3.5-27B | 27B | 128K | ~48% | ~90% | ~93% | 约 16GB+ 显存 |

**一点建议**：如果是个人使用，可以先把工具精简到自己日常够用的数量，再搭配 **7B 及以上**的模型，通常就能有不错的体验。参数越大的模型在复杂多步任务上会更从容，可以按自己的硬件条件按需往上选。

> **关于 Qwen3.8**：2026 年 8 月发布的旗舰是 `qwen3.8-2.4t-a95b`（2.4T 总参 / 95B 激活的稀疏 MoE，1M 上下文），属于超大规模模型，更适合通过云端 API 使用。好消息是它支持 function calling，可以直接接进 Claw Studio——这也正是本项目多供应商设计的用武之地。想在本地跑的话，Qwen3.5 系列是更合适的选择。

跑分数据是第三方数据库实测值，不同量化/推理框架会有出入，仅作选型参考。

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

## 打一个能发出去的包

```bash
python scripts/pack_release.py
```

产出 `dist-release/claw-studio-<日期>.zip`，**只含源码与配置模板**：

| 不会进包 | 原因 |
|---|---|
| `memory/` `sessions/` | 是你的记忆和聊天记录 |
| `data/` | 含加密后的 API Key、设置 |
| `logs/` `workspace/` | 诊断日志、工具的产物 |
| `bridge/{state,secure,inbox,outbox}` | 微信登录凭证与消息 |
| `config/config.json` | 本地配置（个人路径、模型选择），只发 `config.example.json` |
| `node_modules/` `web/dist/` | 依赖与构建产物，对方自己装 |

脚本打包完会**自己解压检查一遍**，发现任何运行期数据就报错退出（退出码 1），
不会让你带着自己的数据发出去。

> 别人拿到包后第一次启动是**空记忆、空会话**，`config/config.json` 会自动从模板生成。

## 许可

MIT
