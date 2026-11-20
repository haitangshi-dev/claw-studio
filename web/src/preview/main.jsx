import React, { useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import '../theme.css'
import './preview.css'
import { CrabMark } from '../ui/brand'
import { streamChat } from '../api'
import ModelPicker from '../components/ModelPicker'

/**
 * 临时预览页：填一个 API Key 就能真的对话。
 *
 * 三条硬约束（对应用户的要求）：
 * 1. **不保存** —— Key 只发给后端放进内存，不写 config.json / settings.json / DPAPI。
 *    关掉页面或停掉服务就没了。
 * 2. **不保留数据** —— 后端以 --ephemeral 启动，数据根在系统临时目录，退出即删。
 *    这里的前端也不往 localStorage 写任何东西。
 * 3. **完整的推理后端** —— 走的是同一个 /api/chat：工具调用、审批闸门、任务规划、
 *    记忆注入全都一样，不是 mock。
 */

const EPH_ID = '__ephemeral__'

function now() {
  const d = new Date()
  const p = (n) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}`
}

function App() {
  const [baseUrl, setBaseUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [model, setModel] = useState('')
  const [models, setModels] = useState([])
  const [probing, setProbing] = useState(false)
  const [savedProviders, setSavedProviders] = useState([])
  const [usingSaved, setUsingSaved] = useState('')
  const [status, setStatus] = useState(null)
  const [busy, setBusy] = useState('')
  const [note, setNote] = useState('')
  const [msgs, setMsgs] = useState([])
  const [stream, setStream] = useState(false)
  const [approvals, setApprovals] = useState([])
  const [draft, setDraft] = useState('')
  const abortRef = useRef(null)
  const boxRef = useRef(null)

  useEffect(() => {
    fetch('/api/ephemeral')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setStatus(d))
      .catch(() => setStatus(null))
  }, [])

  // 已保存的供应商：直接拿来用，不用在预览页重新填 Key
  useEffect(() => {
    fetch('/api/providers')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setSavedProviders((d && d.providers) || []))
      .catch(() => setSavedProviders([]))
  }, [])

  useEffect(() => {
    if (boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight
  }, [msgs.length, stream])

  // 审批卡：与主界面同一条通道
  useEffect(() => {
    const tick = async () => {
      try {
        const r = await fetch('/api/approval/pending')
        const d = r.ok ? await r.json() : null
        setApprovals((d && d.items) || [])
      } catch (e) {
        /* 忽略 */
      }
    }
    const t = setInterval(tick, 900)
    tick()
    return () => clearInterval(t)
  }, [])

  // 拉模型列表：凭据只用于这一次请求，不写内存、不落盘
  const probeModels = async () => {
    if (!baseUrl.trim() || !apiKey.trim()) {
      setNote('先填 Base URL 和 API Key')
      setTimeout(() => setNote(''), 3000)
      return []
    }
    setProbing(true)
    const r = await fetch('/api/ephemeral/models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ baseUrl, apiKey }),
    })
      .then((x) => x.json())
      .catch(() => ({ ok: false, error: '请求失败' }))
    setProbing(false)
    if (!r.ok) {
      setNote(r.error || '获取模型列表失败')
      setTimeout(() => setNote(''), 5000)
      return []
    }
    const list = r.models || []
    setModels(list)
    if (list.length && !model) setModel(list[0])
    setNote(list.length ? `拉到 ${list.length} 个模型` : '这个端点返回了空列表，手动填模型名吧')
    setTimeout(() => setNote(''), 4000)
    return list
  }

  const connect = async () => {
    // 先探模型列表（探不到也能连，手填模型名即可）
    if (!models.length) await probeModels()
    setBusy('connect')
    const r = await fetch('/api/ephemeral', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ baseUrl, apiKey, model }),
    })
    .then((x) => x.json())
    .catch(() => ({ ok: false, error: '请求失败' }))
    setBusy('')
    if (r.ok) {
      setStatus(r)
      setUsingSaved('')
      setApiKey('')          // 立刻从输入框里抹掉，不留在 DOM 里
      setNote('已连接。Key 只在服务端内存中，未写入任何文件。')
    } else {
      setNote(r.error || '连接失败')
    }
    setTimeout(() => setNote(''), 5000)
  }

  const disconnect = async () => {
    await fetch('/api/ephemeral', { method: 'DELETE' }).catch(() => null)
    setStatus(null)
    setUsingSaved('')
    setNote('已断开，内存中的 Key 已清除')
    setTimeout(() => setNote(''), 4000)
  }

  const send = async () => {
    const text = draft.trim()
    if (!text || stream) return
    if (!status || !status.active) {
      setNote('先填好连接信息并点「连接」')
      setTimeout(() => setNote(''), 3000)
      return
    }
    const history = msgs
      .filter((m) => m.role === 'user' || (m.role === 'assistant' && m.text))
      .map((m) => ({ role: m.role, content: m.text }))
    setMsgs((prev) => [...prev, { role: 'user', text, at: now() }])
    setDraft('')
    setStream(true)
    const aid = 'a' + Date.now()
    setMsgs((prev) => [...prev, { role: 'assistant', text: '', at: now(), id: aid, tools: [] }])

    const patch = (fn) =>
      setMsgs((prev) => prev.map((m) => (m.id === aid ? fn(m) : m)))

    abortRef.current = streamChat(
      {
        messages: [...history, { role: 'user', content: text }],
        model: usingSaved || `api:${EPH_ID}/${model}`,
        agent: true,
        session_id: 'preview',
        permission: 'ask',
        max_steps: 12,
        max_tokens: 2048,
      },
      (ev) => {
        if (ev.type === 'content') {
          patch((m) => ({ ...m, text: m.text + (ev.text || '') }))
        } else if (ev.type === 'tool_call') {
          patch((m) => ({
            ...m,
            tools: [...(m.tools || []), { name: ev.name, args: ev.arguments, state: 'running' }],
          }))
        } else if (ev.type === 'tool_result') {
          patch((m) => ({
            ...m,
            tools: (m.tools || []).map((t, i) =>
              i === (m.tools || []).length - 1 && t.state === 'running'
                ? { ...t, state: ev.ok ? 'done' : 'failed', output: ev.output || ev.brief }
                : t
            ),
          }))
        } else if (ev.type === 'notice') {
          patch((m) => ({ ...m, notice: ev.text }))
        } else if (ev.type === 'error') {
          patch((m) => ({ ...m, error: ev.message }))
        }
      },
      () => {
        setStream(false)
        abortRef.current = null
      }
    )
  }

  const answer = async (item, action, reason = '') => {
    await fetch('/api/approval/answer', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: item.id, action, reason }),
    }).catch(() => null)
    setApprovals((prev) => prev.filter((x) => x.id !== item.id))
  }

  const connected = !!(usingSaved || (status && status.active))

  return (
    <div className="pv">
      <header className="pv-head">
        <div className="flex items-center gap-2.5">
          <CrabMark size={30} />
          <div>
            <div className="pv-title">Claw Studio · 临时预览</div>
            <div className="pv-sub">
              真实推理后端 · Key 只在内存 · 数据用完即删
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className={`pv-pill ${connected ? 'on' : ''}`}>
            {connected
              ? `已连接 · ${usingSaved || status.model || '未指定模型'}`
              : '未连接'}
          </span>
          {connected && (
            <button className="pv-btn ghost" onClick={disconnect}>
              断开并清除 Key
            </button>
          )}
        </div>
      </header>

      {!connected && (
        <section className="pv-connect">
          <div className="pv-connect-title">填一个 OpenAI 兼容端点，立刻开始</div>
          <div className="pv-connect-desc">
            Base URL 填到根或 <code>/v1</code> 都行（会自动归一化）。
            <b>API Key 只发到后端内存</b>：不写配置文件、不进加密存储、不进 localStorage，
            关掉页面即消失。服务端数据写在系统临时目录，进程退出整目录删除。
          </div>
          <div className="pv-grid">
            <label>
              <span>Base URL</span>
              <input
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder="https://api.deepseek.com/v1"
              />
            </label>
            <label>
              <span>API Key</span>
              <input
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="sk-..."
                autoComplete="off"
              />
            </label>
            <label>
              <span>
                模型名
              </span>
              <ModelPicker
                value={model}
                models={models}
                baseUrl={baseUrl}
                apiKey={apiKey}
                providerName="临时"
                probe={probeModels}
                onChange={(m, ms) => {
                  setModel(m)
                  setModels(ms)
                }}
              />
            </label>
            <button className="pv-btn primary" onClick={connect} disabled={busy === 'connect'}>
              {busy === 'connect' ? '连接中…' : '连接'}
            </button>
          </div>
          {savedProviders.length > 0 && (
            <div className="pv-saved">
              <div className="pv-saved-title">
                或者直接用已保存的供应商（密钥从本机加密存储读取，不需要在这里填）
              </div>
              <div className="pv-saved-list">
                {savedProviders.map((p) => (
                  <div key={p.id} className="pv-saved-group">
                    <div className="pv-saved-name">
                      <span className="mpk-item-vendor">{p.name}</span>
                      <code>{p.baseUrl}</code>
                    </div>
                    <div className="pv-saved-models">
                      {(p.models || (p.model ? [p.model] : [])).map((m) => {
                        const key = `api:${p.id}/${m}`
                        return (
                          <button
                            key={m}
                            type="button"
                            className={`pv-saved-model ${usingSaved === key ? 'on' : ''}`}
                            onClick={() => {
                              setUsingSaved(key)
                              setStatus(null)
                              setNote(`已选择 ${p.name} · ${m}`)
                              setTimeout(() => setNote(''), 3000)
                            }}
                          >
                            <b>{p.name}</b>
                            <span>{m}</span>
                          </button>
                        )
                      })}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
          {note && <div className="pv-note">{note}</div>}
        </section>
      )}

      {note && connected && <div className="pv-note float">{note}</div>}

      <section className="pv-chat">
        <div className="pv-msgs" ref={boxRef}>
          {!msgs.length && (
            <div className="pv-empty">
              {connected
                ? '连上了。说点什么吧——工具调用、审批、任务规划都会真实发生。'
                : '先在上面填好端点并连接。'}
            </div>
          )}
          {msgs.map((m, i) => (
            <div key={m.id || i} className={`pv-msg ${m.role}`}>
              <div className="pv-bubble">
                {m.text}
                {m.tools?.map((t, j) => (
                  <div key={j} className={`pv-tool ${t.state}`}>
                    <span className="pv-tool-name">{t.name}</span>
                    <span className="pv-tool-state">
                      {t.state === 'running' ? '执行中' : t.state === 'done' ? '完成' : '失败'}
                    </span>
                    {t.output ? (
                      <pre>{String(t.output).slice(0, 400)}</pre>
                    ) : null}
                  </div>
                ))}
                {m.notice && <div className="pv-notice">{m.notice}</div>}
                {m.error && <div className="pv-err">{m.error}</div>}
                {stream && m.role === 'assistant' && !m.text && !m.tools?.length && (
                  <span className="pv-typing">思考中…</span>
                )}
              </div>
              <div className="pv-at">{m.at}</div>
            </div>
          ))}
        </div>

        {approvals.map((a) => (
          <div key={a.id} className="pv-approval">
            <div className="pv-approval-head">
              <b>需要你确认</b>
              <span>
                {a.title} · {a.detail}
              </span>
            </div>
            <div className="flex gap-2">
              <button className="pv-btn primary" onClick={() => answer(a, 'allow')}>
                允许这次
              </button>
              <button className="pv-btn ghost" onClick={() => answer(a, 'deny', '用户拒绝')}>
                拒绝
              </button>
            </div>
          </div>
        ))}

        <div className="pv-input">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                send()
              }
            }}
            placeholder={connected ? '说点什么…（Enter 发送，Shift+Enter 换行）' : '先连接'}
            disabled={!connected}
          />
          {stream ? (
            <button className="pv-btn" onClick={() => abortRef.current && abortRef.current()}>
              停止
            </button>
          ) : (
            <button className="pv-btn primary" onClick={send} disabled={!connected || !draft.trim()}>
              发送
            </button>
          )}
        </div>
      </section>
    </div>
  )
}

createRoot(document.getElementById('root')).render(<App />)
