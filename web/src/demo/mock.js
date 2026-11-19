/* 演示模式：不连后端也能完整浏览界面。
   在 fetch 层拦截 /api/*，返回可信的样例数据；聊天走一条预置的 SSE 流，
   计划面板会跟着一步步推进——用来演示、截图、给别人看，不需要任何 token。 */

const enc = new TextEncoder()

const PLAN = {
  session_id: 'demo',
  title: '修复打包失败',
  steps: [
    { id: 's1', title: '复现问题：本地前台跑一次打包', note: '', status: 'pending' },
    { id: 's2', title: '定位到 7za 与 NSIS 的进程冲突', note: '', status: 'pending' },
    { id: 's3', title: '改打包脚本：先清理残留进程', note: '', status: 'pending' },
    { id: 's4', title: '重新打包并验证安装包可运行', note: '', status: 'pending' },
  ],
}

const EMPTY = { session_id: 'demo', title: '', steps: [] }
const state = { plan: JSON.parse(JSON.stringify(EMPTY)), started: false }

const CHAT_SCRIPT = [
  { type: 'step', step: 1 },
  { type: 'reasoning', text: '先确认打包脚本的报错点，再看是不是进程占用导致的 0xC0000142。' },
  { type: 'tool_call', id: 't1', name: 'file_read', arguments: '{"path":"build.sh"}' },
  { type: 'tool_result', id: 't1', name: 'file_read', ok: true, seconds: 0.1, brief: 'build.sh · 42 行' },
  { type: 'content', text: '看了 `build.sh`，问题在最后一步：' },
  { type: 'content', text: '打包前没有清掉上一次残留的 `makensis.exe`，' },
  { type: 'content', text: '两个实例同时写同一个输出目录，第二次就会在 finalize 阶段崩掉。' },
  { type: 'content', text: '\n\n改法是加一行：\n\n```bash\ntaskkill //F //IM makensis.exe 2>/dev/null || true\n```\n\n' },
  { type: 'content', text: '并且让脚本**前台运行**，不要丢到后台——NSIS 在后台跑会拿不到控制台句柄。' },
  { type: 'done', steps: 3, model: 'demo:Studio' },
]

const MODELS = [
  {
    key: 'demo:Studio',
    name: '演示模型',
    current: true,
    ctx: 32000,
    tools: true,
    images: true,
    efforts: ['off', 'auto', 'low', 'medium', 'high'],
    effort_default: 'auto',
    can_off: true,
    ctx_train: 32000,
  },
]

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function sse(events, interval = 110) {
  const body = new ReadableStream({
    start(controller) {
      let i = 0
      const tick = () => {
        if (i >= events.length) {
          controller.close()
          return
        }
        controller.enqueue(enc.encode(`data: ${JSON.stringify(events[i++])}\n\n`))
        setTimeout(tick, interval)
      }
      tick()
    },
  })
  return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
}

function advancePlan() {
  const s = state.plan.steps
  const seq = [
    () => (s[0].status = 'running'),
    () => {
      s[0].status = 'done'
      s[0].note = '确认是残留进程，不是签名问题'
      s[1].status = 'running'
    },
    () => {
      s[1].status = 'done'
      s[1].note = '0xC0000142 = 两个 NSIS 实例抢同一个输出目录'
      s[2].status = 'running'
    },
    () => {
      s[2].status = 'done'
      s[2].note = 'build.sh 加 taskkill + 前台运行'
      s[3].status = 'running'
    },
    () => {
      s[3].status = 'done'
      s[3].note = '安装包 42MB，双击可启动'
    },
  ]
  seq.forEach((fn, i) => setTimeout(fn, 300 + i * 2600))
}

const WECHAT_ITEMS = [
  { id: 'a1', dir: 'in', at: Date.now() - 420000, state: 'received', text: '在吗？帮我看看打包脚本为什么失败' },
  { id: 'b1', dir: 'out', at: Date.now() - 380000, state: 'sent', text: '看过了：第 12 行把路径写死了，另外 NSIS 打包要前台跑。已经改成相对路径，你再试一次。' },
  { id: 'a2', dir: 'in', at: Date.now() - 240000, state: 'received', text: '好了，谢谢' },
  { id: 'b2', dir: 'out', at: Date.now() - 120000, state: 'pending', text: '【任务完成】依赖检查：3 个包有更新' },
]

const HANDLERS = [
  [/^\/api\/health$/, () => json({ ok: true, llama_running: false, models: 1 })],
  [/^\/api\/models$/, () => json({ ok: true, models: MODELS, current: MODELS[0].key })],
  [
    /^\/api\/status$/,
    () => json({ ok: true, default_workdir: 'workspace', ctx: 32000, overhead_tokens: 780 }),
  ],
  [/^\/api\/quota$/, () => json({ ok: true, providers: [] })],
  [/^\/api\/quota\//, () => json({ ok: true, quota: null })],
  [
    /^\/api\/voice$/,
    () =>
      json({
        ok: true,
        config: { tts: { voice: 'zh-CN-XiaoxiaoNeural', rate: '+0%', auto_read: false } },
        voices: [{ id: 'zh-CN-XiaoxiaoNeural', label: '晓晓 · 温柔' }],
        status: { tts_engine: 'edge' },
      }),
  ],
  [/^\/api\/voice\/config$/, () => json({ ok: true })],
  [/^\/api\/experts$/, () => json({ ok: true, compact: '', vision: '' })],
  [/^\/api\/sessions/, (req) => (req.method === 'GET' ? json({ ok: true, sessions: [] }) : json({ ok: true }))],
  [/^\/api\/ask\/pending$/, () => json({ ok: true, ask: null })],
  [/^\/api\/approval\/pending$/, () => json({ ok: true, items: [] })],
  [/^\/api\/plan/, (req) => json({ ok: true, plan: state.plan })],
  [/^\/api\/wechat$/, () => json({ ok: true, running: false, logged_in: false, can_start: false, models: 0 })],
  [/^\/api\/wechat\/messages/, () => json({ ok: true, items: WECHAT_ITEMS })],
  [/^\/api\/wechat\/send/, () => json({ ok: false, error: '演示模式：不会真的发消息' })],
  [/^\/api\/qoder\/status$/, () => json({ ok: true, available: false, logged_in: false, models: 0 })],
  [/^\/api\/settings$/, () => json({ ok: true, settings: {} })],
  [/^\/api\/memory/, () => json({ ok: true, entries: [], stats: {}, tags: [] })],
  [/^\/api\/skills/, () => json({ ok: true, skills: [] })],
  [/^\/api\/plugins/, () => json({ ok: true, plugins: [] })],
  [/^\/api\/update/, () => json({ ok: true, latest: '', current: '' })],
  [/^\/api\/notify/, () => json({ ok: true })],
  [/^\/api\/wb\//, () => json({ ok: false })],
]

export function installDemoMock() {
  if (window.__clawDemo) return
  window.__clawDemo = true

  const realFetch = window.fetch.bind(window)
  window.fetch = async (input, init) => {
    const url = typeof input === 'string' ? input : (input && input.url) || ''
    const path = url.replace(/^https?:\/\/[^/]+/, '')
    if (!path.startsWith('/api/')) return realFetch(input, init)

    const req = { method: (init && init.method) || 'GET', url: path }

    if (path.startsWith('/api/chat')) {
      if (!state.started) {
        state.started = true
        state.plan = JSON.parse(JSON.stringify(PLAN))
        advancePlan()
      }
      return sse(CHAT_SCRIPT)
    }
    for (const [rx, fn] of HANDLERS) {
      if (rx.test(path)) {
        try {
          return fn(req)
        } catch (e) {
          return json({ ok: false, error: String(e) })
        }
      }
    }
    return json({ ok: true })
  }

  // 演示环境没有后端事件流：给一个永不触发事件的空实现，避免反复重连刷错误
  window.EventSource = class {
    constructor() {
      this.readyState = 0
      setTimeout(() => {
        this.readyState = 1
        this.onopen && this.onopen({})
      }, 50)
    }
    close() {}
    addEventListener() {}
    removeEventListener() {}
  }
}
