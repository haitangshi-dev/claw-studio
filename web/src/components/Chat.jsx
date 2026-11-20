import { Fragment, useEffect, useRef, useState } from 'react'
import { IconArrowDown, IconBrain, IconCheck, IconChevron, IconCopy, IconFolder, IconRefresh, IconTerminal, IconVolume, IconVolumeOff, toolIcon } from '../icons.jsx'
import { CrabMark } from '../ui/brand.jsx'
import { MiniMarkdown } from '../md.jsx'

const TOOL_LABELS = {
  run_shell: '执行命令', shell_jobs: '后台任务',
  file_read: '读文件', file_create: '新建文件', file_edit: '改文件', file_delete: '删文件', file_move: '移动文件', file_copy: '复制文件', file_search: '找文件',
  memory_search: '查记忆', memory_write: '写记忆', memory_get: '取记忆', memory_forget: '忘掉记忆', memory_pin: '固定记忆', memory_stats: '记忆统计', memory_tags: '记忆标签',
  skill_list: '列技能', skill_read: '读技能', skill_run: '跑技能',
  plugin_list: '列插件', browser_control: '浏览器', http_request: 'HTTP 请求', notify: '发通知', ask_user: '询问你',
};
const toolLabel = (name) => TOOL_LABELS[String(name || '').replace(/^mcp__claw-agent__/, '')] || String(name || '').replace(/^mcp__claw-agent__/, '');


// ---------- 思考折叠区 ----------
export function ReasoningBlock({ text, streaming }) {
  const [open, setOpen] = useState(true)
  const userToggled = useRef(false)

  useEffect(() => {
    if (!streaming && !userToggled.current) setOpen(false)
  }, [streaming])

  if (!text) return null
  return (
    <div className={`reasoning ${open ? 'open' : ''} ${streaming ? 'streaming' : ''}`}>
      <div
        className="r-head"
        onClick={() => {
          userToggled.current = true
          setOpen((v) => !v)
        }}
      >
        <span className="chev"><IconChevron size={13} /></span>
        <IconBrain size={14} />
        <span>思考{streaming ? '中…' : `（${text.length} 字）`}</span>
      </div>
      {open && <div className="r-body">{text}</div>}
    </div>
  )
}

// ---------- 工具卡片 ----------
export function ToolCard({ call }) {
  const [open, setOpen] = useState(false)
  const [secs, setSecs] = useState(0)
  const running = call.status === 'running'
  useEffect(() => {
    if (!running) { setSecs(0); return undefined }
    const t = setInterval(() => setSecs((s) => s + 1), 1000)
    return () => clearInterval(t)
  }, [running])
  const failed = call.status === 'done' && call.ok === false && false // ok 语义见下
  const dotCls = running ? 'busy' : call.ok === false ? '' : 'on'
  const dotStyle = call.ok === false && !running ? { background: 'var(--err)' } : undefined

  const dispName = toolLabel(call.name)

  const argsText = (() => {
    try {
      const raw = typeof call.arguments === 'string' ? call.arguments : JSON.stringify(call.arguments || {})
      const o = JSON.parse(raw || '{}')
      if (call.name === 'run_shell' && o.command) return `$ ${String(o.command)}`
      return Object.entries(o)
        .map(([k, v]) => `${k}=${String(v).slice(0, 60)}`)
        .join('  ')
    } catch {
      return String(call.arguments || '').replace(/\s+/g, ' ').slice(0, 90)
    }
  })()

  return (
    <div className={`toolcard ${call.ok === false && !running ? 'fail' : ''}`}>
      <div className="t-head" onClick={() => setOpen((v) => !v)}>
        <span className={`dot-status ${dotCls}`} style={dotStyle} />
        <span style={{ display: 'flex', color: 'var(--text-dim)' }}>{toolIcon(call.name)}</span>
        <span className="t-name">{dispName}</span>
        <span className="t-args">{argsText}</span>
        {call.seconds != null && <span className="t-time">{call.seconds}s</span>}
        <span className="chev" style={{ display: 'flex', color: 'var(--text-faint)', transform: open ? 'rotate(90deg)' : 'none', transition: 'transform 170ms ease' }}>
          <IconChevron size={13} />
        </span>
      </div>
      {open && call.output && <div className="t-body">{call.output}</div>}
    </div>
  )
}

// ---------- 按时序渲染 ----------
// 只是把「思考 / 工具 / 正文」按真实发生顺序摆开：思考仍是折叠卡、工具仍是卡片、正文照旧，
// 不再按类型分堆（原先是一整块思考 → 一串工具 → 最后正文）。
// 数据来自 App.jsx 收集的 msg.timeline；老消息没有该字段时回退到旧的三段式渲染。
function TimelineSegment({ seg, calls, live }) {
  if (seg.kind === 'notice')
    return (
      <div className="notice-line">
        <span className="src-badge framework">框架</span>
        {seg.text}
      </div>
    )
  if (seg.kind === 'tool') {
    const call =
      (calls || []).find((tc) => tc.id === seg.id) ||
      { id: seg.id, name: seg.name, arguments: seg.arguments, status: 'running' }
    return <ToolCard call={call} />
  }
  if (seg.kind === 'reasoning') return <ReasoningBlock text={seg.text} streaming={live} />
  if (seg.kind === 'content')
    return (
      <div className="content-body tl-out">
        <MiniMarkdown text={seg.text} streaming={live} />
        {live && <span className="caret" />}
      </div>
    )
  return null
}

// ---------- 数字员工头像 ----------
export function CrabFace({ size = 18 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M5.2 8.6C3.9 7.2 1.9 8 2.4 9.9c.4 1.5 1.9 1.9 2.9 1" stroke="#FFF6EC" strokeWidth="1.5" strokeLinecap="round" />
      <path d="M18.8 8.6c1.3-1.4 3.3-.6 2.8 1.3-.4 1.5-1.9 1.9-2.9 1" stroke="#FFF6EC" strokeWidth="1.5" strokeLinecap="round" />
      <circle cx="9" cy="12" r="2.1" fill="#FFF6EC" />
      <circle cx="15" cy="12" r="2.1" fill="#FFF6EC" />
      <circle cx="9" cy="12.4" r=".95" fill="#43282F" />
      <circle cx="15" cy="12.4" r=".95" fill="#43282F" />
      <path d="M9.8 16.2c1.5 1.1 2.9 1.1 4.4 0" stroke="#FFF6EC" strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  )
}

// ---------- 消息 ----------
// api:p_xxx/deepseek-v4-pro → deepseek-v4-pro；普通名字原样短化
function shortModel(model) {
  const s = String(model || '').trim()
  if (!s) return ''
  const tail = s.includes('/') ? s.split('/').pop() : s
  return tail.includes(':') ? tail.split(':').pop() : tail
}

export function MessageView({ msg, streaming, isLast, onRegenerate, onEdit, onSpeak, speaking }) {
  const [copied, setCopied] = useState(false)
  const [editing, setEditing] = useState(false)
  const [editText, setEditText] = useState('')

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(msg.content || '')
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch (e) {
      /* 忽略剪贴板失败 */
    }
  }

  if (msg.role === 'user') {
    if (editing) {
      return (
        <div className="msg-user">
          <div className="bubble bubble-edit">
            <textarea
              autoFocus
              value={editText}
              rows={Math.min(6, editText.split('\n').length + 1)}
              onChange={(e) => setEditText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  const t = editText.trim()
                  setEditing(false)
                  if (t && t !== msg.content && onEdit) onEdit(msg.id, t)
                }
                if (e.key === 'Escape') {
                  setEditing(false)
                }
              }}
            />
            <div className="bubble-edit-hint">Enter 保存并重新生成 · Esc 取消</div>
          </div>
        </div>
      )
    }
    return (
      <div className="msg-user">
        <div
          className="bubble"
          title={onEdit ? '双击编辑' : undefined}
          onDoubleClick={() => {
            if (!onEdit) return
            setEditText(msg.content)
            setEditing(true)
          }}
        >
          <div className="bubble-src"><span className="src-badge boss">老板</span></div>
          {msg.images && msg.images.length > 0 && (
            <div className="bubble-imgs">
              {msg.images.map((src, i) => (
                <img key={i} className="bubble-img" src={src} alt="" />
              ))}
            </div>
          )}
          {msg.content}
          {msg.imageNote && <div className="bubble-note">{msg.imageNote}</div>}
        </div>
      </div>
    )
  }
  if (msg.sender) {
    const s = msg.sender
    const role = String(s.role || '')
    const isSup = role.includes('主管')
    const isFw = role.includes('框架') || role.includes('系统')
    const kind = isSup ? 'supervisor' : isFw ? 'framework' : 'employee'
    const kindLabel = isSup ? '主管' : isFw ? '框架' : '员工'
    // 群聊标识：优先用后端随消息下发的部门，其次按 team_id 反查（历史消息没有 room 字段）
    const room = null   // 办公室功能已移除；历史群聊消息仍按 sender 字段渲染
    const roomName = s.room || (room ? room.name : '')
    const roomColor = room?.color || s.color || '#D9713A'
    const when = msg.at
      ? new Date(msg.at * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
      : ''
    return (
      <div className="msg-assistant msg-team" style={{ '--team-color': roomColor }}>
        <div className="avatar crab-avatar" style={{ background: s.color || 'var(--primary)' }}>
          <CrabMark size={22} />
        </div>
        <div className="body">
          <div className="team-head">
            <span className={`src-badge ${kind}`}>{kindLabel}</span>
            {roomName ? (
              <span
                className="team-room"
                title="所属部门"
                style={{ color: roomColor, borderColor: `${roomColor}55`, background: `${roomColor}14` }}
              >
                {roomName}
              </span>
            ) : null}
            <span className="team-name">{s.name || '员工'}</span>
            {s.step ? <span className="team-step">{s.step}/{s.total || s.step}</span> : null}
            {s.role && s.role !== kindLabel ? <span className="team-role">{s.role}</span> : null}
            {when ? <span className="team-time">{when}</span> : null}
          </div>
          <div className="content-body team-bubble">
            <MiniMarkdown text={msg.content || ''} />
          </div>
        </div>
      </div>
    )
  }
  const hasAnything =
    msg.reasoning || msg.content || (msg.toolCalls && msg.toolCalls.length) || (msg.notices && msg.notices.length) || msg.error
  if (!hasAnything && !streaming) return null

  // 新消息：把思考 / 工具 / 正文按真实发生顺序摆开（思考与工具各自仍是可折叠卡，正文照旧）。
  // 只在「思考与工具混合」时走时序渲染；纯思考或纯工具的老样子也用同一套，不额外包折叠条。
  const tl = Array.isArray(msg.timeline) && msg.timeline.length ? msg.timeline : null
  // 流式中的「正在写的那一段」：时间线里最后一段
  const tlLast = tl ? tl.length - 1 : -1
  const tlBusy = streaming && !msg.done && !msg.error

  return (
    <div className="msg-assistant">
      <div className="avatar crab-avatar brand-avatar">
        <CrabMark size={22} />
      </div>
      <div className="body">
        <div className="team-head">
          <span className="src-badge claw">{shortModel(msg.model) || 'Claw'}</span>
        </div>
        {tl ? (
          <>
            {tl.map((s, i) => (
              <TimelineSegment key={i} seg={s} calls={msg.toolCalls} live={tlBusy && i === tlLast} />
            ))}
            {tlBusy && tl.length === 0 && <span className="caret" />}
          </>
        ) : (
          <>
            <ReasoningBlock text={msg.reasoning} streaming={streaming && !msg.done && !msg.error && !msg.content} />
            {(msg.notices || []).map((n, i) => (
              <div key={i} className="notice-line">
                <span className="src-badge framework">框架</span>
                {n}
              </div>
            ))}
            {(msg.toolCalls || []).map((tc, i) => (
              <ToolCard key={tc.id || i} call={tc} />
            ))}
            {msg.content && (
              <div className={`content-body ${msg.compressed ? 'compressed' : ''}`}>
                <MiniMarkdown text={msg.content} streaming={streaming && !msg.done && !msg.error} />
                {streaming && !msg.done && !msg.error && <span className="caret" />}
              </div>
            )}
          </>
        )}
        {msg.error && (
          <div style={{ color: 'var(--err)', fontSize: '12.5px', fontFamily: 'var(--mono)' }}>
            错误：{msg.error}
          </div>
        )}
        {msg.done && (
          <div className="msg-meta">
            {msg.paused && <span style={{ color: 'var(--warn, #B06B14)' }}>上下文达阀门 · 已暂停待续</span>}
            {msg.model && <span>{msg.model}</span>}
            {msg.steps != null && <span>{msg.steps} 步</span>}
            {msg.stats && msg.stats.tps != null && <span>{msg.stats.tps} t/s</span>}
            {msg.stats && msg.stats.tokens ? <span>{msg.stats.tokens} tok</span> : null}
            {msg.memorySaved && <span>已写入记忆</span>}
            <div className="msg-actions">
              {msg.content ? (
                <button
                  className={`icon-btn mini ${speaking ? 'speaking' : ''}`}
                  title={speaking ? '停止朗读' : '朗读这条回复'}
                  onClick={() => onSpeak && onSpeak(msg)}
                >
                  {speaking ? <IconVolumeOff size={13} /> : <IconVolume size={13} />}
                </button>
              ) : null}
              {msg.content ? (
                <button className="icon-btn mini" title={copied ? '已复制' : '复制回复'} onClick={copy}>
                  {copied ? <IconCheck size={13} /> : <IconCopy size={13} />}
                </button>
              ) : null}
              {isLast && (
                <button className="icon-btn mini" title="重新生成" onClick={() => onRegenerate && onRegenerate()}>
                  <IconRefresh size={13} />
                </button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ---------- 界面内提问卡片（ask_user）----------
// 后端需要用户拍板时浮出：点选项或自拟一句话作答，答案回传给模型，本轮对话无缝继续。
export function AskCard({ ask, onSubmit, onSkip }) {
  const [custom, setCustom] = useState('')
  const [picked, setPicked] = useState(null)
  const [left, setLeft] = useState(Math.max(0, ask.expires_in || 0))
  const done = useRef(false)

  useEffect(() => {
    setCustom('')
    setPicked(null)
    done.current = false
    setLeft(Math.max(0, ask.expires_in || 0))
  }, [ask.id])

  useEffect(() => {
    if (!ask.expires_in) return
    const t = setInterval(() => setLeft((v) => (v <= 1 ? 0 : v - 1)), 1000)
    return () => clearInterval(t)
  }, [ask.id, ask.expires_in])

  const submit = (val) => {
    const v = String(val == null ? '' : val).trim()
    if (done.current) return
    done.current = true
    onSubmit && onSubmit(v)
  }

  const options = ask.options || []
  const pickedLabel = picked != null && options[picked] ? options[picked].label : ''
  const toggle = (i) => setPicked((p) => (p === i ? null : i))

  return (
    <div className="askcard" role="dialog" aria-label="需要你确认">
      <div className="ask-head">
        <span className="ask-badge">需要你确认</span>
        {ask.expires_in ? <span className="ask-timer">{left}s</span> : null}
      </div>
      <div className="ask-q">{ask.question}</div>
      {options.length > 0 && (
        <div className="ask-opts">
          {options.map((o, i) => (
            <button
              key={i}
              type="button"
              className={`ask-opt ${picked === i ? 'on' : ''}`}
              onClick={() => toggle(i)}
              onDoubleClick={() => submit(o.label)}
            >
              <span className="ask-opt-k">{i + 1}</span>
              <span className="ask-opt-main">
                <span className="ask-opt-label">{o.label}</span>
                {o.desc ? <span className="ask-opt-desc">{o.desc}</span> : null}
              </span>
            </button>
          ))}
        </div>
      )}
      {ask.allow_custom !== false && (
        <div className="ask-custom">
          <input
            value={custom}
            placeholder="或者自己写一句…"
            onChange={(e) => setCustom(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && custom.trim()) submit(custom)
            }}
          />
        </div>
      )}
      <div className="ask-actions">
        <button className="ask-btn ghost" type="button" onClick={() => onSkip && onSkip()}>
          忽略（改用文字列选项）
        </button>
        <button className="ask-btn ghost" type="button" onClick={() => submit(pickedLabel)} disabled={picked == null}>
          选择一项
        </button>
        <button className="ask-btn primary" type="button" disabled={!custom.trim()} onClick={() => submit(custom)}>
          提交
        </button>
      </div>
    </div>
  )
}

// ---------- 消息流 ----------
const PROMPTS = [
  { t: '看看代码', d: '分析工作目录的项目结构，指出可能的坑', s: '看一下当前工作目录的项目结构，说明它是做什么的，并指出你认为最容易出问题的地方', icon: <IconFolder size={14} />, kbd: '代码' },
  { t: '修个 bug', d: '定位原因、改掉、再跑一遍验证', s: '帮我找出这个项目里最明显的一个 bug，说明原因并修好，然后跑一次验证', icon: <IconTerminal size={14} />, kbd: '调试' },
  { t: '跑条命令', d: '执行并解释输出，不确定的先问我', s: '检查一下当前目录的依赖是否齐全，缺什么告诉我', icon: <IconRefresh size={14} />, kbd: '终端' },
  { t: '记住约定', d: '说一次，以后每个会话都遵守', s: '记住：这个项目的回答偏好是简洁直接、先给结论，改代码前先说改哪几个文件', icon: <IconBrain size={14} />, kbd: '记忆' },
]

export function MessageList({ messages, compacted, streaming, onPickPrompt, onRegenerate, onEdit,
                             onSpeak, speakingId, hero = null }) {
  const bottomRef = useRef(null)
  const listRef = useRef(null)
  const stickBottom = useRef(true)
  const [showJump, setShowJump] = useState(false)

  // 用户上滑时不强制拉回底部
  const onScroll = () => {
    const el = listRef.current
    if (!el) return
    stickBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    setShowJump(!stickBottom.current && messages.length > 0)
  }

  useEffect(() => {
    if (stickBottom.current) {
      bottomRef.current?.scrollIntoView({ block: 'end' })
    }
  })

  const jumpToBottom = () => {
    stickBottom.current = true
    setShowJump(false)
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }

  return (
    <>
      <div className="messages" ref={listRef} onScroll={onScroll}>
        <div className="msg-col">
          {messages.length === 0 ? (
            <div className="welcome">
              <div className="w-logo">
                <CrabMark size={82} />
              </div>
              <div className="w-title">Claw Studio</div>
              <div className="w-sub">本地优先 · {hero?.toolCount || '—'} 件工具 · 四层记忆 · 操作可审批</div>
              {hero?.stats?.length ? (
                <div className="w-stats">
                  {hero.stats.map((st) => (
                    <div className="w-stat" key={st.k} title={st.title || ''}>
                      <span className="w-stat-k">{st.k}</span>
                      <span className={`w-stat-v ${st.tone || ''}`}>{st.v}</span>
                    </div>
                  ))}
                </div>
              ) : null}
              {hero?.needSetup && (
                <div className="w-setup">
                  <div className="w-setup-head">
                    还没有接上任何模型
                  </div>
                  <div className="w-setup-desc">
                    填一个 OpenAI 兼容供应商（DeepSeek、OpenRouter、自建网关都行），
                    配好之后立刻就能对话。
                  </div>
                  <div className="w-setup-actions">
                    <button className="w-setup-btn primary" onClick={() => hero.onSetup && hero.onSetup()}>
                      配置 API 供应商
                    </button>
                    <button className="w-setup-btn" onClick={() => hero.onReload && hero.onReload()}>
                      我已配好，重新检测
                    </button>
                  </div>
                </div>
              )}
              <div className="w-cards" style={hero?.needSetup ? { opacity: 0.45, pointerEvents: 'none' } : undefined}>
                {PROMPTS.map((p) => (
                  <button key={p.t} className="w-card" onClick={() => onPickPrompt && onPickPrompt(p.s)}>
                    <span className="w-card-top">
                      <span className="w-card-t">{p.icon}{p.t}</span>
                      {p.kbd && <span className="w-card-tag">{p.kbd}</span>}
                    </span>
                    <span className="w-card-d">{p.d}</span>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((m, i) => (
              <Fragment key={m.id}>
                {compacted && compacted.upToIndex === i && i > 0 && (
                  <div className="compacted-divider">
                    <span>以上 {i} 条对话已压缩为摘要（模型携带摘要继续对话，本机保留全部记录）</span>
                    <details>
                      <summary>查看摘要</summary>
                      <div className="cd-body">{compacted.summary}</div>
                    </details>
                  </div>
                )}
                <MessageView
                  msg={m}
                  streaming={streaming && m.role === 'assistant' && !m.done}
                  isLast={i === messages.length - 1 && m.role === 'assistant' && m.done}
                  onRegenerate={onRegenerate}
                  onEdit={onEdit}
                  onSpeak={onSpeak}
                  speaking={speakingId === m.id}
                />
              </Fragment>
            ))
          )}
          {compacted && messages.length > 0 && compacted.upToIndex >= messages.length && (
            <div className="compacted-divider">
              <span>以上 {messages.length} 条对话已压缩为摘要（模型携带摘要继续对话，本机保留全部记录）</span>
              <details>
                <summary>查看摘要</summary>
                <div className="cd-body">{compacted.summary}</div>
              </details>
            </div>
          )}
          <div ref={bottomRef} style={{ height: 1 }} />
        </div>
      </div>
      {showJump && (
        <button className="jump-bottom show" onClick={jumpToBottom} title="回到底部">
          <IconArrowDown size={16} />
        </button>
      )}
    </>
  )
}
