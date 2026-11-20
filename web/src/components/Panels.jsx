import { useEffect, useMemo, useRef, useState } from 'react'
import {
  getMemory, getLoadParams, setLoadParams, setExperts, getSkills, getSkillDetail, deleteSkill,
  getPlugins, reloadPlugins, togglePlugin, deletePlugin, getUpdateInfo, checkUpdate, applyUpdate,
  getNotify, setNotify, openPath, getQuotaProviders, getProviderQuota,
  getPet, startPet, stopPet, setPetAuto,
  getWechat, startWechat, stopWechat, setWechatAuto,
  getVoice, saveVoice, fetchTts,
  setMemoryTags, patchMemory, getForgetCandidates, demoteMemory, pinMemory, promoteMemory,
} from '../api.js'
import { ClawLogo, IconBrain, IconCheck, IconChevron, IconGear, IconMemory, IconRefresh, IconShield, IconSliders, IconTrash, IconX } from '../icons.jsx'

export const THINK_LEVELS = [
  { k: 0, lab: '关', desc: '不思考，直接回答' },
  { k: 256, lab: '低', desc: '简要推理' },
  { k: 1024, lab: '中', desc: '常规任务（默认）' },
  { k: 4096, lab: '高', desc: '深度规划与代码' },
  { k: -1, lab: '无限', desc: '不限思考额度' },
]

// ---------- 模型选择器（输入框内芯片 · 左键切换模型 · 右键设置思考强度） ----------
const levelLabel = (k) => (THINK_LEVELS.find((l) => l.k === k) || { lab: String(k) }).lab

// 上下文窗口格式：1,000,000 → 1M；256,000 → 256K
const fmtCtx = (n) => {
  const v = Number(n) || 0
  if (v >= 1_000_000) return `${Number((v / 1_000_000).toFixed(1))}M`
  return `${Math.round(v / 1000)}K`
}

export function ModelMenu({ models, currentKey, disabled, onSwitch, thinkByModel, onSetThink, dropUp, onOpenParams }) {
  const [open, setOpen] = useState(false)
  const [thinkFor, setThinkFor] = useState(null) // 展开思考强度行的模型 key
  const ref = useRef(null)
  useEffect(() => {
    const onDoc = (e) => {
      if (ref.current && !ref.current.contains(e.target)) {
        setOpen(false)
        setThinkFor(null)
      }
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])
  const localModels = (models || []).filter((m) => !m.cloud)
  const cloudModels = (models || []).filter((m) => m.cloud)
  // 云端按提供方分组（顺序按出现先后）
  const cloudGroups = (() => {
    const by = {}
    const order = []
    for (const m of cloudModels) {
      const p = m.provider || 'workbuddy'
      if (!by[p]) {
        by[p] = []
        order.push(p)
      }
      by[p].push(m)
    }
    const LABEL = { api: 'API 供应商' }
    return order.map((p) => ({ provider: p, label: LABEL[p] || p, models: by[p] }))
  })()
  const displayName = ((models || []).find((m) => m.key === currentKey) || {}).name || currentKey
  const pick = (m) => {
    if (m.key !== currentKey) onSwitch(m.key)
    setOpen(false)
    setThinkFor(null)
  }
  return (
    <div className="model-select" ref={ref}>
      <button className="chip" onClick={() => setOpen((v) => !v)} disabled={disabled} title="左键切换模型 · 右键设置思考强度">
        <span className="dot-status on" />
        <span className="chip-name">{displayName}</span>
        <span className="chev"><IconChevron size={12} /></span>
      </button>
      {open && (
        <div className={`model-menu ${dropUp ? 'up' : ''}`}>
          {localModels.map((m) => {
            const cur = thinkByModel?.[m.key] ?? 1024
            return (
              <div
                key={m.key}
                className={`model-item ${m.key === currentKey ? 'current' : ''}`}
                onClick={() => {
                  if (thinkFor === m.key) return
                  pick(m)
                }}
                onContextMenu={(e) => {
                  e.preventDefault()
                  setThinkFor(thinkFor === m.key ? null : m.key)
                }}
              >
                <div className="mi-name">
                  {m.key}
                  {m.has_profile && <span className="tag-pill">档案</span>}
                  <span className="mi-think">思考 {levelLabel(cur)}</span>
                </div>
                <div className="mi-meta">
                  {m.source} · {m.size_mb} MB
                  {m.arch ? ` · ${m.arch}` : ''}
                  {m.ctx_train ? ` · 训练 ${Math.round(m.ctx_train / 1024)}K` : ''}
                </div>
                {thinkFor === m.key && (
                  <div className="think-row" onClick={(e) => e.stopPropagation()}>
                    <ThinkSlider
                      label="思考强度"
                      value={cur}
                      options={THINK_LEVELS}
                      onPick={(lv) => onSetThink(m.key, lv)}
                      ariaLabel={`${m.key} 思考强度`}
                    />
                  </div>
                )}
              </div>
            )
          })}
          {cloudGroups.map((g) => (
            <div key={g.provider}>
              <div className="mm-sep">
                云端 · {g.label}（{g.models.length}）
              </div>
              {g.models.map((m) => {
                const rate = m.free ? '免费' : (m.credits || '按量')
                const bits = [rate, m.tools ? '工具' : '', m.images ? '图片' : '', m.ctx ? fmtCtx(m.ctx) : ''].filter(Boolean)
                const opts = effortOptions(m)
                const curEffort = thinkByModel?.[m.key] ?? m.effort_default ?? ''
                const curLab = (opts.find((o) => o.k === curEffort) || {}).lab
                return (
                  <div
                    key={m.key}
                    className={`model-item cloud ${m.key === currentKey ? 'current' : ''}`}
                    onClick={() => {
                      if (thinkFor === m.key) return
                      pick(m)
                    }}
                    onContextMenu={(e) => {
                      e.preventDefault()
                      if (opts.length > 0) setThinkFor(thinkFor === m.key ? null : m.key)
                    }}
                  >
                    <div className="mi-name">
                      {m.name}
                      <span className="tag-pill tag-cloud">云端</span>
                      {m.free && <span className="tag-pill tag-free">免费</span>}
                      {opts.length > 0 && <span className="mi-think">思考 {curLab || '默认'}</span>}
                    </div>
                    <div className="mi-meta">{bits.join(' · ')}</div>
                    {thinkFor === m.key && opts.length > 0 && (
                      <div className="think-row" onClick={(e) => e.stopPropagation()}>
                        <ThinkSlider
                          label="思考档位"
                          value={curEffort}
                          options={opts}
                          onPick={(lv) => onSetThink(m.key, lv)}
                          ariaLabel={`${m.name} 思考档位`}
                        />
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          ))}
          <div className="mm-params" onClick={() => { onOpenParams && onOpenParams(); setOpen(false) }}>
            <IconSliders size={13} /> 采样与运行参数…
          </div>
          <div className="model-menu-hint">右键模型项可设置思考强度 · 云端模型走已配置的 API 供应商</div>
        </div>
      )}
    </div>
  )
}

// ---------- 思考强度选择（输入框内芯片 · 上弹面板；本地=预算档位，云端=按模型声明档位） ----------
export const EFFORT_LABELS = { off: '关', auto: '自动', low: '低', medium: '中', high: '高', xhigh: '极高', max: '最高' }
export const EFFORT_DESCS = {
  off: '不思考，直接回答',
  auto: '由模型自动选择思考强度',
  low: '轻量推理',
  medium: '中等推理',
  high: '深度推理',
  xhigh: '更强推理',
  max: '最强推理，最慢最贵',
}

/** 云端模型的可选思考档位（仅按上游声明；无声明 = 无控制）。 */
export function effortOptions(m) {
  const out = []
  if (m?.can_off) out.push({ k: 'off', lab: EFFORT_LABELS.off, desc: EFFORT_DESCS.off })
  for (const e of (m?.efforts || [])) out.push({ k: e, lab: EFFORT_LABELS[e] || e, desc: EFFORT_DESCS[e] || '' })
  return out
}

/**
 * 思考强度滑块本体（Codex 风格）：拖动调节，拉到最底触发点火特效。
 *
 * 用原生 range 承载拖动，天然带键盘操作与档位吸附；填充与游标由 CSS 变量染色。
 * 拖动过程中 props.value 更新有延迟，用 ref 记住「上次停留的档位」，
 * 保证「从非最高档拉到最高档」只触发一次特效。
 */
export function ThinkSlider({ value, options, disabled, onPick, onIgnite, label, ariaLabel }) {
  const opts = options && options.length ? options : THINK_LEVELS
  const last = opts.length - 1
  const curIdx = (() => {
    const i = opts.findIndex((l) => String(l.k) === String(value))
    return i >= 0 ? i : 0
  })()
  const seen = useRef(null)
  useEffect(() => {
    seen.current = curIdx
  }, [curIdx])

  const handleChange = (e) => {
    const i = Number(e.target.value)
    const was = seen.current == null ? curIdx : seen.current
    seen.current = i
    if (opts[i]) onPick(opts[i].k)
    if (i >= last && was < last && onIgnite) onIgnite()
  }

  const cur = opts[curIdx] || {}
  const fill = last > 0 ? (curIdx / last) * 100 : 100
  const atTop = curIdx >= last

  return (
    <div className={`think-slider${atTop ? ' at-top' : ''}${label ? ' has-label' : ''}`}>
      {label ? <span className="ts-pre">{label}</span> : <IconBrain size={13} />}
      <input
        className="ts-range"
        type="range"
        min={0}
        max={last}
        step={1}
        value={curIdx}
        disabled={disabled || opts.length < 2}
        onChange={handleChange}
        aria-label={ariaLabel || '思考强度'}
        style={{ '--ts-fill': `${fill}%` }}
      />
      <span className="ts-lab">{cur.lab || '默认'}</span>
    </div>
  )
}

export function ThinkMenu({ value, disabled, onChange, options, title, onIgnite }) {
  const opts = options && options.length ? options : THINK_LEVELS
  const cur = opts.find((l) => String(l.k) === String(value)) || {}
  return (
    <div
      className="think-menu-wrap"
      title={title || (cur.desc ? `思考强度：${cur.lab} —— ${cur.desc}` : '思考强度（思考预算）')}
    >
      <ThinkSlider
        value={value}
        options={opts}
        disabled={disabled}
        onPick={onChange}
        onIgnite={onIgnite}
        ariaLabel="思考强度"
      />
    </div>
  )
}


// ---------- 权限档位（只读 / 标准 / 完全） ----------
export const PERM_LEVELS = [
  { k: 'readonly', lab: '只读', desc: '不写文件；命令仅允许只读白名单' },
  { k: 'ask', lab: '询问', desc: '任何有副作用的操作都先问你（默认）' },
  { k: 'standard', lab: '标准', desc: '沙箱：写入限工作目录，危险命令拦截（默认）' },
  { k: 'full', lab: '完全', desc: '无沙箱：全盘读写，危险命令放行，谨慎使用' },
]

export function PermMenu({ value, disabled, onChange, label = '权限', title = '工具权限档位（沙箱）' }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)
  useEffect(() => {
    const onDoc = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])
  const cur = PERM_LEVELS.find((l) => l.k === value) || PERM_LEVELS[1]
  return (
    <div className="think-menu" ref={ref}>
      <button
        className={`chip perm-chip perm-${cur.k}`}
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
        title={title}
      >
        <IconShield size={13} />
        <span>{label} {cur.lab}</span>
        <span className="chev"><IconChevron size={12} /></span>
      </button>
      {open && (
        <div className="think-pop">
          {PERM_LEVELS.map((l) => (
            <button
              key={l.k}
              type="button"
              className={`think-pop-item ${value === l.k ? 'on' : ''}`}
              onClick={() => {
                onChange(l.k)
                setOpen(false)
              }}
            >
              <span className="tp-lab" style={{ width: 40 }}>{l.lab}</span>
              <span className="tp-desc">{l.desc}</span>
              {value === l.k && <IconCheck size={13} />}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ---------- 模型参数配置面板 ----------
const PARAM_DEFS = [
  { key: 'temperature', label: '温度', desc: '越高越随机，越低越确定', min: 0.05, max: 2, step: 0.05, def: 0.6, fmt: (v) => v.toFixed(2), cloud: true },
  { key: 'repeat_penalty', label: '重复惩罚', desc: '抑制复读（大于 1 生效，本地模型专属）', min: 1.0, max: 1.5, step: 0.01, def: 1.1, fmt: (v) => v.toFixed(2), cloud: false },
  { key: 'repeat_last_n', label: '惩罚窗口', desc: '参与惩罚的最近 token 数（本地模型专属）', min: 0, max: 512, step: 16, def: 64, fmt: (v) => String(v), cloud: false },
  { key: 'max_tokens', label: '最大输出', desc: '单轮回复的 token 上限（本地 32K 上下文下 16384 即实际拉满）', min: 256, max: 16384, step: 256, def: 16384, fmt: (v) => String(v), cloud: true },
  { key: 'max_steps', label: 'Agent 步数', desc: '工具循环最大步数；0 = 不限（模型自主收尾或手动停止）', min: 0, max: 50, step: 1, def: 0, fmt: (v) => (v === 0 ? '不限' : String(v)), cloud: true },
]

// 模型加载参数（改动后自动重启模型生效）
const LOAD_DEFS = [
  {
    key: 'ctx', label: '上下文长度',
    desc: '优先自动检测（读 GGUF 元数据）；也可手填任意值，常用 8K/16K/32K/64K',
    choices: [[8192, '8K'], [16384, '16K'], [32768, '32K'], [65536, '64K']],
    custom: true,
  },
  {
    key: 'kv', label: 'KV 量化',
    desc: 'KV 缓存精度：越低越省显存（32K 上下文的省显存关键）',
    choices: [['q4_0', 'Q4 省显存'], ['q8_0', 'Q8'], ['f16', 'F16 精度']],
  },
  {
    key: 'ngl', label: 'GPU 层数',
    desc: '全部 = 整个模型放 GPU 加速；纯 CPU 显著变慢',
    choices: [[999, '全部'], [0, '纯 CPU']],
  },
  {
    key: 'flash_attn', label: 'Flash Attention',
    desc: '注意力计算加速（推荐开启）',
    choices: [[true, '开'], [false, '关']],
  },
]

// 专家模型（全局设置）：压缩历史 / 图片识别，单实例用时切换
const EXPERT_DEFS = [
  { key: 'compact', label: '压缩专家', desc: '上下文压缩时负责把历史总结成摘要的模型' },
  { key: 'vision', label: '图片识别专家', desc: '识别图片内容（需带视觉投影 mmproj 的模型）' },
]

// 数字格式化：整数不带小数点，小数保留 2 位再去尾零
const fmtNum = (n) => {
  const v = Number(n || 0)
  return Number.isInteger(v) ? String(v) : v.toFixed(2).replace(/\.?0+$/, '')
}

// 额度卡：每家接入方一张（接一家多一套额度设置，字段由后端 provider 决定）
function QuotaCard({ p, busy, onRefresh }) {
  const q = p.quota && p.quota.ok ? p.quota : null
  const rows = Object.entries(p.usage || {}).sort((a, b) => (a[0] < b[0] ? 1 : -1))
  const [day, rec] = rows[0] || []
  const totalCalls = rows.reduce((s, [, r]) => s + (Number(r.calls) || 0), 0)
  const totalCredits = rows.reduce((s, [, r]) => s + (Number(r.credits) || 0), 0)
  const unit = p.unit || (q && q.unit) || 'credits'
  const st = p.status || {}
  return (
    <div className="cred-box" style={{ marginBottom: 10 }}>
      <div className="cred-total">
        {p.name}
        <span className="cred-n">{st.summary || (p.available ? '已接入' : '未就绪')}</span>
      </div>
      {!p.available && (
        <div className="cred-row"><span className="cred-pkg">{st.summary || '尚未接入或未登录'}</span></div>
      )}
      {q && (
        <>
          <div className="cred-row">
            <span className="cred-pkg">剩余额度</span>
            <span className="cred-num"><b>{q.remaining ?? '-'}</b> {unit}</span>
          </div>
          {q.plan && (
            <div className="cred-row">
              <span className="cred-pkg">套餐</span>
              <span className="cred-num">{q.plan}</span>
            </div>
          )}
          {(q.packages || []).map((k) => (
            <div className="cred-row" key={k.name}>
              <span className="cred-pkg">{k.name}</span>
              <span className="cred-num">{k.used}/{k.limit}（余 {k.remaining}）</span>
            </div>
          ))}
        </>
      )}
      {rec && (
        <div className="cred-row">
          <span className="cred-pkg">用量 {String(day).slice(5)}</span>
          <span className="cred-num">{rec.calls} 次 · {fmtNum(rec.credits)} {unit}</span>
        </div>
      )}
      {rows.length > 1 && (
        <div className="cred-row">
          <span className="cred-pkg">近 {rows.length} 天累计</span>
          <span className="cred-num">{totalCalls} 次 · {fmtNum(totalCredits)} {unit}</span>
        </div>
      )}
      {p.err && <div className="cred-row"><span className="cred-pkg">刷新失败：{p.err}</span></div>}
      {p.note && <div className="cred-row"><span className="cred-pkg">{p.note}</span></div>}
      <div style={{ display: 'flex', gap: 8, marginTop: 2, alignItems: 'center' }}>
        <button className="pf-reset" onClick={() => onRefresh(p)} disabled={busy}>
          {busy ? '查询额度中…' : '刷新额度'}
        </button>
      </div>
    </div>
  )
}

// ---------- 语音设置（Edge TTS 女声朗读） ----------
function VoiceSection({ onVoiceChange }) {
  const [v, setV] = useState(null)
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const audioRef = useRef(null)

  const load = () => {
    getVoice()
      .then((d) => {
        if (d && d.ok) setV(d)
        else setMsg('读取语音配置失败：' + ((d && d.error) || '未知错误'))
      })
      .catch((e) => setMsg('读取语音配置失败：' + String(e).slice(0, 100)))
  }

  useEffect(() => {
    load()
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

  const apply = async (patch) => {
    if (busy) return
    setBusy('save')
    setMsg('保存中…')
    try {
      const r = await saveVoice(patch)
      if (r && r.ok) {
        setV(r)
        if (onVoiceChange) onVoiceChange(r.config)
        setMsg('已保存')
      } else {
        setMsg('保存失败：' + ((r && r.error) || '未知错误'))
      }
    } catch (e) {
      setMsg('保存失败：' + String(e).slice(0, 100))
    } finally {
      setBusy('')
      setTimeout(() => setMsg(''), 3000)
    }
  }

  const preview = async () => {
    if (busy) return
    setBusy('preview')
    setMsg('正在合成试听…')
    try {
      const cfg = (v && v.config) || {}
      const blob = await fetchTts('你好，我是 Claw。这是当前女声的试听。', {
        voice: (cfg.tts || {}).voice,
      })
      const old = audioRef.current
      if (old) {
        try {
          old.pause()
        } catch {
          /* ignore */
        }
      }
      const url = URL.createObjectURL(blob)
      const au = new Audio(url)
      audioRef.current = au
      au.onended = () => {
        URL.revokeObjectURL(url)
        setMsg('')
      }
      await au.play()
      setMsg('正在试听…')
    } catch (e) {
      setMsg('试听失败：' + String((e && e.message) || e).slice(0, 120))
    } finally {
      setBusy('')
    }
  }

  if (!v) {
    return (
      <div className="param-field">
        <div className="pf-top"><span className="pf-label">语音</span><span className="pf-val">读取中…</span></div>
        {msg && <div className="param-loadmsg">{msg}</div>}
      </div>
    )
  }

  const cfg = v.config || {}
  const tts = cfg.tts || {}
  const voices = v.voices || []

  return (
    <>
      <div className="param-field">
        <div className="pf-top"><span className="pf-label">朗读女声</span><span className="pf-val">Edge TTS · 在线</span></div>
        <select
          className="expert-select"
          value={tts.voice || ''}
          disabled={!!busy}
          onChange={(e) => apply({ tts: { voice: e.target.value } })}
        >
          {voices.map((x) => (
            <option key={x.id} value={x.id}>{x.name} · {x.desc}</option>
          ))}
        </select>
        <div className="choice-row" style={{ marginTop: 8 }}>
          {[['-10%', '慢一点'], ['+0%', '正常'], ['+10%', '快一点']].map(([k, lab]) => (
            <button
              key={k}
              className={'choice-chip' + (tts.rate === k ? ' on' : '')}
              disabled={!!busy}
              onClick={() => apply({ tts: { rate: k } })}
            >
              {lab}
            </button>
          ))}
          <button className="pf-reset" onClick={preview} disabled={!!busy}>
            {busy === 'preview' ? '合成中…' : '试听'}
          </button>
        </div>
        <div className="pf-bottom"><span className="pf-desc">晓晓为默认女声；也可在每条回复下方点喇叭手动朗读</span></div>
      </div>

      <div className="param-field">
        <div className="pf-top">
          <span className="pf-label">自动朗读回复</span>
          <span className="pf-val">{tts.auto_read ? '开' : '关'}</span>
        </div>
        <div className="choice-row">
          <button
            className={'choice-chip' + (tts.auto_read ? ' on' : '')}
            disabled={!!busy}
            onClick={() => apply({ tts: { auto_read: true } })}
          >
            开
          </button>
          <button
            className={'choice-chip' + (!tts.auto_read ? ' on' : '')}
            disabled={!!busy}
            onClick={() => apply({ tts: { auto_read: false } })}
          >
            关
          </button>
        </div>
        <div className="pf-bottom"><span className="pf-desc">开启后，每条新回复生成完自动用所选女声朗读</span></div>
      </div>
      {msg && <div className="param-loadmsg">{msg}</div>}
    </>
  )
}

export function ParamsPanel({ modelKey, params, onChange, onResetAll, onClose, onApplied, models, experts, onExpertsSaved, onQuotaUpdated, onVoiceChange }) {
  const isCloud = String(modelKey || '').startsWith('api:')
  // 云端模型的真实上限（上游 /v3/config 提供）：输入窗口 = 压缩触发基准，输出上限 = 单轮生成上限
  const cloudMeta = isCloud ? ((models || []).find((m) => m.key === modelKey) || null) : null
  const [load, setLoad] = useState(null) // {params, defaults, overridden}
  const [loadMsg, setLoadMsg] = useState('')
  const [loadBusy, setLoadBusy] = useState(false)
  const [expertBusy, setExpertBusy] = useState('')
  const [expertMsg, setExpertMsg] = useState('')
  const [upd, setUpd] = useState(null)
  const [updMsg, setUpdMsg] = useState('')
  const [updBusy, setUpdBusy] = useState(false)
  const [updAvail, setUpdAvail] = useState(null)
  const [notifyCfg, setNotifyCfg] = useState(null)
  const [notifyMsg, setNotifyMsg] = useState('')
  const [openMsg, setOpenMsg] = useState('')
  const [quotaProviders, setQuotaProviders] = useState([]) // 额度提供方（接一家多一套额度设置）
  const [quotaBusyId, setQuotaBusyId] = useState('')        // 正在刷新的 provider id
  const [quotaMsg, setQuotaMsg] = useState('')             // 刷新结果提示
  const [pet, setPet] = useState(null)          // { running, can_start, auto_start, pid, dir, error }
  const [petMsg, setPetMsg] = useState('')
  const [petBusy, setPetBusy] = useState('')
  const [wx, setWx] = useState(null)            // { running, relay_running, worker_running, logged_in, bound_user, model, auto_start, ... }
  const [wxMsg, setWxMsg] = useState('')
  const [wxBusy, setWxBusy] = useState('')

  const loadWx = async () => {
    try {
      const d = await getWechat()
      if (d && d.ok !== false) setWx(d)
    } catch (e) {
      setWx((prev) => prev || { running: false, can_start: false, auto_start: false, error: String(e) })
    }
  }

  const toggleWxAuto = async (auto) => {
    setWxBusy('auto')
    setWxMsg('保存中…')
    try {
      const r = await setWechatAuto(auto)
      if (r.ok) {
        setWx(r)
        setWxMsg(auto ? '已开启：下次启动 Claw Agent 会自动拉起微信桥接' : '已关闭自动启动')
      } else {
        setWxMsg('失败：' + (r.error || '未知错误'))
      }
    } catch (e) {
      setWxMsg('失败：' + String(e))
    } finally {
      setWxBusy('')
      setTimeout(() => setWxMsg(''), 4000)
    }
  }

  const doStartWx = async () => {
    if (wxBusy) return
    setWxBusy('start')
    setWxMsg('正在启动…')
    try {
      const r = await startWechat()
      setWx(r)
      setWxMsg(r.running ? '微信桥接已启动（现在可以在微信里给我发消息了）' : '启动失败：' + (r.error || '未知错误'))
    } catch (e) {
      setWxMsg('启动失败：' + String(e))
    } finally {
      setWxBusy('')
      setTimeout(() => setWxMsg(''), 5000)
    }
  }

  const doStopWx = async () => {
    if (wxBusy) return
    setWxBusy('stop')
    setWxMsg('正在停止…')
    try {
      const r = await stopWechat()
      setWx(r)
      setWxMsg(r.running === false ? '微信桥接已停止' : '停止失败：' + (r.error || '未知错误'))
    } catch (e) {
      setWxMsg('停止失败：' + String(e))
    } finally {
      setWxBusy('')
      setTimeout(() => setWxMsg(''), 5000)
    }
  }

  const loadPet = async () => {
    try {
      const d = await getPet()
      if (d && d.ok !== false) setPet(d)
    } catch (e) {
      setPet((prev) => prev || { running: false, can_start: false, auto_start: false, error: String(e) })
    }
  }

  const togglePetAuto = async (auto) => {
    setPetBusy('auto')
    setPetMsg('保存中…')
    try {
      const r = await setPetAuto(auto)
      if (r.ok) {
        setPet(r)
        setPetMsg(auto ? '已开启：下次启动 Claw Agent 会自动拉起宠物' : '已关闭自动启动')
      } else {
        setPetMsg('失败：' + (r.error || '未知错误'))
      }
    } catch (e) {
      setPetMsg('失败：' + String(e))
    } finally {
      setPetBusy('')
      setTimeout(() => setPetMsg(''), 4000)
    }
  }

  const doStartPet = async () => {
    if (petBusy) return
    setPetBusy('start')
    setPetMsg('正在启动…')
    try {
      const r = await startPet()
      setPet(r)
      setPetMsg(r.running ? '宠物已启动（右下角应该出现它了）' : '启动失败：' + (r.error || '未知错误'))
    } catch (e) {
      setPetMsg('启动失败：' + String(e))
    } finally {
      setPetBusy('')
      setTimeout(() => setPetMsg(''), 5000)
    }
  }

  const doStopPet = async () => {
    if (petBusy) return
    setPetBusy('stop')
    setPetMsg('正在停止…')
    try {
      const r = await stopPet()
      setPet(r)
      setPetMsg(r.running === false ? '宠物已停止' : '停止失败：' + (r.error || '未知错误'))
    } catch (e) {
      setPetMsg('停止失败：' + String(e))
    } finally {
      setPetBusy('')
      setTimeout(() => setPetMsg(''), 5000)
    }
  }

  // 宠物运行状态：打开面板时拉一次，之后每 4 秒刷新（读心跳文件，开销可忽略）
  useEffect(() => {
    loadPet()
    const t = setInterval(loadPet, 4000)
    return () => clearInterval(t)
  }, [])

  // 微信桥接状态：同样读心跳文件，4 秒刷新一次
  useEffect(() => {
    loadWx()
    const t = setInterval(loadWx, 4000)
    return () => clearInterval(t)
  }, [])

  const applyNotify = async (patch) => {
    try {
      const r = await setNotify(patch)
      if (r.ok) {
        setNotifyCfg({
          enabled: r.enabled,
          on_done_min_seconds: r.on_done_min_seconds,
          wechat: r.wechat,
        })
        setNotifyMsg('已保存')
      } else {
        setNotifyMsg('失败：' + (r.error || '未知错误'))
      }
    } catch (e) {
      setNotifyMsg('失败：' + String(e))
    }
    setTimeout(() => setNotifyMsg(''), 3000)
  }

  const doOpenPath = async (target) => {
    const r = await openPath(target).catch(() => null)
    if (!r || !r.ok) {
      setOpenMsg((r && r.error) || '打开失败')
      setTimeout(() => setOpenMsg(''), 3000)
    }
  }

  const loadQuotaList = () => {
    getQuotaProviders()
      .then((d) => setQuotaProviders((d && d.providers) || []))
      .catch(() => {})
  }

  const refreshQuota = (p) => {
    if (quotaBusyId) return
    setQuotaBusyId(p.id)
    setQuotaMsg(`${p.name} 查询中（约 10~20 秒）…`)
    getProviderQuota(p.id, true)
      .then((d) => {
        setQuotaProviders((prev) =>
          prev.map((x) =>
            x.id === p.id
              ? { ...x, quota: d && d.ok ? d : x.quota, usage: (d && d.usage) || x.usage, err: d && d.ok ? '' : (d && d.error) || '未知错误' }
              : x
          )
        )
        setQuotaMsg(d && d.ok ? `${p.name} 额度已更新` : `刷新失败：${(d && d.error) || '未知错误'}`)
        if (d && d.ok && onQuotaUpdated) onQuotaUpdated(p.id, d)
      })
      .catch((e) => setQuotaMsg('刷新失败：' + String(e)))
      .finally(() => {
        setQuotaBusyId('')
        setTimeout(() => setQuotaMsg(''), 5000)
      })
  }

  useEffect(() => {
    loadQuotaList()
    getNotify()
      .then((d) => {
        if (d.ok) setNotifyCfg({ enabled: d.enabled, on_done_min_seconds: d.on_done_min_seconds })
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    getUpdateInfo()
      .then((d) => {
        if (d.ok) setUpd(d)
      })
      .catch(() => {})
  }, [])

  const doCheckUpdate = async () => {
    if (updBusy) return
    setUpdBusy(true)
    setUpdMsg('检查中…')
    try {
      const r = await checkUpdate()
      if (r.ok && r.available) {
        setUpdAvail(r)
        setUpdMsg(`发现新版本 ${r.latest.version}（当前 ${r.current.version}）：${r.latest.notes || ''}`)
      } else if (r.ok) {
        setUpdAvail(null)
        setUpdMsg(`已是最新版本（${r.current.version}）`)
      } else {
        setUpdMsg('检查失败：' + (r.error || '未知错误'))
      }
    } catch (e) {
      setUpdMsg('检查失败：' + String(e))
    } finally {
      setUpdBusy(false)
    }
  }

  const doApplyUpdate = async () => {
    if (updBusy) return
    if (!window.confirm('更新并重启应用？（数秒后自动重启完成切换）')) return
    setUpdBusy(true)
    setUpdMsg('正在复制新版本…')
    try {
      const r = await applyUpdate()
      setUpdMsg(r.ok ? `更新已启动（${r.from} → ${r.to}），应用即将自动重启…` : '更新失败：' + (r.error || '未知错误'))
    } catch (e) {
      setUpdMsg('更新失败：' + String(e))
    } finally {
      setUpdBusy(false)
    }
  }

  useEffect(() => {
    if (isCloud) return
    let alive = true
    getLoadParams(modelKey)
      .then((d) => {
        if (alive && d.ok) setLoad(d)
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [modelKey])

  const applyLoad = async (key, value) => {
    if (loadBusy) return
    const patch = key === 'kv' ? { kv_k: value, kv_v: value } : { [key]: value }
    setLoad((L) => (L ? { ...L, params: { ...L.params, ...patch } } : L)) // 乐观更新
    setLoadBusy(true)
    setLoadMsg('保存中…')
    try {
      const r = await setLoadParams(modelKey, patch)
      if (r.ok) {
        setLoad(r)
        setLoadMsg(r.restarted ? '已生效（模型已重启）' : '已保存（模型未加载，下次启动生效）')
        if (onApplied) onApplied()
      } else {
        setLoadMsg('失败：' + (r.error || '未知错误'))
        getLoadParams(modelKey)
          .then((d) => {
            if (d.ok) setLoad(d)
          })
          .catch(() => {})
      }
    } catch (e) {
      setLoadMsg('失败：' + String(e))
    } finally {
      setLoadBusy(false)
      setTimeout(() => setLoadMsg(''), 4000)
    }
  }

  const loadCur = (key) => {
    if (!load) return null
    return key === 'kv' ? load.params.kv_k : load.params[key]
  }
  const loadDefLab = (d) => {
    if (!load) return ''
    const dv = d.key === 'kv' ? load.defaults.kv_k : load.defaults[d.key]
    const hit = d.choices.find(([v]) => v === dv)
    return hit ? hit[1] : String(dv)
  }

  const applyExpert = async (key, value) => {
    if (expertBusy) return
    setExpertBusy(key)
    setExpertMsg('保存中…')
    try {
      const r = await setExperts({ [key]: value })
      if (r.ok) {
        if (onExpertsSaved) onExpertsSaved(r.experts)
        setExpertMsg('已保存')
      } else {
        setExpertMsg('失败：' + (r.error || '未知错误'))
      }
    } catch (e) {
      setExpertMsg('失败：' + String(e))
    } finally {
      setExpertBusy('')
      setTimeout(() => setExpertMsg(''), 3000)
    }
  }

  return (
    <div className="overlay" onClick={onClose}>
      <div className="panel" onClick={(e) => e.stopPropagation()}>
        <div className="p-head">
          <span style={{ display: 'flex', color: 'var(--violet-2)' }}><IconSliders size={17} /></span>
          <span className="p-title">采样与运行参数</span>
          <span className="spacer" />
          <button className="icon-btn ghost" title="全部恢复默认" onClick={onResetAll}>
            <IconRefresh size={15} />
          </button>
          <button className="icon-btn ghost" title="关闭" onClick={onClose}>
            <IconX size={16} />
          </button>
        </div>
        <div className="p-body">
          <div className="param-note">
            这里只管<b>当前模型</b>的采样与加载参数。供应商、权限、微信、通知、外观这些
            <b>应用级设置</b>已经移到主窗口右上角齿轮里。
          </div>
          <div className="param-note">额度管理 · 每接一家给一套独立的额度设置</div>
          {quotaProviders.length === 0 && (
            <div className="cred-box">
              <div className="cred-row"><span className="cred-pkg">暂无额度提供方</span></div>
            </div>
          )}
          {quotaProviders.map((p) => (
            <QuotaCard key={p.id} p={p} busy={quotaBusyId === p.id} onRefresh={refreshQuota} />
          ))}
          {quotaMsg && (
            <div className="cred-box">
              <div className="cred-row"><span className="cred-pkg">{quotaMsg}</span></div>
            </div>
          )}
          <div className="param-sep" />
          <div className="param-sep" />
          <div className="param-note">
            当前模型：{modelKey}。以下采样参数按模型保存，修改后下一轮对话立即生效；显示"默认"的项使用框架配置值。
          </div>
          {PARAM_DEFS.filter((d) => (isCloud ? d.cloud : true)).map((rawDef) => {
            // 云端模型的输出上限按上游 maxOutputTokens 放开（如 GLM-5.3 = 64K、Space-Bunny = 128K），
            // 本地模型维持 16384（32K 上下文下已实际拉满）。
            const d = rawDef.key === 'max_tokens' && cloudMeta && cloudMeta.ctx_max_out
              ? { ...rawDef, max: Math.min(cloudMeta.ctx_max_out, 131072), def: Math.min(rawDef.def, cloudMeta.ctx_max_out),
                  desc: `单轮回复的 token 上限（该模型上游上限 ${Math.round(cloudMeta.ctx_max_out / 1000)}K）` }
              : rawDef
            const isCustom = params[d.key] != null
            const cur = isCustom ? params[d.key] : d.def
            return (
              <div className="param-field" key={d.key}>
                <div className="pf-top">
                  <span className="pf-label">{d.label}</span>
                  {isCustom && <span className="pf-custom">已自定义</span>}
                  <span className="pf-val">{d.fmt(cur)}</span>
                </div>
                <input
                  type="range"
                  min={d.min}
                  max={d.max}
                  step={d.step}
                  value={cur}
                  onChange={(e) => onChange(d.key, parseFloat(e.target.value))}
                />
                <div className="pf-bottom">
                  <span className="pf-desc">{d.desc}</span>
                  <span className="pf-def">默认 {d.fmt(d.def)}</span>
                  {isCustom && (
                    <button className="pf-reset" onClick={() => onChange(d.key, null)}>
                      恢复默认
                    </button>
                  )}
                </div>
              </div>
            )
          })}

          <div className="param-sep" />
          <div className="param-note">
            {isCloud
              ? (cloudMeta
                  ? `云端模型：上下文窗口 ${Math.round((cloudMeta.ctx_train || 0) / 1000)}K（上游真实输入上限，压缩按它触发）`
                    + (cloudMeta.ctx_max_out ? ` · 单轮输出上限 ${Math.round(cloudMeta.ctx_max_out / 1000)}K` : '')
                    + '；加载参数由服务端管理，此处不适用。'
                  : '云端模型：上下文窗口跟随上游配置（目录未拉到，按 200K 保守估算）。')
              : '模型加载参数：修改后自动重启该模型生效（约 7 秒）；模型未加载时仅保存，下次启动时应用。'}
          </div>
          {!isCloud && LOAD_DEFS.map((d) => {
            const cur = loadCur(d.key)
            const overridden =
              load && (d.key === 'kv' ? load.overridden.includes('kv_k') || load.overridden.includes('kv_v') : load.overridden.includes(d.key))
            return (
              <div className="param-field" key={d.key}>
                <div className="pf-top">
                  <span className="pf-label">{d.label}</span>
                  {overridden && <span className="pf-custom">已自定义</span>}
                </div>
                <div className="choice-row">
                  {d.custom && (
                    <button
                      className={'choice-chip' + (!overridden ? ' on' : '')}
                      disabled={loadBusy || !load}
                      onClick={() => { if (overridden) applyLoad(d.key, 'auto') }}
                      title="用模型 GGUF 元数据里的上下文长度"
                    >
                      自动检测{load?.auto_ctx ? ` ${Math.round(load.auto_ctx / 1024)}K` : ''}
                    </button>
                  )}
                  {d.choices.map(([v, lab]) => (
                    <button
                      key={String(v)}
                      className={'choice-chip' + (cur === v ? ' on' : '')}
                      disabled={loadBusy || !load}
                      onClick={() => {
                        if (cur !== v) applyLoad(d.key, v)
                      }}
                    >
                      {lab}
                    </button>
                  ))}
                </div>
                {d.custom && (
                  <div className="ctx-custom-row">
                    <input
                      type="number"
                      min={1024}
                      max={131072}
                      step={1024}
                      placeholder={load?.auto_ctx ? String(load.auto_ctx) : '自定义 token 数'}
                      defaultValue={overridden ? cur : ''}
                      disabled={loadBusy || !load}
                      onKeyDown={(e) => {
                        if (e.key !== 'Enter') return
                        const v = Number(e.target.value)
                        if (v >= 1024 && v <= 131072) applyLoad(d.key, v)
                      }}
                    />
                    <button
                      disabled={loadBusy || !load}
                      onClick={(e) => {
                        const v = Number(e.target.previousElementSibling.value)
                        if (v >= 1024 && v <= 131072) applyLoad(d.key, v)
                      }}
                    >
                      应用
                    </button>
                  </div>
                )}
                <div className="pf-bottom">
                  <span className="pf-desc">{d.desc}</span>
                  {load && <span className="pf-def">{overridden ? '自动 ' + (load.auto_ctx ? Math.round(load.auto_ctx/1024)+'K' : '默认') : '自动检测'}</span>}
                </div>
              </div>
            )
          })}
          <div className="param-sep" />
          <div className="param-note">
            专家模型：压缩历史与识别图片时使用（单实例串行——用时自动切换加载，任务完成自动切回会话模型）。
          </div>
          {EXPERT_DEFS.map((d) => (
            <div className="param-field" key={d.key}>
              <div className="pf-top">
                <span className="pf-label">{d.label}</span>
                {expertBusy === d.key && <span className="pf-custom">应用中…</span>}
              </div>
              <select
                className="expert-select"
                value={(experts && experts[d.key]) || ''}
                disabled={!!expertBusy}
                onChange={(e) => applyExpert(d.key, e.target.value)}
              >
                <option value="">（未设置）</option>
                {(models || []).map((m) => (
                  <option key={m.key} value={m.key}>
                    {m.cloud ? `${m.name}（云端）` : m.key}
                  </option>
                ))}
              </select>
              <div className="pf-bottom">
                <span className="pf-desc">{d.desc}</span>
              </div>
            </div>
          ))}
          {expertMsg && <div className="param-loadmsg">{expertMsg}</div>}
          {loadMsg && <div className="param-loadmsg">{loadMsg}</div>}

          <div className="param-sep" />
          <div className="param-note">
            框架更新（原生）：从更新源检查新版本，就地替换并自动重启。当前版本：
            <b> {upd?.current?.version || '…'}</b>
            {upd?.current?.built_at ? `（${upd.current.built_at}）` : ''}
          </div>
          <div style={{ display: 'flex', gap: 8, marginTop: 6 }}>
            <button className="pf-reset" onClick={doCheckUpdate} disabled={updBusy}>
              检查更新
            </button>
            {updAvail && (
              <button
                className="pf-reset"
                style={{ borderColor: 'var(--violet-border)', color: 'var(--violet-2)' }}
                onClick={doApplyUpdate}
                disabled={updBusy}
              >
                更新并重启（{updAvail.latest?.version}）
              </button>
            )}
          </div>
          {updMsg && <div className="param-loadmsg">{updMsg}</div>}

          <div className="param-sep" />
          <div className="param-note">完成通知：长任务跑完弹 Windows 通知（本机系统对未注册应用可能静默过滤）。</div>
          {notifyCfg && (
            <div className="param-field">
              <div className="pf-top">
                <span className="pf-label">完成通知</span>
                <span className="pf-val">{notifyCfg.enabled ? '开' : '关'}</span>
              </div>
              <div className="choice-row">
                <button className={'choice-chip' + (notifyCfg.enabled ? ' on' : '')} onClick={() => applyNotify({ enabled: true })}>开</button>
                <button className={'choice-chip' + (!notifyCfg.enabled ? ' on' : '')} onClick={() => applyNotify({ enabled: false })}>关</button>
              </div>
            </div>
          )}
          {notifyCfg && notifyCfg.enabled && (
            <div className="param-field">
              <div className="pf-top">
                <span className="pf-label">最小耗时</span>
                <span className="pf-val">{notifyCfg.on_done_min_seconds === 0 ? '每次' : notifyCfg.on_done_min_seconds + ' 秒+'}</span>
              </div>
              <div className="choice-row">
                {[0, 30, 60, 120, 300].map((s) => (
                  <button
                    key={s}
                    className={'choice-chip' + (notifyCfg.on_done_min_seconds === s ? ' on' : '')}
                    onClick={() => applyNotify({ on_done_min_seconds: s })}
                  >
                    {s === 0 ? '每次' : s + 's'}
                  </button>
                ))}
              </div>
              <div className="pf-bottom"><span className="pf-desc">任务运行超过该时长才通知</span></div>
            </div>
          )}
          {notifyCfg && (
            <div className="param-field">
              <div className="pf-top">
                <span className="pf-label">微信推送</span>
                <span className="pf-val">
                  {notifyCfg.wechat?.available === false
                    ? '未就绪'
                    : (notifyCfg.wechat?.enabled ? '开' : '关')}
                </span>
              </div>
              <div className="choice-row">
                <button
                  className={'choice-chip' + (notifyCfg.wechat?.enabled ? ' on' : '')}
                  onClick={() => applyNotify({ wechat: { enabled: true } })}
                  disabled={notifyCfg.wechat?.available === false}
                >
                  开
                </button>
                <button
                  className={'choice-chip' + (!notifyCfg.wechat?.enabled ? ' on' : '')}
                  onClick={() => applyNotify({ wechat: { enabled: false } })}
                >
                  关
                </button>
              </div>
              <div className="pf-bottom">
                <span className="pf-desc">
                  {notifyCfg.wechat?.available === false
                    ? '未找到微信桥接（workspace/wechat-bridge 未登录）'
                    : '每次任务完成，通过微信桥接发一条消息给你'}
                </span>
              </div>
            </div>
          )}
          {notifyCfg && notifyCfg.wechat?.enabled && (
            <div className="param-field">
              <div className="pf-top">
                <span className="pf-label">微信最小耗时</span>
                <span className="pf-val">{notifyCfg.wechat.min_seconds === 0 ? '每次' : notifyCfg.wechat.min_seconds + ' 秒+'}</span>
              </div>
              <div className="choice-row">
                {[0, 30, 60, 120, 300].map((s) => (
                  <button
                    key={s}
                    className={'choice-chip' + (notifyCfg.wechat.min_seconds === s ? ' on' : '')}
                    onClick={() => applyNotify({ wechat: { min_seconds: s } })}
                  >
                    {s === 0 ? '每次' : s + 's'}
                  </button>
                ))}
              </div>
              <div className="pf-bottom"><span className="pf-desc">任务运行超过该时长才推微信（0=每次）</span></div>
            </div>
          )}
          {notifyMsg && <div className="param-loadmsg">{notifyMsg}</div>}

          <div className="param-sep" />
          <div className="param-note">语音输出（Edge TTS 朗读）</div>
          <VoiceSection onVoiceChange={onVoiceChange} />

          <div className="param-sep" />
          <div className="param-note">桌面宠物（Claw 宠物 · 桌面上的小浮窗，会跟着我变状态）</div>
          {pet && (
            <>
              <div className="param-field">
                <div className="pf-top">
                  <span className="pf-label">运行状态</span>
                  <span className="pf-val" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span
                      className="pet-dot"
                      style={{ background: pet.running ? 'var(--ok)' : 'var(--text-faint)' }}
                    />
                    {pet.running ? `运行中${pet.pid ? `（PID ${pet.pid}）` : ''}` : '未运行'}
                  </span>
                </div>
                <div style={{ display: 'flex', gap: 8, marginTop: 2 }}>
                  <button
                    className="pf-reset"
                    style={pet.running ? undefined : { borderColor: 'var(--violet-border)', color: 'var(--violet-2)', display: 'flex', alignItems: 'center', gap: 6 }}
                    onClick={doStartPet}
                    disabled={!pet.can_start || pet.running || petBusy === 'start'}
                  >
                    {!pet.running && <ClawLogo size={13} twoTone />}
                    {pet.running ? '已启动' : petBusy === 'start' ? '启动中…' : '立即启动宠物'}
                  </button>
                  <button
                    className="pf-reset"
                    onClick={doStopPet}
                    disabled={!pet.running || petBusy === 'stop'}
                  >
                    {petBusy === 'stop' ? '停止中…' : '停止'}
                  </button>
                </div>
                {!pet.can_start && pet.error && (
                  <div className="pf-bottom"><span className="pf-desc" style={{ color: 'var(--err)' }}>{pet.error}</span></div>
                )}
              </div>
              <div className="param-field">
                <div className="pf-top">
                  <span className="pf-label">随 Claw Agent 启动</span>
                  <span className="pf-val">{pet.auto_start ? '开' : '关'}</span>
                </div>
                <div className="choice-row">
                  <button
                    className={'choice-chip' + (pet.auto_start ? ' on' : '')}
                    disabled={petBusy === 'auto'}
                    onClick={() => togglePetAuto(true)}
                  >
                    开
                  </button>
                  <button
                    className={'choice-chip' + (!pet.auto_start ? ' on' : '')}
                    disabled={petBusy === 'auto'}
                    onClick={() => togglePetAuto(false)}
                  >
                    关
                  </button>
                </div>
                <div className="pf-bottom"><span className="pf-desc">开启后，Claw Agent 启动约 6 秒自动把宠物拉起来（默认关）</span></div>
              </div>
            </>
          )}
          {petMsg && <div className="param-loadmsg">{petMsg}</div>}

          <div className="param-sep" />
          <div className="param-note">微信（微信直连本机 Claw Agent · 在微信里给我发消息，我在这台电脑上干活）</div>
          {wx && (
            <>
              <div className="param-field">
                <div className="pf-top">
                  <span className="pf-label">运行状态</span>
                  <span className="pf-val" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span
                      className="pet-dot"
                      style={{ background: wx.running ? 'var(--ok)' : 'var(--text-faint)' }}
                    />
                    {wx.running
                      ? `运行中${wx.pid ? `（PID ${wx.pid}）` : ''}`
                      : '未运行'}
                  </span>
                </div>
                <div style={{ display: 'flex', gap: 8, marginTop: 2 }}>
                  <button
                    className="pf-reset"
                    style={wx.running ? undefined : { borderColor: 'var(--violet-border)', color: 'var(--violet-2)', display: 'flex', alignItems: 'center', gap: 6 }}
                    onClick={doStartWx}
                    disabled={!wx.can_start || wx.running || wxBusy === 'start'}
                  >
                    {wx.running ? '已启动' : wxBusy === 'start' ? '启动中…' : '立即启动'}
                  </button>
                  <button
                    className="pf-reset"
                    onClick={doStopWx}
                    disabled={!wx.running || wxBusy === 'stop'}
                  >
                    {wxBusy === 'stop' ? '停止中…' : '停止'}
                  </button>
                </div>
                {wx.running && (
                  <div className="pf-bottom">
                    <span className="pf-desc">
                      收消息 {wx.relay_running ? '正常' : '未运行'} · 处理回复 {wx.worker_running ? '正常' : '未运行'}
                      {typeof wx.inbox_pending === 'number' && wx.inbox_pending > 0 ? ` · 待处理 ${wx.inbox_pending} 条` : ''}
                    </span>
                  </div>
                )}
                {!wx.can_start && wx.error && (
                  <div className="pf-bottom"><span className="pf-desc" style={{ color: 'var(--err)' }}>{wx.error}</span></div>
                )}
              </div>

              <div className="param-field">
                <div className="pf-top">
                  <span className="pf-label">微信登录</span>
                  <span className="pf-val">{wx.logged_in ? '已登录' : '未登录'}</span>
                </div>
                <div className="pf-bottom">
                  <span className="pf-desc">
                    {wx.logged_in
                      ? `已绑定 ${wx.bound_user || '（未知）'} · 走 ${wx.model || 'wb:auto'}（云端，不用本地模型）`
                      : (wx.hint || '还没扫码登录：在桥接目录运行 python login_qr.py 扫码')}
                  </span>
                </div>
              </div>

              <div className="param-field">
                <div className="pf-top">
                  <span className="pf-label">随 Claw Agent 启动</span>
                  <span className="pf-val">{wx.auto_start ? '开' : '关'}</span>
                </div>
                <div className="choice-row">
                  <button
                    className={'choice-chip' + (wx.auto_start ? ' on' : '')}
                    disabled={wxBusy === 'auto'}
                    onClick={() => toggleWxAuto(true)}
                  >
                    开
                  </button>
                  <button
                    className={'choice-chip' + (!wx.auto_start ? ' on' : '')}
                    disabled={wxBusy === 'auto'}
                    onClick={() => toggleWxAuto(false)}
                  >
                    关
                  </button>
                </div>
                <div className="pf-bottom"><span className="pf-desc">开启后，Claw Agent 启动约 8 秒自动把微信桥接拉起来（默认关）</span></div>
              </div>
            </>
          )}
          {wxMsg && <div className="param-loadmsg">{wxMsg}</div>}

          <div className="param-sep" />
          <div className="param-note">本地目录（资源管理器中打开）</div>
          <div style={{ display: 'flex', gap: 8, marginTop: 6 }}>
            <button className="pf-reset" onClick={() => doOpenPath('workdir')}>工作目录</button>
            <button className="pf-reset" onClick={() => doOpenPath('logs')}>日志</button>
            <button className="pf-reset" onClick={() => doOpenPath('memory')}>记忆数据</button>
          </div>
          {openMsg && <div className="param-loadmsg">{openMsg}</div>}
        </div>
      </div>
    </div>
  )
}

// ---------- 技能库面板（模型可创建/管理） ----------
export function SkillsPanel({ onClose }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [detail, setDetail] = useState(null)
  const [plugins, setPlugins] = useState([])

  const load = () => {
    getSkills()
      .then((d) => {
        setData(d)
        setErr('')
      })
      .catch((e) => setErr(String(e)))
    getPlugins()
      .then((d) => {
        if (d.ok) setPlugins(d.plugins || [])
      })
      .catch(() => {})
  }
  useEffect(() => {
    load()
  }, [])

  const openDetail = (name) => {
    getSkillDetail(name)
      .then((d) => {
        if (d.ok) setDetail(d)
      })
      .catch(() => {})
  }

  const del = async (name) => {
    if (!window.confirm(`删除技能「${name}」？目录会被整体删除。`)) return
    await deleteSkill(name).catch(() => {})
    setDetail(null)
    load()
  }

  const skills = (data && data.skills) || []
  return (
    <div className="overlay" onClick={onClose}>
      <div className="panel" onClick={(e) => e.stopPropagation()}>
        <div className="p-head">
          <span style={{ display: 'flex', color: 'var(--text-dim)' }}><IconSliders size={16} /></span>
          <span className="p-title">技能库</span>
          <span className="spacer" />
          <button className="icon-btn ghost" title="刷新" onClick={load}><IconRefresh /></button>
          <button className="icon-btn ghost" title="关闭" onClick={onClose}><IconX /></button>
        </div>
        <div className="p-body">
          <div className="param-note">
            技能是把常用流程固化成的可复用脚本（run.py）。在对话里对模型说「把这件事做成技能」，模型会用 skill_create 创建；说「运行 xxx 技能」即执行。
          </div>
          {err && <div style={{ color: 'var(--err)', fontSize: 12.5 }}>{err}</div>}
          {detail ? (
            <div>
              <div className="mem-layer-title">
                <span>技能：{detail.name}</span>
                <button className="layer-clear" onClick={() => setDetail(null)}>返回列表</button>
              </div>
              {detail.meta?.description && <div className="param-note">{detail.meta.description}</div>}
              {detail.skill_md && (
                <>
                  <div className="mem-layer-title"><span>SKILL.md</span></div>
                  <pre className="skill-pre">{detail.skill_md}</pre>
                </>
              )}
              <div className="mem-layer-title"><span>run.py</span></div>
              <pre className="skill-pre">{detail.script || '（无脚本）'}</pre>
              <button className="pf-reset" style={{ marginTop: 10 }} onClick={() => del(detail.name)}>删除该技能</button>
            </div>
          ) : (
            <>
              {skills.length === 0 && <div className="mem-empty">（还没有技能；对模型说「把这件事做成技能」试试）</div>}
              {skills.map((s) => (
                <div className="mem-entry" key={s.name} style={{ cursor: 'pointer' }} onClick={() => openDetail(s.name)}>
                  <div className="me-title">
                    <span>{s.name}</span>
                    <span className="tag-pill">{s.author === 'agent' ? '模型创建' : s.author === 'system' ? '内置示例' : s.author || ''}</span>
                    <span className="spacer" style={{ flex: 1 }} />
                    <button
                      className="icon-btn ghost"
                      style={{ width: 26, height: 26 }}
                      title="删除"
                      onClick={(e) => {
                        e.stopPropagation()
                        del(s.name)
                      }}
                    >
                      <IconTrash size={13} />
                    </button>
                  </div>
                  <div className="me-body">{s.description || '（无说明）'}</div>
                  <div className="me-meta">
                    <span>{s.created_at}</span>
                    <span>{s.has_script ? '含脚本' : '无脚本'}</span>
                  </div>
                </div>
              ))}
              <div className="mem-layer-title" style={{ marginTop: 16 }}>
                <span>插件（一切皆插件 · 给框架加新工具）</span>
                <button className="layer-clear" onClick={() => reloadPlugins().then(load)}>全部重载</button>
              </div>
              {plugins.length === 0 && <div className="mem-empty">（还没有插件；对模型说「写个插件加个工具」试试）</div>}
              {plugins.map((p) => (
                <div className="mem-entry" key={p.name}>
                  <div className="me-title">
                    <span>{p.name}</span>
                    <span className="tag-pill">v{p.version || '1.0'}</span>
                    {p.error && p.error !== '已停用' ? (
                      <span className="tag-pill" style={{ borderColor: 'rgba(196,69,63,.5)', color: 'var(--err)' }}>异常</span>
                    ) : !p.enabled ? (
                      <span className="tag-pill">停用</span>
                    ) : null}
                    <span className="spacer" style={{ flex: 1 }} />
                    <button className="layer-clear" onClick={() => reloadPlugins(p.name).then(load)}>重载</button>
                    <button className="layer-clear" onClick={() => togglePlugin(p.name, !p.enabled).then(load)}>
                      {p.enabled ? '停用' : '启用'}
                    </button>
                    <button
                      className="icon-btn ghost"
                      style={{ width: 26, height: 26 }}
                      title="删除"
                      onClick={() => {
                        if (window.confirm(`删除插件 ${p.name}？`)) deletePlugin(p.name).then(load)
                      }}
                    >
                      <IconTrash size={13} />
                    </button>
                  </div>
                  <div className="me-body">{p.description || '（无说明）'}</div>
                  <div className="me-meta">
                    <span>工具：{p.tools.length ? p.tools.join('、') : '—'}</span>
                    {p.error && p.error !== '已停用' ? <span style={{ color: 'var(--err)' }}>{p.error.slice(0, 60)}</span> : null}
                  </div>
                </div>
              ))}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ---------- 记忆面板 ----------
const LAYER_NAMES = {
  1: 'L1 常驻核心（始终注入 · 不含经验）',
  2: 'L2 重要记忆（经验 / 技术方案 · 按需检索）',
  3: 'L3 情景记忆（项目 / 事件 · 按需检索）',
  4: 'L4 会话记忆（自动记录 · 到期归入 L3）',
}
const LAYER_SHORT = { 1: '核心', 2: '重要', 3: '情景', 4: '会话' }

// 记忆面板的「筛选」纯走前端：后端的 store.search 会累计 hit_count / last_hit_at，
// 每敲一个字就调一次会把命中统计污染成噪声，所以这里只在已取回的数据上过滤。
const _low = (s) => String(s || '').toLowerCase()
const _tagList = (tags) => String(tags || '').split(',').map((t) => t.trim()).filter(Boolean)

const memMatch = (e, query) => {
  const q = _low(query).trim()
  if (!q) return true
  if (q.startsWith('#')) {                       // 「#标签」= 只按标签找
    const t = q.replace(/^#+/, '')
    return _tagList(e.tags).some((x) => _low(x).includes(t))
  }
  return [e.title, e.summary, e.content, e.tags].some((v) => _low(v).includes(q))
}

const memHasTag = (e, tag) =>
  !tag || _tagList(e.tags).some((x) => _low(x) === _low(tag))

// 工作目录作用域键（与后端 workspace_key 对齐：统一分隔符与大小写）
const normWs = (p) =>
  String(p || '')
    .replace(/\//g, '\\')
    .replace(/\\+$/, '')
    .toLowerCase()

const memScope = (e, scope, wsKey) => {
  const ws = String(e.workspace || 'global')
  if (scope === 'global') return ws === 'global'
  if (scope === 'current') return ws !== 'global' && normWs(ws) === wsKey
  return true
}

export function MemoryPanel({ onClose, workdir = '' }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [tagId, setTagId] = useState(null)      // 正在编辑标签的条目 id
  const [tagDraft, setTagDraft] = useState('')
  const [tagNotice, setTagNotice] = useState('')
  const [saving, setSaving] = useState(false)
  const [q, setQ] = useState('')                // 搜索词（标题/结论/正文/标签）
  const [tagFilter, setTagFilter] = useState('') // 选中的标签（点标签圆片即选中）
  const [scopeFilter, setScopeFilter] = useState('all') // all / current / global
  const [cands, setCands] = useState([])        // 遗忘候选
  const [showCand, setShowCand] = useState(false)
  const [forgetBusy, setForgetBusy] = useState(false)
  const [promoting, setPromoting] = useState(0)
  const [toast, setToast] = useState('')
  const wsKey = normWs(workdir)

  const load = () => {
    getMemory(500, workdir)
      .then((d) => {
        setData(d)
        setErr('')
      })
      .catch((e) => setErr(String(e)))
  }
  useEffect(() => {
    load()
  }, [workdir])

  const openTagEditor = (e) => {
    setTagId(e.id)
    setTagDraft(e.tags || '')
    setTagNotice('')
  }

  const saveTags = async (id) => {
    setSaving(true)
    try {
      const r = await setMemoryTags(id, { tags: tagDraft })
      if (r.ok === false) {
        setErr(r.error || '标签保存失败')
      } else {
        setTagDraft(r.tags || '')
        setTagNotice(r.notice || '')
        setData((d) => (d ? { ...d, tags: r.tags_overview || d.tags } : d))
        load()
        if (!r.notice) setTagId(null)
      }
    } catch (e) {
      setErr(String(e))
    } finally {
      setSaving(false)
    }
  }

  const del = async (id) => {
    try {
      await fetch(`/api/memory/${id}`, { method: 'DELETE' })
    } catch (e) {
      /* 忽略 */
    }
    load()
  }

  const clearLayer = async (layer) => {
    if (!window.confirm(`确定清空「${LAYER_SHORT[layer]}记忆」（L${layer}）的全部条目？此操作不可撤销。`)) return
    try {
      await fetch(`/api/memory?layer=${layer}`, { method: 'DELETE' })
    } catch (e) {
      /* 忽略 */
    }
    load()
  }

  // ---- 遗忘机制：先看候选（只提议），确认后才降级 ----
  const scanForget = async () => {
    setForgetBusy(true)
    setShowCand(true)
    try {
      const r = await getForgetCandidates()
      setCands(r.candidates || [])
    } catch (e) {
      setErr(String(e))
    } finally {
      setForgetBusy(false)
    }
  }

  const doDemote = async (ids) => {
    const list = Array.isArray(ids) ? ids : [ids]
    const names = list
      .map((i) => {
        const c = cands.find((x) => x.id === i)
        return c ? `#${i}《${c.title.slice(0, 20)}》→ ${c.suggest === '归档' ? '归档' : 'L3'}` : `#${i}`
      })
      .join('\n')
    if (!window.confirm(`确认执行降级？\n\n${names}\n\n（降级后仍可检索，不会删除）`)) return
    setForgetBusy(true)
    try {
      const r = await demoteMemory(list)
      const ok = r.done ?? 0
      setToast(`已降级 ${ok} / ${r.total ?? list.length} 条`)
      setCands((cs) => cs.filter((c) => !list.includes(c.id)))
      load()
    } catch (e) {
      setErr(String(e))
    } finally {
      setForgetBusy(false)
    }
  }

  const doPin = async (id, on) => {
    try {
      const r = await pinMemory(id, on)
      if (r.ok === false) setErr(r.error || '操作失败')
      setCands((cs) => cs.filter((c) => c.id !== id))
      load()
    } catch (e) {
      setErr(String(e))
    }
  }

  const doPromote = async (id) => {
    setPromoting(id)
    setErr('')
    try {
      const r = await promoteMemory(id)
      if (r.ok === false) {
        setErr(r.msg || r.error || '提升失败')
      } else {
        setToast(r.msg || `#${id} 已提取到全局记忆`)
        setTimeout(() => setToast(''), 3000)
        load()
      }
    } catch (e) {
      setErr(String(e))
    } finally {
      setPromoting(0)
    }
  }

  const total = (data?.entries || []).length
  const filtered = useMemo(
    () =>
      (data?.entries || []).filter(
        (e) => memHasTag(e, tagFilter) && memMatch(e, q) && memScope(e, scopeFilter, wsKey)
      ),
    [data, q, tagFilter, scopeFilter, wsKey]
  )
  const filtering = !!q.trim() || !!tagFilter || scopeFilter !== 'all'
  const currentCount = (data?.entries || []).filter(
    (e) => memScope(e, 'current', wsKey) && memHasTag(e, tagFilter) && memMatch(e, q)
  ).length
  const globalCount = (data?.entries || []).filter(
    (e) => memScope(e, 'global', wsKey) && memHasTag(e, tagFilter) && memMatch(e, q)
  ).length

  const byLayer = useMemo(() => {
    const m = { 1: [], 2: [], 3: [], 4: [] }
    for (const e of filtered) {
      if (m[e.layer]) m[e.layer].push(e)
    }
    return m
  }, [filtered])

  return (
    <div className="overlay" onClick={onClose}>
      <div className="panel" onClick={(e) => e.stopPropagation()}>
        <div className="p-head">
          <span style={{ display: 'flex', color: 'var(--text-dim)' }}><IconMemory size={17} /></span>
          <span className="p-title">记忆库</span>
          <span className="spacer" />
          <button className="icon-btn ghost" title="刷新" onClick={load}><IconRefresh /></button>
          <button className="icon-btn ghost" title="关闭" onClick={onClose}><IconX /></button>
        </div>
        <div className="p-body">
          {err && <div style={{ color: 'var(--err)', fontSize: 12.5 }}>{err}</div>}
          {data && (
            <>
              <div className="mem-stats">
                {['L1', 'L2', 'L3', 'L4'].map((k) => (
                  <div className="mem-stat" key={k}>
                    <div className="num">{data.stats?.[k] ?? 0}</div>
                    <div className="lab">{k}</div>
                  </div>
                ))}
                <div className="mem-stat">
                  <div className="num">{data.stats?.archived ?? 0}</div>
                  <div className="lab">归档</div>
                </div>
              </div>

              {/* 作用域：全局（所有目录可见）/ 本目录（同目录会话共享） */}
              <div className="mem-scope-bar">
                {[
                  { k: 'all', l: '全部' },
                  { k: 'current', l: '本目录' },
                  { k: 'global', l: '全局' },
                ].map((x) => (
                  <button
                    key={x.k}
                    className={`scope-pill ${scopeFilter === x.k ? 'on' : ''}`}
                    onClick={() => setScopeFilter(x.k)}
                    title={
                      x.k === 'current'
                        ? `当前目录：${workdir || '（默认目录）'}`
                        : x.k === 'global'
                          ? '全局记忆：所有工作目录可见'
                          : '全部作用域'
                    }
                  >
                    {x.l}
                  </button>
                ))}
                <span className="mem-found">
                  本目录 {currentCount} 条 · 全局 {globalCount} 条
                </span>
              </div>

              {/* 遗忘机制：只提议 → 确认后执行 */}
              <div className="mem-search">
                <button className="tag-btn" disabled={forgetBusy} onClick={scanForget}>
                  {forgetBusy ? '扫描中…' : '遗忘扫描（只提议）'}
                </button>
                <span className="mem-found">
                  L2/L3 层内垫底 + 长期未被真正使用；pin／重要度 5 豁免
                </span>
              </div>
              {showCand ? (
                <div className="forget-box">
                  {cands.length === 0 ? (
                    <div className="mem-empty">
                      暂无候选。库龄不足时通常为空（时间门槛默认 90 天）——这是正常的。
                    </div>
                  ) : (
                    <>
                      <div className="forget-head">
                        遗忘候选 {cands.length} 条（<b>未改动任何数据</b>）
                        <span className="spacer" style={{ flex: 1 }} />
                        <button className="tag-btn" onClick={() => doDemote(cands.map((c) => c.id))}>
                          全部执行
                        </button>
                        <button className="tag-btn ghost" onClick={() => setShowCand(false)}>
                          收起
                        </button>
                      </div>
                      {cands.map((c) => (
                        <div className="forget-row" key={c.id}>
                          <div className="forget-main">
                            <span className="me-id">#{c.id}</span> L{c.layer}《{c.title.slice(0, 30)}》
                          </div>
                          <div className="forget-why">
                            被用 {c.hit_count} 次 · 注入 {c.inject_count} 次 · 分 {c.score}（中位 {c.layer_median}）
                            {c.kind === 'ed' ? ' · ⚠ 常注入却从没被用过' : ''}
                          </div>
                          <div className="forget-act">
                            <span className="forget-sug">→ 建议 {c.suggest}</span>
                            <button className="tag-btn" onClick={() => doDemote(c.id)}>执行</button>
                            <button className="tag-btn ghost" onClick={() => doPin(c.id, true)}>锁定</button>
                          </div>
                        </div>
                      ))}
                    </>
                  )}
                </div>
              ) : null}
              {toast ? <div className="mem-empty" style={{ color: 'var(--text-dim)' }}>{toast}</div> : null}

              {/* 搜索 + 标签筛选 */}
              <div className="mem-search">
                <input
                  className="mem-search-input"
                  value={q}
                  placeholder="搜标题 / 结论 / 正文 / 标签（前缀 # 只搜标签）"
                  onChange={(ev) => setQ(ev.target.value)}
                  onKeyDown={(ev) => {
                    if (ev.key === 'Escape') {
                      setQ('')
                      setTagFilter('')
                    }
                  }}
                />
                {filtering ? (
                  <>
                    <span className="mem-found">{filtered.length} / {total} 条</span>
                    <button
                      className="tag-btn ghost"
                      onClick={() => {
                        setQ('')
                        setTagFilter('')
                        setScopeFilter('all')
                      }}
                    >
                      清除
                    </button>
                  </>
                ) : null}
              </div>
              {data.tags?.length ? (
                <div className="mem-tagbar">
                  <span className="tag-sug-lab">标签：</span>
                  {data.tags.slice(0, 18).map((t) => (
                    <button
                      key={t.tag}
                      className={`tag-pill tag-pick${_low(tagFilter) === _low(t.tag) ? ' on' : ''}`}
                      title={`${t.count} 条 · 点击筛选，再点一次取消`}
                      onClick={() =>
                        setTagFilter((cur) => (_low(cur) === _low(t.tag) ? '' : t.tag))
                      }
                    >
                      {t.tag}
                      <span className="tag-pick-n">{t.count}</span>
                    </button>
                  ))}
                  {data.tags.length > 18 ? (
                    <span className="tag-sug-lab">…共 {data.tags.length} 个</span>
                  ) : null}
                </div>
              ) : null}

              {filtering && filtered.length === 0 ? (
                <div className="mem-empty">没有匹配的记忆（换个词，或点「清除」）</div>
              ) : null}
              {[1, 2, 3, 4].map((layer) => (
                <div key={layer}>
                  <div className="mem-layer-title">
                    <span>
                      {LAYER_NAMES[layer]}
                      {filtering && byLayer[layer].length ? `（命中 ${byLayer[layer].length}）` : ''}
                    </span>
                    {byLayer[layer].length > 0 && (
                      <button className="layer-clear" onClick={() => clearLayer(layer)}>
                        清空该层
                      </button>
                    )}
                  </div>
                  {byLayer[layer].length === 0 ? (
                    filtering ? null : <div className="mem-empty">（空）</div>
                  ) : (
                    byLayer[layer].map((e) => (
                      <div className="mem-entry" key={e.id}>
                        <div className="me-title">
                          <span className="me-id">#{e.id}</span>
                          <span>{e.title}</span>
                          <span
                            className={`scope-badge ${
                              (e.workspace || 'global') === 'global' ? 'global' : 'ws'
                            }`}
                            title={e.workspace || 'global'}
                          >
                            {(e.workspace || 'global') === 'global'
                              ? '全局'
                              : normWs(e.workspace) === wsKey
                                ? '本目录'
                                : '其他目录'}
                          </span>
                          {e.pin ? <span className="pin-badge" title="已锁定：永不被降级">锁定</span> : null}
                          <span className="spacer" style={{ flex: 1 }} />
                          {(e.workspace || 'global') !== 'global' && (
                            <button
                              className="tag-btn promote-btn"
                              disabled={promoting === e.id}
                              title="提取一份到全局记忆（所有工作目录可见）"
                              onClick={() => doPromote(e.id)}
                            >
                              {promoting === e.id ? '提升中…' : '提升全局'}
                            </button>
                          )}
                          <button
                            className="icon-btn ghost"
                            style={{ width: 26, height: 26 }}
                            title={e.pin ? '解锁' : '锁定（永不被降级）'}
                            onClick={() => doPin(e.id, !e.pin)}
                          >
                            <IconShield size={13} />
                          </button>
                          <button className="icon-btn ghost" style={{ width: 26, height: 26 }} title="删除" onClick={() => del(e.id)}>
                            <IconTrash size={13} />
                          </button>
                        </div>
                        <div className="me-body">{e.content}</div>
                        <div className="me-meta">
                          <span>重要度 {e.importance}</span>
                          <span>被用 {e.hit_count} 次</span>
                          <span>注入 {e.inject_count ?? 0} 次</span>
                          <span>{e.updated_at}</span>
                          <span className="me-tags" title="点标签=筛选 · 点「改标签」=编辑">
                            {e.tags ? _tagList(e.tags).map((t, i) => (
                              <span
                                className={`tag-pill tag-pick${_low(tagFilter) === _low(t) ? ' on' : ''}`}
                                key={i}
                                title={`按标签「${t}」筛选`}
                                onClick={() => setTagFilter((cur) => (_low(cur) === _low(t) ? '' : t))}
                              >
                                {t}
                              </span>
                            )) : (
                              <span
                                className="tag-pill tag-empty"
                                title="添加标签"
                                onClick={() => (tagId === e.id ? setTagId(null) : openTagEditor(e))}
                              >
                                + 加标签
                              </span>
                            )}
                          </span>
                          <button
                            className="tag-btn ghost tag-edit-btn"
                            title="编辑标签"
                            onClick={() => (tagId === e.id ? setTagId(null) : openTagEditor(e))}
                          >
                            改标签
                          </button>
                        </div>
                        {tagId === e.id && (
                          <div className="tag-editor">
                            <div className="tag-editor-row">
                              <input
                                className="tag-input"
                                value={tagDraft}
                                autoFocus
                                placeholder="标签，逗号分隔，数量不限"
                                onChange={(ev) => setTagDraft(ev.target.value)}
                                onKeyDown={(ev) => {
                                  if (ev.key === 'Enter') saveTags(e.id)
                                  if (ev.key === 'Escape') setTagId(null)
                                }}
                              />
                              <button className="tag-btn" disabled={saving} onClick={() => saveTags(e.id)}>
                                {saving ? '保存中…' : '保存'}
                              </button>
                              <button className="tag-btn ghost" onClick={() => setTagId(null)}>取消</button>
                            </div>
                            <div className="tag-parse">
                              解析为 {tagDraft.split(/[，,、；;|]/).map((t) => t.trim()).filter(Boolean).length} 个标签
                              {tagNotice ? <span className="tag-warn"> · {tagNotice}</span> : null}
                            </div>
                            {data?.tags?.length ? (
                              <div className="tag-sug">
                                <span className="tag-sug-lab">常用：</span>
                                {data.tags.slice(0, 14).map((t) => (
                                  <button
                                    key={t.tag}
                                    className="tag-pill tag-sug-item"
                                    title={`已被 ${t.count} 条使用`}
                                    onClick={() =>
                                      setTagDraft((cur) => {
                                        const parts = cur.split(',').map((x) => x.trim()).filter(Boolean)
                                        if (parts.some((x) => x === t.tag)) return cur
                                        return [...parts, t.tag].join(',')
                                      })
                                    }
                                  >
                                    {t.tag}
                                  </button>
                                ))}
                              </div>
                            ) : null}
                          </div>
                        )}
                      </div>
                    ))
                  )}
                </div>
              ))}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
