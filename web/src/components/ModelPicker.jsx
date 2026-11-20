import React, { useEffect, useRef, useState } from 'react'
import { Check, ChevronDown, Loader2, Pencil, RefreshCw, Sparkles, TriangleAlert } from 'lucide-react'
import './ModelPicker.css'

/**
 * 模型选择器：自动抓取 + 手填 + 好看。
 *
 * 视觉层次：
 *   1. 主控件：供应商徽章 + 模型名，大而清晰
 *   2. 状态条：识别到几个模型 / 抓取失败，一眼可见
 *   3. 候选面板：每项「供应商徽章 · 模型名」，选中项高亮打勾
 *   4. 手填提示：自定义模型名原样发送，不阻止
 */
export default function ModelPicker({
  value = '',
  models = [],
  onChange,
  baseUrl = '',
  apiKey = '',
  providerName = 'API',
  probe,
  auto = true,
  placeholder = '输入或选择模型名…',
}) {
  const [text, setText] = useState(value || '')
  const [list, setList] = useState(models || [])
  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState(null)
  const [open, setOpen] = useState(false)
  const [idx, setIdx] = useState(-1)
  const rootRef = useRef(null)
  const seq = useRef(0)

  useEffect(() => setText(value || ''), [value])
  useEffect(() => { if (models && models.length) setList(models) }, [models])

  useEffect(() => {
    const onDoc = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  const doProbe = async () => {
    if (!baseUrl.trim() || !apiKey.trim() || !probe) return
    const my = ++seq.current
    setLoading(true)
    setStatus(null)
    try {
      const r = await probe({ baseUrl, apiKey })
      if (my !== seq.current) return
      if (r && r.ok) {
        const ms = r.models || []
        setList(ms)
        setStatus({ ok: true, text: ms.length ? `识别到 ${ms.length} 个模型` : '端点未返回模型，可手动输入' })
        if (!text.trim() && ms.length) {
          setText(ms[0])
          onChange && onChange(ms[0], ms)
        }
      } else {
        setStatus({ ok: false, text: (r && r.error) || '无法获取模型列表，可手动输入' })
      }
    } catch (e) {
      if (my !== seq.current) return
      setStatus({ ok: false, text: `连接失败（${e.name || 'Error'}），可手动输入` })
    } finally {
      if (my === seq.current) setLoading(false)
    }
  }

  // 填完就自动抓，防抖 700ms；key 是掩码时（编辑已有供应商）不重复抓
  useEffect(() => {
    if (!auto || !probe) return undefined
    if (!baseUrl.trim() || !apiKey.trim() || apiKey.includes('•')) return undefined
    const t = setTimeout(() => doProbe(), 700)
    return () => clearTimeout(t)
  }, [baseUrl, apiKey])

  const commit = (m) => {
    setText(m)
    setOpen(false)
    setIdx(-1)
    onChange && onChange(m, list)
  }

  const onKey = (e) => {
    if (!open || !list.length) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setIdx((i) => Math.min(i + 1, list.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setIdx((i) => Math.max(i - 1, 0))
    } else if (e.key === 'Enter' && idx >= 0) {
      e.preventDefault()
      commit(list[idx])
    } else if (e.key === 'Escape') {
      setOpen(false)
    }
  }

  const shown = list.filter((m) => !text || m.toLowerCase().includes(text.toLowerCase()))
  const isCustom = text && !list.includes(text)

  return (
    <div className="mpk" ref={rootRef}>
      <div className={`mpk-control ${open ? 'open' : ''}`}>
        <span className="mpk-vendor" title={`供应商：${providerName}`}>
          <Sparkles size={12} />
          <span>{providerName}</span>
        </span>
        <input
          value={text}
          onChange={(e) => {
            setText(e.target.value)
            setOpen(true)
            setIdx(-1)
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKey}
          placeholder={placeholder}
          spellCheck={false}
        />
        {loading && <Loader2 size={14} className="mpk-spin" />}
        {!loading && (
          <button
            type="button"
            className="mpk-act"
            title="重新拉取模型列表"
            onClick={(e) => { e.stopPropagation(); doProbe() }}
          >
            <RefreshCw size={13} />
          </button>
        )}
        <button
          type="button"
          className={`mpk-act mpk-toggle ${open ? 'on' : ''}`}
          onClick={() => setOpen((v) => !v)}
          aria-label="展开模型列表"
        >
          <ChevronDown size={14} />
        </button>
      </div>

      <div className="mpk-meta">
        {status ? (
          <span className={`mpk-chip ${status.ok ? 'ok' : 'err'}`}>
            {status.ok ? <Check size={11} /> : <TriangleAlert size={11} />}
            {status.text}
          </span>
        ) : (
          <span className="mpk-chip mute">
            {baseUrl && apiKey ? '正在识别模型…' : '填好端点与密钥后自动识别'}
          </span>
        )}
        <span className="mpk-hint"><Pencil size={10} /> 可直接手填</span>
      </div>

      {open && (shown.length > 0 || isCustom) && (
        <div className="mpk-panel">
          <div className="mpk-panel-head">
            <span>可选模型</span>
            <span className="mpk-count">{list.length}</span>
            <button type="button" onClick={() => setOpen(false)}>收起</button>
          </div>
          {shown.map((m, i) => (
            <button
              type="button"
              key={m}
              className={`mpk-row ${i === idx ? 'on' : ''} ${m === text ? 'current' : ''}`}
              onMouseEnter={() => setIdx(i)}
              onMouseDown={(e) => { e.preventDefault(); commit(m) }}
            >
              <span className="mpk-row-vendor">{providerName}</span>
              <span className="mpk-row-model">{m}</span>
              {m === text && <span className="mpk-row-check"><Check size={12} /></span>}
            </button>
          ))}
          {isCustom && (
            <div className="mpk-custom">
              <Pencil size={11} />
              <span>使用自定义模型名 <b>{text}</b>，将原样发给 API</span>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
