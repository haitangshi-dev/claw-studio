import { useEffect, useMemo, useRef, useState } from 'react'
import { getExperts, getModels, getStatus, getQuotaProviders, streamChat, switchModel, getPendingAsk, answerAsk, skipAsk, getVoice, saveVoice, fetchTts, getApprovals, getPlan } from './api.js'
import { IconBrain, IconChat, IconChevron, IconEdit, IconFolder, IconGear, IconImage, IconMemory, IconPanelLeft, IconPlus, IconSearch, IconSend, IconSliders, IconStop, IconTrash, IconVolume, IconVolumeOff, IconX, toolIcon } from './icons.jsx'
import { MessageList } from './components/Chat.jsx'
import { AskCard } from './components/Chat.jsx'
import { WorkdirPanel } from './components/WorkdirPanel.jsx'
import ApprovalCard from './components/ApprovalCard.jsx'
import PlanPanel from './components/PlanPanel.jsx'
import ModePopover from './components/ModePopover.jsx'
import { CrabMark } from './ui/brand.jsx'
import { MemoryPanel, ModelMenu, ParamsPanel, PermMenu, SkillsPanel, ThinkMenu, THINK_LEVELS, effortOptions } from './components/Panels.jsx'
import { EffortIgnition } from './effort-ignition.jsx'
import { EffortDrawer, EffortTrigger } from './effort-drawer.jsx'

let uid = 0
const genId = () => `m${Date.now()}_${uid++}`

// 云端模型判定（api: 走 OpenAI 兼容端点，无需本地加载）
const isCloudKey = (k) => String(k || '').startsWith('api:')

const TOOL_LABELS = {
  run_shell: '执行命令', shell_jobs: '后台任务',
  file_read: '读文件', file_create: '新建文件', file_edit: '改文件', file_delete: '删文件', file_move: '移动文件', file_copy: '复制文件', file_search: '找文件',
  memory_search: '查记忆', memory_write: '写记忆', memory_get: '取记忆', memory_forget: '忘掉记忆', memory_pin: '固定记忆', memory_stats: '记忆统计', memory_tags: '记忆标签',
  skill_list: '列技能', skill_read: '读技能', skill_run: '跑技能',
  plugin_list: '列插件', browser_control: '浏览器', http_request: 'HTTP 请求', notify: '发通知', ask_user: '询问你',
};
const toolLabel = (name) => TOOL_LABELS[String(name || '').replace(/^mcp__claw-agent__/, '')] || String(name || '').replace(/^mcp__claw-agent__/, '');


// 数字格式化：整数不带小数点，小数保留 2 位再去尾零
const fmtNum = (n) => {
  const v = Number(n || 0)
  return Number.isInteger(v) ? String(v) : v.toFixed(2).replace(/\.?0+$/, '')
}

// 顶栏额度 chip：每家接入方一个（优先显示剩余额度，没有就显示最近一天用量）
const quotaChipInfo = (p) => {
  const q = p.quota && p.quota.ok ? p.quota : null
  const rows = Object.entries(p.usage || {}).sort((a, b) => (a[0] < b[0] ? 1 : -1))
  const [day, rec] = rows[0] || []
  const hasQuota = q && q.remaining != null
  if (!hasQuota && !rec) return null
  const d = new Date()
  const today = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  const label = day === today ? '今日' : String(day).slice(5)
  const unit = p.unit || (q && q.unit) || 'credits'
  const detail = rows.slice(0, 7)
    .map(([dt, r]) => `${String(dt).slice(5)}：${r.calls} 次 · ${fmtNum(r.credits)} ${unit}`)
    .join('\n')
  const quotaLines = hasQuota
    ? [
        `剩余：${q.remaining} ${unit}`,
        q.plan ? `套餐：${q.plan}` : '',
        ...((q.packages || []).map((k) => `${k.name}：${k.used}/${k.limit}`)),
      ].filter(Boolean).join('\n')
    : ''
  const updated = q && q.at
    ? new Date(q.at * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    : ''
  return {
    text: hasQuota
      ? `${p.name} 余 ${q.remaining}`
      : `${p.name} ${label} ${rec.calls} 次 · ${fmtNum(rec.credits)}`,
    title: [
      `${p.name} 额度（点击打开设置）`,
      quotaLines,
      updated ? `额度更新：${updated}` : '',
      rec ? `${label}用量：${rec.calls} 次 · ${fmtNum(rec.credits)} ${unit}` : '',
      detail,
    ].filter(Boolean).join('\n'),
  }
}

// 图片处理：等比缩到最长边 ≤1024 再转 dataURI（控制体积）
const fileToDataUrl = (file, maxSide = 1024) =>
  new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const img = new Image()
      img.onload = () => {
        const scale = Math.min(1, maxSide / Math.max(img.width, img.height))
        const w = Math.max(1, Math.round(img.width * scale))
        const h = Math.max(1, Math.round(img.height * scale))
        const c = document.createElement('canvas')
        c.width = w
        c.height = h
        c.getContext('2d').drawImage(img, 0, 0, w, h)
        resolve(c.toDataURL('image/jpeg', 0.85))
      }
      img.onerror = reject
      img.src = reader.result
    }
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
const newSession = (workdir, extra = {}) => {
  const now = Date.now()
  return {
    id: crypto.randomUUID(),
    title: '新会话',
    messages: [],
    createdAt: now,
    updatedAt: now,
    ...(workdir ? { workdir } : {}),
    ...extra,
  }
}

// 会话数据修复（中断态修正 + 时间戳迁移 + timeline 还原）——localStorage 与后端文件共用
function normalizeSessions(raw) {
  if (!Array.isArray(raw) || !raw.length) return null
  const now = Date.now()
  raw.forEach((s, i) => {
    if (!s || !s.id || !Array.isArray(s.messages)) return
    s.createdAt = s.createdAt || now - (raw.length - i) * 60000
    s.updatedAt = s.updatedAt || s.createdAt
  })
  for (const s of raw) {
    if (!s || !s.id || !Array.isArray(s.messages)) return null
    for (const m of s.messages) {
      if (m.role === 'assistant' && !m.done) {
        m.done = true
        m.stopped = true
      }
      // 恢复 timeline：磁盘上只存了「结构 + 各段长度」，按顺序从 reasoning/content 切回文本，
      // 这样既不重复存正文，又能还原真实的「思考→工具→输出」交错顺序。
      if (m.role === 'assistant' && Array.isArray(m.timeline)) {
        let rp = 0
        let cp = 0
        const rr = m.reasoning || ''
        const cc = m.content || ''
        m.timeline = m.timeline
          .map((seg) => {
            if (seg.kind === 'reasoning') {
              const n = seg.len != null ? seg.len : (rp === 0 ? rr.length : 0)
              const text = rr.slice(rp, rp + n)
              rp += n
              return { ...seg, text }
            }
            if (seg.kind === 'content') {
              const n = seg.len != null ? seg.len : (cp === 0 ? cc.length : 0)
              const text = cc.slice(cp, cp + n)
              cp += n
              return { ...seg, text }
            }
            return seg
          })
          .filter((seg) => seg.kind !== 'reasoning' || seg.text)
        // 兜底：老数据里 timeline 没有正文段 → 把整段 content 追加到最后一次正文位置之后
        if (!m.timeline.some((x) => x.kind === 'content') && cc) {
          m.timeline.push({ kind: 'content', text: cc, step: m.stepNow ?? 0 })
        }
      }
    }
  }
  return raw
}

// 兜底：localStorage 里的会话（后端不可用时才作为主源）
function loadSessions() {
  try {
    return normalizeSessions(JSON.parse(localStorage.getItem('claw_sessions') || 'null'))
  } catch (e) {
    return null
  }
}

// 目录短名（路径最后一段）
const baseName = (p) => {
  const parts = String(p || '').split(/[\\/]+/).filter(Boolean)
  return parts[parts.length - 1] || String(p || '')
}

// 会话时间显示：今天→17:23；昨天→昨天；更早→9月17日
function fmtSessionTime(ts) {  if (!ts) return ''
  const d = new Date(ts)
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  if (ts >= today) return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  if (ts >= today - 86400000) return '昨天'
  return `${d.getMonth() + 1}月${d.getDate()}日`
}

// 会话预览：最后一条有内容的回复/提问，去掉 markdown 符号
function sessionPreview(s) {
  for (let i = s.messages.length - 1; i >= 0; i--) {
    const m = s.messages[i]
    if (m.content) {
      return m.content.replace(/[#*`>[\]()]/g, '').replace(/\s+/g, ' ').trim().slice(0, 46)
    }
  }
  return '空会话'
}

// 会话图标双色（紫/青，按 id 稳定分配）
const iconColor = (id) =>
  [...String(id)].reduce((a, c) => a + c.charCodeAt(0), 0) % 2 === 0 ? '#D9713A' : '#2C7F9E'

// ---------- 上下文估算与载荷组装（压缩管理用） ----------
const INJECT_OVERHEAD = 6000 // 默认注入开销；实际值由后端 /api/status 提供
const estimateMsgTokens = (m) => {
  if (!m) return 6
  let n = ((m.content || '').length) + 6
  if (Array.isArray(m.tool_calls)) {
    for (const tc of m.tool_calls) n += String(((tc.function || {}).arguments) || '').length
  }
  if (Array.isArray(m.toolCalls)) {
    for (const tc of m.toolCalls) {
      n += String(tc.arguments || '').length + String(tc.output != null ? tc.output : '').length
    }
  }
  return Math.ceil(n)
}
const estimateTokens = (msgs) => msgs.reduce((s, m) => s + estimateMsgTokens(m), 0)

// 工具调用压缩成一行短文本（用于历史摘要的原料）
const briefToolCalls = (tcs) =>
  (tcs || [])
    .filter((t) => t.status === 'done' && t.name)
    .map((t) => {
      let v = ''
      try {
        const o = JSON.parse(t.arguments || '{}')
        v = o.path || o.command || o.query || o.title || ''
      } catch (e) {
        v = String(t.arguments || '').slice(0, 60)
      }
      return `${t.name}(${String(v).replace(/\s+/g, ' ').slice(0, 60)})${t.ok === false ? '✗' : '✓'}`
    })
    .join('；')

// 组装发送给模型的历史：压缩边界之前的消息用摘要替代；助手消息带完整工具调用与结果
//（每次工具调用都回放——模型对自己的行为有记忆，不用重新瞎找；超限交给压缩机制）
const buildPayload = (msgs, compacted) => {
  const from = compacted && compacted.upToIndex > 0 ? compacted.upToIndex : 0
  const out = []
  if (from > 0 && compacted) out.push({ role: 'assistant', content: `【历史摘要】${compacted.summary}` })
  for (const m of msgs.slice(from)) {
    if (m.role === 'user' && m.content) out.push({ role: 'user', content: m.content })
    else if (m.role === 'assistant' && m.done) {
      const done = (m.toolCalls || []).filter(
        (tc) => tc.status === 'done' && tc.name && !String(tc.name).startsWith('mcp__')
      )
      if (done.length) {
        const calls = done.map((tc, i) => ({ tc, rid: `${m.id}_${tc.id || 'c' + i}` }))
        out.push({
          role: 'assistant',
          content: m.content || '',
          tool_calls: calls.map(({ tc, rid }) => ({
            id: rid,
            type: 'function',
            function: { name: tc.name, arguments: tc.arguments || '{}' },
          })),
        })
        for (const { tc, rid } of calls) {
          out.push({ role: 'tool', tool_call_id: rid, content: String(tc.output != null ? tc.output : '(无输出)') })
        }
      } else if (m.content) {
        out.push({ role: 'assistant', content: m.content })
      }
    }
  }
  return out
}

const appendTeamEvent = (sessions, evt) => {
  const content = String(evt.text || '').trim()
  if (!content || !evt.session_id) return sessions
  const msg = {
    id: evt.id || genId(), role: 'assistant', sender: evt.sender || {},
    content, done: true, team: true, team_id: evt.team_id, at: evt.at || Date.now() / 1000,
  }
  const target = sessions.find((s) => s.id === evt.session_id)
  if (!target) return [...sessions, newSession(undefined, { id: evt.session_id, title: '办公室会话', messages: [msg] })]
  if (target.messages.some((m) => m.id === msg.id)) return sessions
  return sessions.map((s) => s.id !== target.id ? s : { ...s, messages: [...s.messages, msg], updatedAt: Date.now() })
}

// 按最后活跃时间分组（今天/昨天/前 7 天/更早）
function groupSessions(list) {
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const DAY = 86400000
  const groups = { 今天: [], 昨天: [], '前 7 天': [], 更早: [] }
  const sorted = [...list].sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0))
  for (const s of sorted) {
    const t = s.updatedAt || 0
    if (t >= today) groups['今天'].push(s)
    else if (t >= today - DAY) groups['昨天'].push(s)
    else if (t >= today - 7 * DAY) groups['前 7 天'].push(s)
    else groups['更早'].push(s)
  }
  return groups
}

// ---------- 会话持久化：以后端文件为主，localStorage 仅作兜底 ----------
// 起因：localStorage 约 5MB，带工具调用的长会话（工具输出动辄几 MB）写满后
// 旧逻辑会把整个列表砍掉一半 → 「之前的会话不见了」。后端 sessions/ 无此上限。
const LS_KEY = 'claw_sessions'
const LS_MIGRATED = 'claw_sessions_migrated'

function slimSession(s) {
  // 只裁超长思考；正文/工具输出全部保留（后端没有容量焦虑）
  return {
    ...s,
    messages: (s.messages || []).map((m) => {
      const mm = m.reasoning && m.reasoning.length > 6000 ? { ...m, reasoning: m.reasoning.slice(0, 6000) } : m
      if (!Array.isArray(mm.timeline)) return mm
      // timeline 只留「结构 + 各段长度」，文本另有字段存着，切回时按长度还原
      return {
        ...mm,
        timeline: mm.timeline.map((sg) => {
          if (sg.kind === 'reasoning') return { kind: 'reasoning', step: sg.step, len: (sg.text || '').length }
          if (sg.kind === 'content') return { kind: 'content', step: sg.step, len: (sg.text || '').length }
          if (sg.kind === 'notice') return { kind: 'notice', text: sg.text, step: sg.step }
          return { kind: 'tool', id: sg.id, name: sg.name, arguments: sg.arguments, step: sg.step }
        }),
      }
    }),
  }
}

async function apiSessionsList() {
  const r = await fetch('/api/sessions')
  const d = await r.json()
  if (!d || !d.ok) throw new Error((d && d.error) || '会话列表读取失败')
  return d.sessions || []
}

async function apiSessionSave(s) {
  const r = await fetch('/api/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session: s }),
  })
  return r.json()
}

async function apiSessionDelete(id) {
  try {
    await fetch(`/api/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' })
  } catch (e) {
    /* 离线忽略 */
  }
}

async function apiSessionsReplace(list, dropMissing = false) {
  const r = await fetch('/api/sessions/replace', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sessions: list, drop_missing: dropMissing }),
  })
  return r.json()
}

// 兜底：localStorage（只在后端不可用时读写）
function persistSessionsLocal(list) {
  const slim = list.map(slimSession)
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(slim))
  } catch (e) {
    /* 超容量：放弃本轮兜底，不影响后端主存 */
  }
}

export default function App() {
  const initial = useRef(null)
  if (!initial.current) {
    initial.current = loadSessions() || [newSession()]
  }
  const [sessions, setSessions] = useState(initial.current)
  // 后端会话是否已拉回 / 已落盘快照（避免重复上传未变化的会话）
  const bootedRef = useRef(false)
  const savedRef = useRef(new Map())
  const [activeId, setActiveId] = useState(() => {
    const saved = localStorage.getItem('claw_active')
    return initial.current.some((s) => s.id === saved) ? saved : initial.current[0].id
  })
  const [models, setModels] = useState([])
  const [modelKey, setModelKey] = useState(() => localStorage.getItem('claw_model') || '')
  // 思考强度按模型记忆（输入框芯片或右键模型项设置）
  const [thinkByModel, setThinkByModel] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem('claw_think_by_model') || '{}')
    } catch (e) {
      return {}
    }
  })
  const thinkLevel = (() => {
    const stored = thinkByModel[modelKey]
    if (stored != null) return stored
    if (isCloudKey(modelKey)) {
      const cm = models.find((m) => m.key === modelKey)
      return (cm && cm.effort_default) || ''
    }
    return 1024
  })()
  const isCloudModel = isCloudKey(modelKey)
  const cloudModel = models.find((m) => m.key === modelKey)
  const cloudThinkOpts = isCloudModel ? effortOptions(cloudModel) : []
  // 抽屉里用的档位表（本地=预算档位，云端=模型声明的档位）
  const thinkOpts = isCloudModel ? cloudThinkOpts : THINK_LEVELS
  // 该模型的默认档位（用于「恢复默认」）
  const thinkDefault = isCloudModel ? ((cloudModel && cloudModel.effort_default) || '') : 1024
  // 是否已拉到思考强度最高档（本地=「无限」，云端=最后一个档位）——决定是否带点火强调色
  const thinkAtTop = isCloudModel
    ? !!(cloudThinkOpts.length && String(thinkLevel) === String(cloudThinkOpts[cloudThinkOpts.length - 1].k))
    : thinkLevel === -1
  // busy 按会话存：一条流在跑不该让别的会话也发不出消息（原来是个全局布尔，
  // 切到另一个会话想插一句话都被 disabled 挡住，很反直觉）。
  const [busyMap, setBusyMap] = useState({})
  const markBusy = (sid, on) => {
    if (!sid) return
    setBusyMap((prev) => {
      if (!!prev[sid] === !!on) return prev
      const next = { ...prev }
      if (on) next[sid] = true
      else delete next[sid]
      return next
    })
  }
  const busy = !!busyMap[activeId]
  const [experts, setExperts] = useState({})
  const [pendingImages, setPendingImages] = useState([]) // 待发送图片（dataURI）
  const [voiceCfg, setVoiceCfg] = useState(null)
  const [speakingId, setSpeakingId] = useState('')
  const speakingRef = useRef('')
  const audioRef = useRef(null)
  const spokenRef = useRef(new Set())
  const appStartRef = useRef(Date.now())
  const imgInputRef = useRef(null)
  const [toast, setToast] = useState('')
  const [ignition, setIgnition] = useState(null) // 推理强度拉到底时的点火特效
  const [effortOpen, setEffortOpen] = useState(false) // 思考强度抽屉是否展开（默认收起）
  const [modeOpen, setModeOpen] = useState(false) // 值守模式抽屉是否展开

  // 点空白处 / 按 Esc 收起思考强度抽屉
  useEffect(() => {
    if (!effortOpen) return undefined
    const onDown = (e) => {
      const t = e.target
      if (t && t.closest && t.closest('[data-effort-ui]')) return
      if (t && t.closest && t.closest('.effort-trigger')) return
      setEffortOpen(false)
    }
    const onKey = (e) => {
      if (e.key === 'Escape') setEffortOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [effortOpen])
  const [showMemory, setShowMemory] = useState(false)
  const [showSkills, setShowSkills] = useState(false)
  const [quotaProviders, setQuotaProviders] = useState([]) // 额度提供方列表（接一家多一套额度设置）
  const [draft, setDraft] = useState('')
  // 工作目录随会话保存；未绑定时用后端默认目录
  const [defaultWorkdir, setDefaultWorkdir] = useState('workspace')
  const [showWorkdir, setShowWorkdir] = useState(false)
  const [query, setQuery] = useState('')
  const [editingId, setEditingId] = useState(null)
  const [ctxSize, setCtxSize] = useState(32768)
  const [overhead, setOverhead] = useState(INJECT_OVERHEAD)
  // 有效上下文窗口：云端用上游声明的真实输入上限（maxInputTokens，后端归一进 ctx_train），
  // 本地按 llama 配置的 ctx。目录还没拉到时按同族保守值兜底。
  const effCtxSize = (() => {
    if (isCloudModel) {
      const cm = models.find((m) => m.key === modelKey)
      if (cm) return cm.ctx_train || cm.ctx || 192000
      return 192000
    }
    return ctxSize
  })()
  const [showParams, setShowParams] = useState(false)
  const [toolCount, setToolCount] = useState(0)
  const [showSettings, setShowSettings] = useState(false)
  const [settingsPane, setSettingsPane] = useState('')   // 打开设置时直接落到哪一页
  // 值守模式 / 无人值守时长：按会话保存（会话 A 与 B 互不影响）
  const modeSession = sessions.find((s) => s.id === activeId) || sessions[0] || {}
  const legacyMode = (() => {
    const saved = localStorage.getItem('claw_agent_mode')
    if (saved === 'onduty' || saved === 'unattended') return saved
    return localStorage.getItem('claw_unattended') === '1' ? 'unattended' : 'onduty'
  })()
  const agentMode =
    modeSession.agentMode === 'onduty' || modeSession.agentMode === 'unattended'
      ? modeSession.agentMode
      : legacyMode
  const unattended = agentMode === 'unattended'
  const legacyMinutes = 30
  const unattendedMinutes = (() => {
    const raw =
      modeSession.unattendedMinutes != null && Number.isFinite(Number(modeSession.unattendedMinutes))
        ? Number(modeSession.unattendedMinutes)
        : legacyMinutes
    return [0, 30, 60, 90, 120].includes(raw) ? raw : 30
  })()
  const setAgentMode = (k) =>
    setSessions((prev) =>
      prev.map((s) => (s.id === activeId ? { ...s, agentMode: k, updatedAt: Date.now() } : s))
    )
  const setUnattendedMinutes = (n) =>
    setSessions((prev) =>
      prev.map((s) =>
        s.id === activeId ? { ...s, unattendedMinutes: Number(n), updatedAt: Date.now() } : s
      )
    )
  // 工具权限档位：两种模式各自独立保存（值守中用的是专用值守权限）
  const [permByMode, setPermByMode] = useState(() => {
    try {
      const saved = JSON.parse(localStorage.getItem('claw_perm_by_mode') || 'null')
      if (saved && typeof saved === 'object') return saved
    } catch (e) {
      /* 旧数据损坏则回落默认 */
    }
    const legacy = localStorage.getItem('claw_permission') || 'ask'
    return { onduty: legacy, unattended: legacy }
  })
  useEffect(() => {
    localStorage.setItem('claw_perm_by_mode', JSON.stringify(permByMode))
  }, [permByMode])
  const permission = permByMode[agentMode] || 'ask'
  const setPermission = (v) => setPermByMode((prev) => ({ ...prev, [agentMode]: v }))
  // 状态栏：模型当前在干什么
  const [activity, setActivity] = useState({ kind: 'idle', label: '就绪' })
  // 模型参数覆盖（按模型保存，仅存用户自定义项）
  const [paramsByModel, setParamsByModel] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem('claw_params_by_model') || '{}')
    } catch (e) {
      return {}
    }
  })
  const curParams = paramsByModel[modelKey] || {}
  const setParam = (key, val) => {
    setParamsByModel((prev) => {
      const mp = { ...(prev[modelKey] || {}) }
      if (val == null) delete mp[key]
      else mp[key] = val
      const next = { ...prev }
      if (Object.keys(mp).length) next[modelKey] = mp
      else delete next[modelKey]
      return next
    })
  }
  const resetParams = () => {
    setParamsByModel((prev) => {
      const next = { ...prev }
      delete next[modelKey]
      return next
    })
  }
  const [sideCollapsed, setSideCollapsed] = useState(() => localStorage.getItem('claw_side') === '1')
  useEffect(() => {
    localStorage.setItem('claw_side', sideCollapsed ? '1' : '0')
  }, [sideCollapsed])
  // 界面内提问卡片（ask_user）：后端有 pending 提问时浮出，用户点选后回传
  const [ask, setAsk] = useState(null)
  const [approvals, setApprovals] = useState([])
  const [plan, setPlan] = useState(null)
  const askDismissedRef = useRef(new Set())
  useEffect(() => {
    let stop = false
    const tick = async () => {
      try {
        const d = await getPendingAsk()
        if (stop) return
        const a = d && d.ok ? d.ask : null
        if (!a || !a.id || askDismissedRef.current.has(a.id)) {
          setAsk((prev) => (prev && (!a || prev.id !== a.id) ? null : prev))
          return
        }
        setAsk((prev) => (prev && prev.id === a.id ? { ...prev, ...a } : a))
      } catch (e) {
        /* 离线/后端重启时忽略 */
      }
    }
    tick()
    const t = setInterval(tick, 1200)
    return () => { stop = true; clearInterval(t) }
  }, [])
  useEffect(() => {
    let stop = false
    const tick = async () => {
      if (document.hidden) return
      try {
        const d = await getApprovals()
        if (stop) return
        setApprovals((d && d.ok && Array.isArray(d.items) ? d.items : []))
      } catch (e) {
        /* 离线忽略 */
      }
    }
    tick()
    const t = setInterval(tick, 900)
    return () => { stop = true; clearInterval(t) }
  }, [])

  useEffect(() => {
    let stop = false
    const sid = activeId
    if (!sid) return undefined
    const tick = async () => {
      if (document.hidden) return
      try {
        const d = await getPlan(sid)
        if (!stop && d && d.ok) setPlan(d.plan)
      } catch (e) {
        /* 离线忽略 */
      }
    }
    tick()
    const t = setInterval(tick, 1500)
    return () => { stop = true; clearInterval(t) }
  }, [activeId])

  // 设置页在 iframe 里跑，点它自己的关闭按钮时通过 postMessage 通知主窗口
  useEffect(() => {
    const onMsg = (e) => {
      if (e && e.data && e.data.type === 'claw:close-settings') setShowSettings(false)
    }
    const onKey = (e) => {
      if (e.key === 'Escape') setShowSettings(false)
    }
    window.addEventListener('message', onMsg)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('message', onMsg)
      window.removeEventListener('keydown', onKey)
    }
  }, [])

  const answerAskCard = async (val) => {
    const cur = ask
    if (!cur) return
    askDismissedRef.current.add(cur.id)
    setAsk(null)
    try {
      await answerAsk(cur.id, val)
    } catch (e) {
      /* 提交失败：工具侧会超时退回文字版 */
    }
  }
  const skipAskCard = async () => {
    const cur = ask
    if (!cur) return
    askDismissedRef.current.add(cur.id)
    setAsk(null)
    try {
      await skipAsk(cur.id)
    } catch (e) {
      /* 忽略失败：工具侧会超时退回文字版 */
    }
  }
  // 中断函数按会话存：单值 ref 会在「A 在跑 → 切到 B 发消息」时被覆盖，A 就停不下来了
  const abortsRef = useRef({})
  // 用户是否手动按了「停止」：用来区分「手动停」与「因阀门暂停」（后者要续跑）
  const userStoppedRef = useRef(false)
  // 会话状态最新镜像：异步续跑时要拿到最新消息当压缩原料
  const sessionsRef = useRef(null)
  const activeIdRef = useRef(activeId)
  const busyRef = useRef(busy)
  const sendRef = useRef(null)
  activeIdRef.current = activeId
  busyRef.current = busy

  // 手机 Claw 主动发来的消息：通过后端的 /api/live 推送到当前聊天，
  // 复用现有 send 链路（保持当前模型/会话上下文），用户能看到完整问答。
  useEffect(() => {
    sendRef.current = send
  })

  const pendingTeamRef = useRef([])
  useEffect(() => {
    let es = null
    let timer = null
    let deadline = 0
    try {
      es = new EventSource('/api/live')
    } catch {
      return undefined
    }
    const deliver = (evt) => {
      const text = String((evt && evt.text) || '').trim()
      if (!text) return
      const target = evt.session_id || activeIdRef.current
      if (target && target !== activeIdRef.current) return
      const content = `【手机 Claw】${text}`
      deadline = Date.now() + 120000
      const trySend = () => {
        if (!sendRef.current || busyRef.current) {
          if (Date.now() > deadline) return
          timer = setTimeout(trySend, 1500)
          return
        }
        sendRef.current(content)
      }
      trySend()
    }
    const deliverTeam = (evt) => {
      const event = { ...evt, session_id: evt.session_id || activeIdRef.current }
      if (!bootedRef.current) { pendingTeamRef.current.push(event); return }
      setSessions((prev) => appendTeamEvent(prev, event))
    }
    es.onmessage = (e) => {
      try {
        const evt = JSON.parse(e.data)
        if (evt && evt.type === 'team_message') deliverTeam(evt)
        else if (evt && evt.type === 'phone_message') deliver(evt)
      } catch {
        /* ignore malformed event */
      }
    }
    return () => {
      try {
        es.close()
      } catch {
        /* ignore */
      }
      if (timer) clearTimeout(timer)
    }
  }, [])
  const taRef = useRef(null)
  const session = sessions.find((s) => s.id === activeId) || sessions[0]
  // 会话自带的 workdir 优先，旧会话回落默认目录
  const workdir = session?.workdir || defaultWorkdir || 'workspace'
  const workdirName = baseName(workdir)
  const autoReadOn = voiceCfg ? voiceCfg.tts?.auto_read !== false : true

  // ---------- 语音：朗读 / 麦克风识别 ----------
  const stopAudio = () => {
    const a = audioRef.current
    if (a) {
      try {
        a.pause()
      } catch {
        /* ignore */
      }
      if (a._url) URL.revokeObjectURL(a._url)
      audioRef.current = null
    }
    speakingRef.current = ''
    setSpeakingId('')
  }

  const speak = async (msgOrText, idOverride) => {
    const text = typeof msgOrText === 'string' ? msgOrText : (msgOrText && msgOrText.content) || ''
    const id = idOverride || (msgOrText && msgOrText.id) || ''
    if (!String(text).trim()) return
    if (id && speakingRef.current === id) {
      stopAudio()
      return
    }
    stopAudio()
    speakingRef.current = id
    setSpeakingId(id)
    try {
      const blob = await fetchTts(text, {
        voice: voiceCfg?.tts?.voice,
        rate: voiceCfg?.tts?.rate,
      })
      if (speakingRef.current !== id) return
      const url = URL.createObjectURL(blob)
      const au = new Audio(url)
      au._url = url
      audioRef.current = au
      au.onended = () => {
        if (audioRef.current === au) stopAudio()
      }
      au.onerror = () => {
        setToast('朗读播放失败')
        if (audioRef.current === au) stopAudio()
      }
      await au.play()
    } catch (e) {
      setToast('朗读失败：' + String((e && e.message) || e).slice(0, 100))
      stopAudio()
    }
  }

  const toggleAutoRead = async () => {
    const next = !(voiceCfg ? voiceCfg.tts?.auto_read !== false : true)
    setVoiceCfg((prev) => ({ ...(prev || {}), tts: { ...((prev && prev.tts) || {}), auto_read: next } }))
    if (!next) stopAudio()
    try {
      const r = await saveVoice({ tts: { auto_read: next } })
      if (r && r.ok) {
        setVoiceCfg(r.config)
        setToast(next ? '自动朗读已开启' : '自动朗读已关闭')
      } else {
        setToast('朗读开关保存失败：' + ((r && r.error) || '未知错误'))
        getVoice().then((d) => d && d.ok && setVoiceCfg(d.config)).catch(() => {})
      }
    } catch (e) {
      setToast('朗读开关保存失败：' + String(e).slice(0, 100))
    }
    setTimeout(() => setToast(''), 2200)
  }

  useEffect(() => {
    getVoice()
      .then((d) => {
        if (d && d.ok) setVoiceCfg(d.config)
      })
      .catch(() => {})
    return () => {
      const a = audioRef.current
      if (a) {
        try {
          a.pause()
        } catch {
          /* ignore */
        }
      }
    }
  }, [])

  // 自动朗读：只读本次启动之后新生成的回复，避免切会话时重读历史
  useEffect(() => {
    if (!voiceCfg?.tts?.auto_read) return
    const msgs = (session && session.messages) || []
    const last = [...msgs].reverse().find((m) => m.role === 'assistant')
    if (!last || !last.done || !last.content) return
    if (spokenRef.current.has(last.id)) return
    const created = Number((String(last.id).match(/^m(\d+)_/) || [])[1] || 0)
    if (!created || created < appStartRef.current) return
    spokenRef.current.add(last.id)
    speak(last.content, last.id)
  }, [session, voiceCfg])

  // ---------- 初始化 ----------
  const loadModels = () =>
    getModels()
      .then((d) => {
        setModels(d.models || [])
        // 选择校验：模型不在列表（如已移除的 WorkBuddy）→ 回落本地当前模型；
        // 本地 llama 在跑且选的是本地模型 → 跟随当前加载；云端选择保留
        const stored = localStorage.getItem('claw_model') || ''
        const list = d.models || []
        const cur = list.find((m) => m.current)
        const exists = list.some((m) => m.key === stored)
        if (cur && (!stored || !exists || !isCloudKey(stored))) setModelKey(cur.key)
        else if (!exists) {
          // 没有存过、也没有本地模型在跑：优先挑云端模型，避免选中一个不存在的 key
          const first = list[0]
          if (first) setModelKey(first.key)
        }
      })
      .catch(() => {})

  const refreshStatus = () => {
    getStatus()
      .then((d) => {
        if (d.default_workdir) setDefaultWorkdir(d.default_workdir)
        if (d.ctx) setCtxSize(d.ctx)
        if (d.overhead_tokens) setOverhead(d.overhead_tokens)
        if (d.tool_count) setToolCount(d.tool_count)
      })
      .catch(() => {})
  }

  const loadQuota = () => {
    getQuotaProviders()
      .then((d) => setQuotaProviders((d && d.providers) || []))
      .catch(() => {})
  }

  useEffect(() => {
    loadModels()
    refreshStatus()
    loadQuota()
    getExperts()
      .then((d) => {
        if (d.ok) setExperts(d.experts || {})
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    localStorage.setItem('claw_model', modelKey)
  }, [modelKey])
  useEffect(() => {
    localStorage.setItem('claw_think_by_model', JSON.stringify(thinkByModel))
  }, [thinkByModel])
  useEffect(() => {
    localStorage.setItem('claw_params_by_model', JSON.stringify(paramsByModel))
  }, [paramsByModel])
  useEffect(() => {
    sessionsRef.current = sessions      // 续跑时（异步）要能拿到最新消息
    if (!bootedRef.current) return      // 后端会话还没拉回来前，别用空列表覆盖
    const timer = setTimeout(() => {
      const saved = savedRef.current
      const seen = new Set()
      for (const s of sessions) {
        seen.add(s.id)
        // 空会话不写盘：新建后什么都没说就切走/关掉，不该在 sessions/ 里留一个空壳
        if (!s.messages || s.messages.length === 0) {
          if (saved.has(s.id)) {
            saved.delete(s.id)
            apiSessionDelete(s.id)
          }
          continue
        }
        let json = ''
        try {
          json = JSON.stringify(slimSession(s))
        } catch (e) {
          continue
        }
        if (saved.get(s.id) === json) continue   // 没变化不重复上传（长会话一次可达数 MB）
        saved.set(s.id, json)
        apiSessionSave(JSON.parse(json)).catch(() => {
          saved.delete(s.id)                      // 失败则下次重试
          persistSessionsLocal(sessions)
        })
      }
      for (const id of Array.from(saved.keys())) {
        if (seen.has(id)) continue
        saved.delete(id)
        apiSessionDelete(id)
      }
    }, 400)
    return () => clearTimeout(timer)
  }, [sessions])
  useEffect(() => {
    localStorage.setItem('claw_active', activeId)
  }, [activeId])

  // 启动：以后端文件为准拉回会话；首次升级时把 localStorage 里的旧会话迁移上去
  useEffect(() => {
    let stop = false
    ;(async () => {
      let remote = []
      try {
        remote = await apiSessionsList()
      } catch (e) {
        remote = []          // 后端不可用：退回 localStorage
      }
      if (stop) return
      const local = loadSessions() || []
      const migrated = localStorage.getItem(LS_MIGRATED) === '1'
      let list
      if (remote.length) {
        list = normalizeSessions(remote) || []
        if (!migrated) {
          // 后端没有、本地还有的会话补进来（避免升级瞬间丢东西）
          const ids = new Set(list.map((s) => s.id))
          const extra = local.filter((s) => s && s.id && !ids.has(s.id))
          if (extra.length) list = [...extra, ...list]
          localStorage.setItem(LS_MIGRATED, '1')
        }
      } else if (local.length) {
        list = local
        try {
          await apiSessionsReplace(local.map(slimSession), false)
        } catch (e) {
          /* 迁移失败：下次启动再试 */
        }
        localStorage.setItem(LS_MIGRATED, '1')
      } else {
        list = [newSession()]
      }
      for (const event of pendingTeamRef.current) list = appendTeamEvent(list, event)
      pendingTeamRef.current = []
      list.sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0))
      const snap = new Map()
      for (const s of list) {
        try {
          snap.set(s.id, JSON.stringify(slimSession(s)))
        } catch (e) {
          /* 跳过无法序列化的 */
        }
      }
      savedRef.current = snap
      bootedRef.current = true
      setSessions(list)
      setActiveId((prev) => (list.some((s) => s.id === prev) ? prev : list[0].id))
    })()
    return () => {
      stop = true
    }
  }, [])

  // ---------- 会话操作 ----------
  const patchAssistant = (sid, mid, fn) =>
    setSessions((prev) =>
      prev.map((s) =>
        s.id !== sid ? s : { ...s, messages: s.messages.map((m) => (m.id === mid ? fn(m) : m)) }
      )
    )

  // 上下文压缩：把边界之后的所有消息交给压缩专家摘要；返回 {summary, upToIndex}
  // 规则：全部消息都可压缩——不保留最近预算、不按轮次保护，边界直接推到最新一条。
  //       压缩用专家模型（默认 Qwen3.5-2B），完成后切回会话模型。
  const doCompress = async (msgs, compacted) => {
    const from = compacted && compacted.upToIndex > 0 ? compacted.upToIndex : 0
    const keepFrom = msgs.length
    if (keepFrom <= from) return null // 可压区间为空
    const oldMsgs = msgs.slice(from, keepFrom)
    const material = [
      ...(compacted ? [{ role: 'assistant', content: `（此前摘要：${compacted.summary}）` }] : []),
      ...oldMsgs.map((m) => {
        const brief = m.role === 'assistant' ? briefToolCalls(m.toolCalls) : ''
        const content = [m.content, brief ? `（本回合工具：${brief}）` : ''].filter(Boolean).join('\n')
        return { role: m.role, content }
      }),
    ]
    const ctrl = new AbortController()
    const timer = setTimeout(() => ctrl.abort(), 180000)
    let r
    try {
      r = await fetch('/api/compress', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          messages: material,
          model: (experts && experts.compact) || modelKey, // 压缩专家（默认 Qwen3.5-2B）
          restore_model: modelKey, // 压缩完切回会话模型
        }),
        signal: ctrl.signal,
      }).then((x) => x.json())
    } finally {
      clearTimeout(timer)
    }
    if (!r.ok) throw new Error(r.error || '压缩失败')
    return { summary: r.summary, upToIndex: keepFrom }
  }

  // 手动压缩：点顶栏"压缩"立刻把现有全部历史压成摘要（所有消息都可压）
  const manualCompress = async () => {
    if (busy) return
    const msgs = session.messages
    const compacted = session.compacted || null
    markBusy(session.id, true)
    setActivity({ kind: 'compressing', label: '正在压缩历史…' })
    setToast('正在手动压缩历史…')
    const t0 = Date.now()
    try {
      const result = await doCompress(msgs, compacted)
      if (result) {
        setSessions((prev) => prev.map((s) => (s.id === session.id ? { ...s, compacted: result } : s)))
        const removed = result.upToIndex - (compacted && compacted.upToIndex > 0 ? compacted.upToIndex : 0)
        setToast(`已压缩 ${removed} 条消息为摘要（界面记录不受影响）`)
      } else {
        setToast('没有可压缩的内容（已全部压缩）')
      }
    } catch (e) {
      setToast('压缩失败：' + String(e))
    } finally {
      markBusy(session.id, false)
      setActivity({ kind: 'idle', label: '就绪' })
      setTimeout(() => setToast(''), 2600)
      console.log('手动压缩耗时', ((Date.now() - t0) / 1000).toFixed(1) + 's')
    }
  }

  // ---------- 上下文阀门 ----------
  // 「会话未中断时达到阀门」的正确动作不是硬发超限请求，而是：
  //   暂停当前会话（中止正在跑的流）→ 压缩历史 → 从断点继续。
  // 这样用户看不到「上下文超限」报错，也看不到重复输出。
  const ctxTriggerRatio = () => parseFloat(localStorage.getItem('claw_ctx_trigger')) || 0.9

  // 一次请求的真实携带量（历史 + 注入开销 + 本轮的输入/已生成内容）
  const payloadTokens = (msgs, compacted, extraText) =>
    estimateTokens(buildPayload(msgs, compacted)) + estimateMsgTokens({ content: extraText || '' }) + overhead

  const RESUME_HINT = '（上下文已压缩，请从中断的地方继续输出，不要重复已经写过的内容。）'

  // 暂停后「压缩 → 继续」的收尾动作
  const resumeAfterPause = async (pend) => {
    const { sid, asstMsgId, compacted } = pend
    // 取界面里的最新会话（闭包里的 msgs 是发起这一轮之前的状态，不能用）
    const live = (sessionsRef.current || []).find((s) => s.id === sid)
    const conv = live ? live.messages : (pend.msgs || [])
    setActivity({ kind: 'compressing', label: '上下文已达阀门：已暂停会话，正在压缩历史…' })
    setToast('上下文已达阀门 · 已暂停 → 压缩中…')
    let next = compacted
    try {
      const r = await doCompress(conv, compacted)
      if (r) {
        next = r
        setSessions((prev) => prev.map((s) => (s.id === sid ? { ...s, compacted: r } : s)))
      }
    } catch (e) {
      console.warn('自动压缩失败：', e)
    }
    // 护栏：压缩没产出新摘要（可压区间为空）时，不能让它再触发一次暂停，否则
    // 「暂停 → 压缩 → 继续 → 又到阀门 → 再暂停」会转圈。所以剩余暂停次数清零。
    const progressed = next !== compacted
    const pauseLeft = progressed ? (pend.pauseLeft != null ? pend.pauseLeft : 2) : 0
    // 已写出来的内容留在界面上（不丢），标成「已暂停·待续」，随后的续跑接着它继续写。
    // 注意：这份 marked 同时会作为续跑的 baseMessages —— 里面带着半截正文（done=true 才会
    // 进 payload），模型才知道自己写到了哪里，否则会从头重复。
    const marked = conv.map((m) => (m.id === asstMsgId ? { ...m, done: true, paused: true } : m))
    setSessions((prev) =>
      prev.map((s) =>
        s.id !== sid
          ? s
          : {
              ...s,
              messages: s.messages.map((m) => (m.id === asstMsgId ? { ...m, done: true, paused: true } : m)),
            }
      )
    )
    setToast(next !== compacted ? '历史已压缩，继续这次会话…' : '压缩未产出摘要，直接继续…')
    setTimeout(() => setToast(''), 2000)
    markBusy(sid, false) // send 内部会重新置 true（send 的闭包里 busy 仍是这一轮开始前的 false，不会自锁）
    await send(RESUME_HINT, marked, { resume: true, compacted: next, pauseLeft })
  }

  const send = async (text, baseMessages = null, opts = {}) => {
    const t = String(text || '').trim()
    if ((!t && pendingImages.length === 0) || busy) return
    const sid = session.id
    // resume=true：这是「压缩后续跑」，续跑提示只发给模型，不在界面上多出一条用户消息
    const resume = !!opts.resume
    userStoppedRef.current = false
    markBusy(sid, true)
    setActivity({ kind: 'preparing', label: resume ? '继续处理…' : '等待模型响应…' })

    // 上下文压缩管理：按"实际携带量"判断，达到触发比例（默认 0.9）时压缩
    const msgs = baseMessages || session.messages
    let compacted = opts.compacted !== undefined ? opts.compacted : (session.compacted || null)
    const trigger = ctxTriggerRatio()
    try {
      const used = payloadTokens(msgs, compacted, t)
      if (used >= effCtxSize * trigger) {
        // 会话还没开始就已经满：先「暂停」（无事可停）→ 压缩 → 继续发送
        setActivity({ kind: 'compressing', label: '上下文已达阀门，暂停会话并压缩历史…' })
        setToast(`上下文已用 ${Math.round((used / effCtxSize) * 100)}% · 暂停会话 → 压缩 → 继续`)
        const result = await doCompress(msgs, compacted)
        if (result) {
          compacted = result
          setSessions((prev) => prev.map((s) => (s.id === sid ? { ...s, compacted: result } : s)))
          setToast('历史已压缩，继续这次会话…')
          setTimeout(() => setToast(''), 1800)
        } else {
          setToast('')
        }
      }
    } catch (e) {
      console.warn('上下文压缩失败，继续发送：', e)
      setToast('')
    }

    const userMsg = { id: genId(), role: 'user', origin: 'boss', content: t, images: pendingImages.length ? [...pendingImages] : undefined }
    const asstMsg = { id: genId(), role: 'assistant', origin: 'claw', content: '', reasoning: '', toolCalls: [], done: false }

    // 续跑：不新增用户气泡，直接接着上一次那条回答继续写
    let contMsg = asstMsg
    if (resume) {
      const last = msgs[msgs.length - 1]
      if (last && last.role === 'assistant') {
        contMsg = { ...last, done: false, paused: false }
      }
    }

    // 发给模型的载荷：续跑时把已写出的半截正文一起带上（模型才知道写到哪，不然会重复），
    // 再把「从中断处继续」的提示放在最后（界面上不显示成一条用户消息）。
    const history = buildPayload(msgs, compacted)
    history.push({ role: 'user', content: t })

    setSessions((prev) =>
      prev.map((s) => {
        if (s.id !== sid) return s
        if (resume) {
          return {
            ...s,
            messages: s.messages.map((m) => (m.id === contMsg.id ? contMsg : m)),
            updatedAt: Date.now(),
          }
        }
        return {
          ...s,
          title: s.messages.length === 0 ? (t || '图片').slice(0, 18) : s.title,
          messages: [...msgs, userMsg, asstMsg],
          updatedAt: Date.now(),
        }
      })
    )
    if (!resume) {
      setDraft('')
      setPendingImages([])
    }
    markBusy(sid, true)

    // 模型参数覆盖（仅附带用户已自定义的项，其余走后端配置默认）
    const paramOverrides = {}
    for (const k of ['temperature', 'repeat_penalty', 'repeat_last_n']) {
      if (curParams[k] != null) paramOverrides[k] = curParams[k]
    }

    // 阀门检查用的基准：本次请求已携带的量 + 本轮已产出的字数
    const baseUsed = estimateTokens(history) + overhead
    let liveChars = 0
    let pausedForCompact = false
    // 允许的暂停次数（默认 2 次；压缩无进展时清零，防止来回转圈）
    const pauseLeft = opts.pauseLeft != null ? opts.pauseLeft : 2

    abortsRef.current[sid] = streamChat(
      {
        messages: history,
        model: modelKey || undefined,
        session_id: sid,
        thinking: isCloudModel ? undefined : thinkLevel !== 0,
        reasoning_budget_tokens: isCloudModel ? undefined : thinkLevel,
        reasoning_effort: isCloudModel && thinkLevel ? String(thinkLevel) : undefined,
        max_steps: curParams.max_steps != null ? curParams.max_steps : undefined,
        max_tokens: curParams.max_tokens || undefined,
        params: paramOverrides,
        permission,
        mode: agentMode,
        workdir,
        unattended: unattended || undefined,
        unattended_minutes: unattended ? unattendedMinutes : undefined,
        images: !resume && pendingImages.length ? pendingImages : undefined,
      },
      (ev) => {
        // ---- 会话未中断时达到阀门 → 暂停 → 压缩 → 继续 ----
        // 边收边算真实携带量：越线就立刻中止本轮流（后端 SSE 断开会停掉推理），
        // 然后压掉旧历史、带着「从中断处继续」的提示续跑。用户不会看到超限报错。
        if (!pausedForCompact && pauseLeft > 0) {
          if (ev.type === 'content') liveChars += (ev.text || '').length
          if ((ev.type === 'content' || ev.type === 'step' || ev.type === 'tool_call')
              && baseUsed + liveChars >= effCtxSize * trigger) {
            pausedForCompact = true
            if (abortsRef.current[sid]) {
              abortsRef.current[sid]()
              delete abortsRef.current[sid]
            }
            setActivity({ kind: 'compressing', label: '上下文已达阀门：暂停会话…' })
            return
          }
        }
        if (ev.type === 'reasoning') {
          // 思考按「真实时序」落到 timeline：同一段连续思考就接着追加，中间插了工具/正文就另起一段
          patchAssistant(sid, contMsg.id, (m) => {
            const tl = (m.timeline || []).slice()
            const last = tl[tl.length - 1]
            if (last && last.kind === 'reasoning') {
              tl[tl.length - 1] = { ...last, text: last.text + ev.text }
            } else {
              tl.push({ kind: 'reasoning', text: ev.text, step: m.stepNow ?? 0 })
            }
            return { ...m, timeline: tl, reasoning: (m.reasoning || '') + ev.text }
          })
        } else if (ev.type === 'content') {
          setActivity((a) => (a.kind === 'writing' ? a : { kind: 'writing', label: '生成回复中' }))
          patchAssistant(sid, contMsg.id, (m) => {
            const tl = (m.timeline || []).slice()
            const last = tl[tl.length - 1]
            if (last && last.kind === 'content') {
              tl[tl.length - 1] = { ...last, text: last.text + ev.text }
            } else {
              tl.push({ kind: 'content', text: ev.text, step: m.stepNow ?? 0 })
            }
            return { ...m, timeline: tl, content: (m.content || '') + ev.text }
          })
        } else if (ev.type === 'step') {
          setActivity({
            kind: 'thinking',
            label: ev.final ? '正在收尾…' : `思考中 · 第 ${(ev.step || 0) + 1} 步`,
          })
          patchAssistant(sid, contMsg.id, (m) => ({ ...m, stepNow: ev.step || 0 }))
        } else if (ev.type === 'tool_call') {
          setActivity({ kind: 'tool', tool: ev.name, label: `正在执行 ${toolLabel(ev.name)}` })
          patchAssistant(sid, contMsg.id, (m) => ({
            ...m,
            timeline: [...(m.timeline || []), { kind: 'tool', id: ev.id, name: ev.name, arguments: ev.arguments, step: m.stepNow ?? 0 }],
            toolCalls: [...(m.toolCalls || []), { id: ev.id, name: ev.name, arguments: ev.arguments, status: 'running' }],
          }))
        } else if (ev.type === 'tool_result') {
          setActivity({ kind: 'thinking', label: '工具完成，继续推理…' })
          patchAssistant(sid, contMsg.id, (m) => ({
            ...m,
            toolCalls: (m.toolCalls || []).map((tc) =>
              tc.id === ev.id ? { ...tc, status: 'done', ok: ev.ok, seconds: ev.seconds, output: ev.output } : tc
            ),
          }))
        } else if (ev.type === 'notice') {
          setActivity({ kind: 'thinking', label: String(ev.text || '正在自动恢复…').slice(0, 60) })
          patchAssistant(sid, contMsg.id, (m) => ({
            ...m,
            notices: [...(m.notices || []), ev.text],
            timeline: [...(m.timeline || []), { kind: 'notice', text: ev.text, step: m.stepNow ?? 0 }],
          }))
        } else if (ev.type === 'image_desc') {
          // 图片识别完成：识别文本随该条用户消息持久化（历史回放保持一致）
          patchAssistant(sid, userMsg.id, (m) => ({ ...m, imageNote: ev.note }))
        } else if (ev.type === 'done') {
          setActivity({ kind: 'idle', label: unattended ? '任务完成 · 已暂停并汇报' : '就绪' })
          loadQuota() // 刷新各接入方的额度与用量（10 分钟缓存，过期才真正查询）
          patchAssistant(sid, contMsg.id, (m) => ({
            ...m,
            done: true,
            model: ev.model,
            steps: ev.steps,
            memorySaved: ev.memory_saved,
            ...(ev.stats ? { stats: ev.stats } : {}),
            ...(unattended
              ? { notices: [...(m.notices || []),
                  `无人监管（${unattendedMinutes === 0 ? '无限' : `${unattendedMinutes} 分钟`}）：已暂停并汇报`] }
              : {}),
          }))
        } else if (ev.type === 'error') {
          setActivity({ kind: 'error', label: `出错：${String(ev.message || '').slice(0, 60)}` })
          patchAssistant(sid, contMsg.id, (m) => ({ ...m, error: ev.message, done: true }))
        }
      },
      () => {
        delete abortsRef.current[sid]
        if (pausedForCompact && !userStoppedRef.current) {
          // 因为达到阀门而中止 → 压缩 → 从断点继续
          pausedForCompact = false
          resumeAfterPause({ sid, asstMsgId: contMsg.id, compacted, msgs, pauseLeft: pauseLeft - 1 })
          return
        }
        markBusy(sid, false)
        setActivity((a) => (a.kind === 'error'
          ? a
          : { kind: 'idle', label: unattended ? '已完成 · 已暂停并汇报' : '就绪' }))
      }
    )
  }

  const stop = () => {
    userStoppedRef.current = true      // 手动停止 → 不要触发「暂停后继续」
    const sid = activeId
    const abort = abortsRef.current[sid]
    if (!abort) return
    abort()
    delete abortsRef.current[sid]
    setSessions((prev) =>
      prev.map((s) =>
        s.id !== sid
          ? s
          : {
              ...s,
              messages: s.messages.map((m) =>
                m.role === 'assistant' && !m.done ? { ...m, done: true, stopped: true } : m
              ),
            }
      )
    )
    markBusy(sid, false)
    setActivity({ kind: 'idle', label: '已停止' })
    setTimeout(() => setActivity((a) => (a.kind === 'idle' && a.label === '已停止' ? { kind: 'idle', label: '就绪' } : a)), 2000)
  }

  const regenerate = () => {
    if (busy) return
    const msgs = session.messages
    let lastUserIdx = -1
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].role === 'user') {
        lastUserIdx = i
        break
      }
    }
    if (lastUserIdx < 0) return
    const base = msgs.slice(0, lastUserIdx)
    setSessions((prev) => prev.map((s) => (s.id === session.id ? { ...s, messages: base } : s)))
    send(msgs[lastUserIdx].content, base)
  }

  const deleteSession = (id) => {
    const idx = sessions.findIndex((s) => s.id === id)
    const next = sessions.filter((s) => s.id !== id)
    const final = next.length ? next : [newSession(workdir)]
    setSessions(final)
    if (id === activeId) {
      // 切到相邻会话（优先后一条，越界则前一条）
      setActiveId(final[Math.min(idx, final.length - 1)].id)
    }
  }

  const createSession = () => {
    // 当前已是空会话：不重复创建，聚焦输入框
    if (session.messages.length === 0) {
      taRef.current?.focus()
      return
    }
    const s = newSession(workdir, {
      agentMode,
      unattendedMinutes,
    })
    setSessions((prev) => [s, ...prev])
    setActiveId(s.id)
    setTimeout(() => taRef.current?.focus(), 50)
  }

  // 目录选择：写入当前会话（随会话持久化），后续同目录会话共享目录记忆
  const applyWorkdir = (path) => {
    const p = String(path || '').trim()
    if (!p || busy) return
    setSessions((prev) =>
      prev.map((s) => (s.id === session.id ? { ...s, workdir: p, updatedAt: Date.now() } : s))
    )
    setShowWorkdir(false)
  }

  const renameSession = (id, title) => {
    const t = String(title || '').trim()
    if (!t) return
    setSessions((prev) => prev.map((s) => (s.id === id ? { ...s, title: t } : s)))
  }

  // 侧栏会话项渲染（含 hover 操作 + 就地重命名）
  const renderSessionItem = (s) => {
    if (editingId === s.id) {
      return (
        <div key={s.id} className={`session-item editing ${s.id === activeId ? 'active' : ''}`}>
          <input
            className="session-rename"
            autoFocus
            defaultValue={s.title}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                renameSession(s.id, e.target.value)
                setEditingId(null)
              }
              if (e.key === 'Escape') setEditingId(null)
            }}
            onBlur={(e) => {
              renameSession(s.id, e.target.value)
              setEditingId(null)
            }}
          />
        </div>
      )
    }
    const isEmpty = s.messages.length === 0
    return (
      <div
        key={s.id}
        className={`session-item ${s.id === activeId ? 'active' : ''} ${isEmpty ? 'empty' : ''}`}
        onClick={() => setActiveId(s.id)}
        title="双击编辑标题"
      >
        <div className="si-top">
          <span
            className="si-icon"
            style={{
              color: iconColor(s.id),
              borderColor: iconColor(s.id) + '40',
              background: iconColor(s.id) + '17',
            }}
          >
            <IconChat size={12} />
          </span>
          <span
            className="s-title"
            onDoubleClick={(e) => {
              e.stopPropagation()
              setEditingId(s.id)
            }}
          >
            {s.title}
          </span>
          <span className="si-time">{fmtSessionTime(s.updatedAt)}</span>
        </div>
        <div className="si-preview">{sessionPreview(s)}</div>
        <div className="si-wd" title={s.workdir || defaultWorkdir}>
          <IconFolder size={10} />
          <span>{baseName(s.workdir || defaultWorkdir)}</span>
        </div>
        <span className="s-actions">
          <button
            title="重命名"
            onClick={(e) => {
              e.stopPropagation()
              setEditingId(s.id)
            }}
          >
            <IconEdit size={12} />
          </button>
          <button
            title="删除会话"
            onClick={(e) => {
              e.stopPropagation()
              deleteSession(s.id)
            }}
          >
            <IconTrash size={12} />
          </button>
        </span>
      </div>
    )
  }

  // 双击编辑用户消息：截断到该消息前，用新文本重新生成
  const editMessage = (mid, text) => {
    if (busy) return
    const msgs = session.messages
    const idx = msgs.findIndex((m) => m.id === mid)
    if (idx < 0 || msgs[idx].role !== 'user') return
    const base = msgs.slice(0, idx)
    setSessions((prev) => prev.map((s) => (s.id === session.id ? { ...s, messages: base } : s)))
    send(text, base)
  }

  // Esc 停止
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape' && busy) stop()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  // ---------- 模型切换 ----------
  const doSwitch = async (key) => {
    if (isCloudKey(key)) {
      setModelKey(key) // 云端模型：无需本地加载，立即生效
      return
    }
    setToast(`正在切换模型：${key}（加载中，约 10 秒）…`)
    try {
      const r = await switchModel(key)
      if (r.ok) {
        setModelKey(key)
        setToast('')
      } else {
        setToast(`切换失败：${r.error || '未知错误'}`)
        setTimeout(() => setToast(''), 4000)
      }
    } catch (e) {
      setToast(`切换失败：${e}`)
      setTimeout(() => setToast(''), 4000)
    }
  }

  // ---------- 输入 ----------
  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send(draft)
    }
  }
  const autoGrow = () => {
    const el = taRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 168) + 'px'
  }

  // 图片：选择/粘贴 → 压缩为 dataURI 入待发区（最多 4 张）
  const addImages = async (files) => {
    const list = [...(files || [])].filter((f) => f.type && f.type.startsWith('image/'))
    for (const f of list) {
      if (pendingImages.length >= 4) break
      try {
        const url = await fileToDataUrl(f)
        setPendingImages((prev) => (prev.length >= 4 ? prev : [...prev, url]))
      } catch (e) {
        /* 单张失败忽略 */
      }
    }
  }

  const onPaste = (e) => {
    const items = (e.clipboardData && e.clipboardData.items) || []
    const files = []
    for (const it of items) {
      if (it.kind === 'file' && it.type && it.type.startsWith('image/')) {
        const f = it.getAsFile()
        if (f) files.push(f)
      }
    }
    if (files.length) {
      e.preventDefault()
      addImages(files)
    }
  }

  // 上下文占用百分比（按实际发送载荷 + 注入开销估算；窗口随模型走——云端用云端声明的窗口）
  const ctxPct = useMemo(() => {
    const payload = buildPayload(session.messages, session.compacted)
    return Math.min(100, Math.round(((estimateTokens(payload) + overhead) / effCtxSize) * 100))
  }, [session, effCtxSize, overhead])

  // 空状态首屏的实时状态条（把「当前拿什么在跑」摆在眼前）
  const heroStats = useMemo(() => {
    const m = models.find((x) => x.key === modelKey)
    const name = m ? (m.name || m.key) : (modelKey || '未配置')
    const running = m ? m.current : false
    const permLabel = permission === 'readonly' ? '只读' : permission === 'full' ? '完全' : permission === 'ask' ? '询问' : '标准'
    const thinkLabel = isCloudModel
      ? (thinkLevel || '默认')
      : thinkLevel === 0 ? '关闭' : `${thinkLevel} tok`
    const list = [
      { k: 'model', v: name, tone: (isCloudModel || running) ? 'hot' : 'dim', title: isCloudModel ? '云端模型' : (running ? '本地模型（运行中）' : '本地模型（未加载）') },
      { k: 'context', v: `${ctxPct}% / ${Math.round(effCtxSize / 1024)}K`, tone: ctxPct >= 90 ? 'err' : ctxPct >= 75 ? 'warn' : 'dim', title: '上下文占用 / 可用窗口' },
      { k: 'think', v: thinkLabel, tone: 'dim', title: '思考预算' },
      { k: 'permission', v: permLabel, tone: 'dim', title: '权限档位' },
      { k: 'workdir', v: workdirName, tone: 'dim', title: `会话工作目录：${workdir}` },
    ]
    return list
  }, [models, modelKey, ctxPct, effCtxSize, thinkLevel, isCloudModel, permission, workdir])

  return (
    <div className={`app ${sideCollapsed ? 'side-collapsed' : ''}`}>
      {/* 侧栏 */}
      <div className="sidebar">
        <div className="brand">
          <span className="logo-glow"><CrabMark size={22} /></span>
          CLAW STUDIO
        </div>
        <button className="newchat-btn" onClick={createSession}>
          <IconPlus size={14} /> 新会话
        </button>
        <div className="session-search-wrap">
          <IconSearch size={13} />
          <input
            className="session-search"
            placeholder="搜索会话…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <div className="session-list">
          {query.trim()
            ? (() => {
                const q = query.trim().toLowerCase()
                const matches = sessions.filter(
                  (s) =>
                    s.title.toLowerCase().includes(q) ||
                    s.messages.some((m) => (m.content || '').toLowerCase().includes(q))
                )
                return matches.length ? (
                  matches.map(renderSessionItem)
                ) : (
                  <div className="session-empty-hint">没有匹配的会话</div>
                )
              })()
            : Object.entries(groupSessions(sessions)).map(([label, list]) =>
                list.length ? (
                  <div key={label}>
                    <div className="session-group-title">{label}</div>
                    {list.map(renderSessionItem)}
                  </div>
                ) : null
              )}
        </div>
        <div className="footer">
          <button className="footer-btn" onClick={() => setShowMemory(true)}>
            <IconMemory size={15} /> 记忆库
          </button>
          <button className="footer-btn" onClick={() => setShowSkills(true)}>
            <IconBrain size={15} /> 技能库
          </button>
        </div>
      </div>

      {/* 主区 */}
      <div className="main">
        <div className="header">
          <button
            className="icon-btn ghost"
            title={sideCollapsed ? '展开侧栏' : '折叠侧栏'}
            onClick={() => setSideCollapsed((v) => !v)}
          >
            <IconPanelLeft size={16} />
          </button>
          <span className="chat-title" title={session.title}>{session.title}</span>
          <span className="group-chip" title="单 agent：Claw 直接完成任务">
            <span className="group-dot" style={{ background: 'var(--violet)' }} />
            <span className="group-name">Claw</span>
            <span className="group-count">单 agent</span>
          </span>
          <span className="spacer" />
          {quotaProviders.map((p) => {
            const info = quotaChipInfo(p)
            if (!info) return null
            return (
              <span
                key={p.id}
                className="credits-chip"
                title={info.title}
                onClick={() => setShowSettings(true)}
              >
                {info.text}
              </span>
            )
          })}
          <button
            className="icon-btn ghost"
            title="设置"
            onClick={() => setShowSettings(true)}
          >
            <IconGear size={16} />
          </button>
          <span
            className={`ctx-meter ${ctxPct >= 90 ? 'red' : ctxPct >= 75 ? 'warn' : ''}`}
            title="上下文占用（达到 90% 自动压缩）。点右侧「压缩」立即手动压缩历史"
          >
            上下文 {ctxPct}%
          </span>
          <button
            className="ctx-compress"
            title="立刻把全部对话压缩成摘要（所有消息都可压，界面记录保留）"
            onClick={manualCompress}
            disabled={busy}
          >
            压缩
          </button>
          <button
            className="workdir"
            title={`会话工作目录：${workdir}\n点击切换（同目录会话共享记忆）`}
            onClick={() => setShowWorkdir(true)}
          >
            <span className={`dot-status ${busy ? 'busy' : 'off'}`} />
            <IconFolder size={12} />
            <span className="wd-chip-name">{workdirName}</span>
            <span className="wd-chip-full">{workdir}</span>
          </button>
        </div>

        <MessageList
          key={session.id}
          messages={session.messages}
          compacted={session.compacted}
          streaming={busy}
          hero={{
            stats: heroStats,
            toolCount,
            needSetup: models.length === 0,
            onSetup: () => {
              setSettingsPane('providers')
              setShowSettings(true)
            },
            onReload: () => {
              loadModels()
              setToast('已重新检测模型…')
              setTimeout(() => setToast(''), 2000)
            },
          }}
          onPickPrompt={(t) => {
            setDraft(t)
            taRef.current?.focus()
          }}
          onRegenerate={regenerate}
          onEdit={editMessage}
          onSpeak={speak}
          speakingId={speakingId}
        />

        <PlanPanel
          session={activeId}
          plan={plan}
          onRefresh={async () => {
            const d = await getPlan(activeId)
            if (d && d.ok) setPlan(d.plan)
          }}
        />
        <div className="inputbar">
          <div className={`status-bar ${activity.kind}`} title="模型当前状态">
            <span className="sb-dot" />
            {activity.tool && <span className="sb-icon">{toolIcon(activity.tool)}</span>}
            <span className="sb-label">{activity.label}</span>
          </div>
          {ask && (
            <AskCard key={ask.id} ask={ask} onSubmit={answerAskCard} onSkip={skipAskCard} />
          )}
          <div className={`input-outer${thinkAtTop ? (isCloudModel ? ' effort-ultra' : ' effort-max') : ''}`}>
            <EffortIgnition run={ignition} />
            {pendingImages.length > 0 && (
              <div className="img-previews">
                {pendingImages.map((src, i) => (
                  <div className="img-preview" key={i}>
                    <img src={src} alt="" />
                    <button
                      className="ip-del"
                      title="移除"
                      onClick={() => setPendingImages((prev) => prev.filter((_, j) => j !== i))}
                    >
                      <IconX size={11} />
                    </button>
                  </div>
                ))}
              </div>
            )}
            <div className="input-main">
              <textarea
                ref={taRef}
                rows={1}
                value={draft}
                placeholder="交代任务，或随便聊聊。它记得你。"
                onChange={(e) => {
                  setDraft(e.target.value)
                  autoGrow()
                }}
                onKeyDown={onKeyDown}
                onPaste={onPaste}
              />
              <button
                className={`icon-btn voice-toggle ${autoReadOn ? 'on' : ''}`}
                title={autoReadOn ? '自动朗读回复：开（点击关闭）' : '自动朗读回复：关（点击开启）'}
                onClick={toggleAutoRead}
              >
                {autoReadOn ? <IconVolume size={15} /> : <IconVolumeOff size={15} />}
              </button>
              {busy ? (
                <button className="icon-btn stop" title="停止（Esc）" onClick={stop}>
                  <IconStop size={15} />
                </button>
              ) : (
                <button className="icon-btn send" title="发送（Enter）" disabled={!draft.trim() && pendingImages.length === 0} onClick={() => send(draft)}>
                  <IconSend size={15} />
                </button>
              )}
            </div>
            <div className="input-tools">
              <button
                className="chip"
                title="添加图片（也可直接粘贴截图，最多 4 张）"
                disabled={busy}
                onClick={() => imgInputRef.current && imgInputRef.current.click()}
              >
                <IconImage size={13} />
                <span className="chip-name">图片</span>
              </button>
              <input
                ref={imgInputRef}
                type="file"
                accept="image/*"
                multiple
                style={{ display: 'none' }}
                onChange={(e) => {
                  addImages(e.target.files)
                  e.target.value = ''
                }}
              />
              <ModelMenu
                models={models}
                currentKey={modelKey}
                disabled={busy}
                onSwitch={doSwitch}
                thinkByModel={thinkByModel}
                onSetThink={(key, level) => setThinkByModel((prev) => ({ ...prev, [key]: level }))}
                dropUp
                onOpenParams={() => setShowParams(true)}
              />
              {(!isCloudModel || cloudThinkOpts.length > 0) && (
                <EffortTrigger
                  value={thinkLevel}
                  options={thinkOpts}
                  disabled={busy}
                  open={effortOpen}
                  onToggle={() => {
                    setEffortOpen((v) => !v)
                    setModeOpen(false)
                  }}
                  title={isCloudModel ? '思考档位（点击展开）' : '思考强度（点击展开）'}
                />
              )}
              <PermMenu
                value={permission}
                disabled={busy}
                onChange={setPermission}
                label={unattended ? '监管权限' : '值守权限'}
                title={unattended
                  ? '无人监管模式权限（与值守中分开保存）'
                  : '值守中专用权限（与无人监管分开保存）'}
              />
              <button
                type="button"
                className={`chip unattended-chip ${unattended ? 'on' : ''}`}
                onClick={() => {
                  setModeOpen((v) => !v)
                  setEffortOpen(false)
                }}
                disabled={busy}
                title={unattended
                  ? `无人监管 · ${unattendedMinutes === 0 ? '不限时' : unattendedMinutes + ' 分钟'}：自主决策、自行验证、收尾汇报（点击修改）`
                  : '值守中：你在看着，遇到岔路会问你（点击切换为无人监管）'}
              >
                <span className="unattended-dot" />
                <span className="chip-name">
                  {unattended
                    ? `无人监管 · ${unattendedMinutes === 0 ? '不限时' : `${unattendedMinutes}m`}`
                    : '值守中'}
                </span>
                <span className="chev"><IconChevron size={12} /></span>
              </button>
              <span className="tools-hint">
                <kbd>Enter</kbd> 发送 · <kbd>Shift+Enter</kbd> 换行 · <kbd>Esc</kbd> 停止
              </span>
            </div>
            {(!isCloudModel || cloudThinkOpts.length > 0) && (
              <EffortDrawer
                open={effortOpen}
                value={thinkLevel}
                options={thinkOpts}
                defaultKey={thinkDefault}
                onPick={(lv) => setThinkByModel((prev) => ({ ...prev, [modelKey]: lv }))}
                onReset={(lv) => setThinkByModel((prev) => ({ ...prev, [modelKey]: lv }))}
                onIgnite={() => setIgnition({ id: Date.now(), tier: isCloudModel ? 'ultra' : 'max' })}
              />
            )}
            <ModePopover
              open={modeOpen}
              mode={agentMode}
              minutes={unattendedMinutes}
              anchor="up"
              onPickMode={(k) => {
                setAgentMode(k)
                // 选值守就关掉；选无人监管时留在弹层里让你顺手定个时长
                if (k === 'onduty') setModeOpen(false)
              }}
              onPickMinutes={(n) => setUnattendedMinutes(Number(n))}
              onClose={() => setModeOpen(false)}
            />
          </div>
        </div>
      </div>

      {showParams && (
        <ParamsPanel
          modelKey={modelKey}
          params={curParams}
          onChange={setParam}
          onResetAll={resetParams}
          onClose={() => setShowParams(false)}
          onApplied={refreshStatus}
          models={models}
          experts={experts}
          onExpertsSaved={(ex) => setExperts(ex || {})}
          onVoiceChange={(cfg) => setVoiceCfg(cfg)}
          onQuotaUpdated={(id, d) =>
            setQuotaProviders((prev) =>
              prev.map((x) => (x.id === id ? { ...x, quota: d, usage: d.usage || x.usage } : x))
            )
          }
        />
      )}
      {showSettings && (
        <div className="settings-shield" role="dialog" aria-label="设置">
          <iframe className="settings-frame" title="设置" src={`/admin.html?embed=1${settingsPane ? `&pane=${settingsPane}` : ''}`} />
        </div>
      )}
      {showMemory && <MemoryPanel onClose={() => setShowMemory(false)} workdir={workdir} />}
      {showWorkdir && (
        <WorkdirPanel
          current={workdir}
          busy={busy}
          onPick={applyWorkdir}
          onClose={() => setShowWorkdir(false)}
        />
      )}
      {showSkills && <SkillsPanel onClose={() => setShowSkills(false)} />}
      {approvals.length > 0 && (
        <ApprovalCard
          item={approvals[0]}
          extra={approvals.length - 1}
          onDone={(id) => setApprovals((prev) => prev.filter((x) => x.id !== id))}
        />
      )}
      {toast && (
        <div className="blocking-toast">
          <span className="spinner" />
          {toast}
        </div>
      )}
    </div>
  )
}
