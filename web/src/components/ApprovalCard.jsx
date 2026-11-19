import React, { useEffect, useRef, useState } from 'react'
import { answerApproval } from '../api'
import { IconShield } from '../icons'

const RISK_LABEL = {
  write: { text: '写入', bg: 'rgba(44,127,158,.12)', fg: 'var(--cyan)' },
  exec: { text: '执行', bg: 'rgba(194,130,14,.14)', fg: '#8a5e00' },
  destructive: { text: '不可逆', bg: 'rgba(196,69,63,.12)', fg: 'var(--err)' },
  external: { text: '外发', bg: 'rgba(217,113,58,.14)', fg: 'var(--violet-2)' },
}

export default function ApprovalCard({ item, extra = 0, onDone }) {
  const [reason, setReason] = useState('')
  const [showReason, setShowReason] = useState(false)
  const [left, setLeft] = useState(item?.remaining ?? 120)
  const [busy, setBusy] = useState(false)
  const timer = useRef(null)
  const risk = RISK_LABEL[item?.risk] || RISK_LABEL.write

  useEffect(() => {
    setLeft(item?.remaining ?? 120)
    setReason('')
    setShowReason(false)
  }, [item?.id])

  useEffect(() => {
    clearInterval(timer.current)
    timer.current = setInterval(() => setLeft((v) => (v > 0 ? v - 1 : 0)), 1000)
    return () => clearInterval(timer.current)
  }, [item?.id])

  if (!item) return null

  const submit = async (action) => {
    setBusy(true)
    try {
      await answerApproval(item.id, action, action === 'deny' ? reason : '')
      onDone && onDone(item.id)
    } catch (e) {
      /* 超时/已失效：让轮询自己收敛 */
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      style={{
        position: 'fixed',
        left: '50%',
        bottom: 108,
        transform: 'translateX(-50%)',
        width: 560,
        maxWidth: 'calc(100vw - 48px)',
        zIndex: 60,
        background: 'var(--bg-panel)',
        border: '1px solid var(--border)',
        borderRadius: 14,
        boxShadow: '0 10px 30px rgba(43,33,23,.14)',
        padding: '13px 15px 12px',
      }}
      role="alertdialog"
      aria-label="需要确认的操作"
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <span style={{ color: 'var(--violet)', display: 'flex' }}>
          <IconShield size={14} />
        </span>
        <span style={{ fontSize: 13, fontWeight: 600 }}>需要你确认</span>
        <span
          style={{
            fontSize: 11,
            padding: '2px 7px',
            borderRadius: 999,
            background: risk.bg,
            color: risk.fg,
          }}
        >
          {risk.text}
        </span>
        {extra > 0 && (
          <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>还有 {extra} 个待确认</span>
        )}
        <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--text-faint)' }}>
          {left > 0 ? `${left}s 后自动拒绝` : '已超时'}
        </span>
      </div>

      <p style={{ margin: '0 0 6px', fontSize: 13 }}>{item.title}</p>
      <pre
        style={{
          margin: '0 0 10px',
          padding: '8px 10px',
          background: 'var(--bg-soft)',
          border: '1px solid var(--border)',
          borderRadius: 8,
          fontFamily: 'var(--mono)',
          fontSize: 12,
          lineHeight: 1.55,
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-all',
          maxHeight: 132,
          overflow: 'auto',
        }}
      >
        {item.detail || item.tool}
      </pre>

      {showReason && (
        <input
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="为什么不行？模型会看到这句话（可留空）"
          style={{
            width: '100%',
            height: 32,
            marginBottom: 8,
            padding: '0 10px',
            fontSize: 12.5,
            border: '1px solid var(--border)',
            borderRadius: 8,
            background: 'var(--bg)',
            color: 'var(--text)',
          }}
          autoFocus
        />
      )}

      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <button
          onClick={() => submit('allow')}
          disabled={busy}
          style={btn('var(--violet)', '#fff8f2', 'var(--violet)')}
        >
          允许这次
        </button>
        <button
          onClick={() => submit('allow_session')}
          disabled={busy}
          style={btn('transparent', 'var(--text)', 'var(--border)')}
        >
          本会话允许此类
        </button>
        <button
          onClick={() => (showReason ? submit('deny') : setShowReason(true))}
          disabled={busy}
          style={{ ...btn('transparent', 'var(--err)', 'rgba(196,69,63,.35)'), marginLeft: 'auto' }}
        >
          {showReason ? '确认拒绝' : '拒绝'}
        </button>
      </div>
      <p style={{ margin: '8px 0 0', fontSize: 11, color: 'var(--text-faint)' }}>
        被拒绝的操作会作为结果回给模型，它会换方案而不是原地重试。
      </p>
    </div>
  )
}

function btn(bg, color, border) {
  return {
    height: 30,
    padding: '0 12px',
    fontSize: 12.5,
    borderRadius: 8,
    border: `1px solid ${border}`,
    background: bg,
    color,
    cursor: 'pointer',
  }
}
