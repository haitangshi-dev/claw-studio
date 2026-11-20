import React, { useEffect, useRef, useState } from 'react'
import { Check, ChevronDown, Loader2, RefreshCw, Sparkles, TriangleAlert } from 'lucide-react'

/**
 * 模型选择器：自动抓取 + 可手填 + 好看。
 *
 * 设计要点：
 * - 填完 Base URL / Key 后自动抓取模型（防抖），不需要先点「测试连接」；
 * - 候选项显示「供应商名 · 模型名」，不是干巴巴的模型字符串；
 * - 支持手输任意模型名（有些端点不提供 /models，或要用最新模型）；
 * - 抓取失败不崩溃，内联说明原因，并且照样允许手填。
 */
export default function ModelPicker({
  value = '',
  models = [],
  onChange,
  baseUrl = '',
  apiKey = '',
  providerName = 'API',
  probe,                // async (payload) => { ok, models?, error? }
  auto = true,          // 填完自动抓
  placeholder = '选择或输入模型名…',
  compact = false,
}) {
  const [text, setText] = useState(value || '')
  const [list, setList] = useState(models || [])
  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState(null)   // {ok, text}
  const [open, setOpen] = useState(false)
  const [idx, setIdx] = useState(-1)
  const rootRef = useRef(null)
  const seq = useRef(0)

  useEffect(() => {
    setText(value || '')
  }, [value])

  useEffect(() => {
    if (models && models.length) setList(models)
  }, [models])

  // 外部值变化时同步
  useEffect(() => {
    const onDoc = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  const doProbe = async (silent = false) => {
    if (!baseUrl.trim() || !apiKey.trim() || !probe) return
    const my = ++seq.current
    setLoading(true)
    if (!silent) setStatus(null)
    try {
      const r = await probe({ baseUrl, apiKey })
      if (my !== seq.current) return           // 被更新的请求取代
      if (r && r.ok) {
        const ms = r.models || []
        setList(ms)
        setStatus({ ok: true, text: ms.length ? `已自动识别 ${ms.length} 个模型` : '端点没有返回模型，可手动输入' })
        // 用户还没选过模型时，自动用第一个——这样保存后立即可用
        if (!text.trim() && ms.length) {
          setText(ms[0])
          onChange && onChange(ms[0], ms)
        }
      } else {
        setStatus({ ok: false, text: (r && r.error) || '无法获取模型列表，可手动输入模型名' })
      }
    } catch (e) {
      if (my !== seq.current) return
      setStatus({ ok: false, text: `连接失败（${e.name || 'Error'}），可手动输入模型名` })
    } finally {
      if (my === seq.current) setLoading(false)
    }
  }

  // 填完就自动抓，防抖 700ms；key 是掩码时（编辑已有供应商）不重复抓
  useEffect(() => {
    if (!auto || !probe) return undefined
    if (!baseUrl.trim() || !apiKey.trim() || apiKey.includes('•')) return undefined
    const t = setTimeout(() => doProbe(true), 700)
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
    <div className={`mpk ${compact ? 'compact' : ''}`} ref={rootRef}>
      <div className={`mpk-box ${open ? 'open' : ''}`}>
        <span className="mpk-vendor" title={`供应商：${providerName}`}>
          <Sparkles size={11} />
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
        {loading && <Loader2 size={13} className="mpk-spin" />}
        {!loading && probe && (
          <button
            type="button"
            className="mpk-icon"
            title="重新拉取模型列表"
            onClick={(e) => {
              e.stopPropagation()
              doProbe()
            }}
          >
            <RefreshCw size={12} />
          </button>
        )}
        <button type="button" className="mpk-icon" onClick={() => setOpen((v) => !v)}>
          <ChevronDown size={12} />
        </button>
      </div>

      {status && (
        <div className={`mpk-status ${status.ok ? 'ok' : 'err'}`}>
          {status.ok ? <Check size={11} /> : <TriangleAlert size={11} />}
          <span>{status.text}</span>
        </div>
      )}

      {open && shown.length > 0 && (
        <div className="mpk-list">
          {shown.map((m, i) => (
            <button
              type="button"
              key={m}
              className={`mpk-item ${i === idx ? 'on' : ''} ${m === text ? 'current' : ''}`}
              onMouseEnter={() => setIdx(i)}
              onMouseDown={(e) => {
                e.preventDefault()
                commit(m)
              }}
            >
              <span className="mpk-item-vendor">{providerName}</span>
              <span className="mpk-item-model">{m}</span>
              {m === text && <Check size={12} className="mpk-item-check" />}
            </button>
          ))}
        </div>
      )}

      {open && isCustom && !shown.length && (
        <div className="mpk-custom">
          使用自定义模型名 <b>{text}</b>（该名称不在自动列表里，仍会照原样发给 API）
        </div>
      )}
    </div>
  )
}
