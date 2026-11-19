import React, { useEffect, useState } from 'react'
import { postPlan } from '../api'

const ICON = {
  done: (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="8" cy="8" r="7" fill="var(--ok)" />
      <path d="M4.8 8.2l2.1 2.1 4.3-4.3" fill="none" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ),
  running: (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="8" cy="8" r="6.3" fill="none" stroke="var(--cyan)" strokeWidth="1.7" />
      <circle cx="8" cy="8" r="2.4" fill="var(--cyan)" />
    </svg>
  ),
  failed: (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="8" cy="8" r="7" fill="var(--err)" />
      <path d="M5.6 5.6l4.8 4.8M10.4 5.6l-4.8 4.8" fill="none" stroke="#fff" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  ),
  skipped: (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="8" cy="8" r="6.3" fill="none" stroke="var(--border-strong, #cdbda2)" strokeWidth="1.2" />
      <path d="M5.4 8h5.2" stroke="var(--text-faint)" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  ),
  pending: (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="8" cy="8" r="6.3" fill="none" stroke="var(--border)" strokeWidth="1.2" />
    </svg>
  ),
}

export default function PlanPanel({ session, plan, onRefresh }) {
  const [open, setOpen] = useState(() => localStorage.getItem('claw_plan_open') !== '0')
  const [hover, setHover] = useState('')

  useEffect(() => {
    localStorage.setItem('claw_plan_open', open ? '1' : '0')
  }, [open])

  const steps = plan?.steps || []
  if (!steps.length) return null

  const done = steps.filter((s) => s.status === 'done' || s.status === 'skipped').length
  const running = steps.find((s) => s.status === 'running')
  const failed = steps.filter((s) => s.status === 'failed').length

  const patch = async (op, step_id, extra = {}) => {
    try {
      await postPlan({ session, op, step_id, ...extra })
      onRefresh && onRefresh()
    } catch (e) {
      /* 忽略：下一轮轮询会收敛 */
    }
  }

  return (
    <div
      style={{
        width: '100%',
        maxWidth: 900,
        margin: '0 auto 8px',
        background: 'var(--bg-panel)',
        border: '1px solid var(--border)',
        borderRadius: 12,
        padding: open ? '10px 12px 8px' : '7px 12px',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true" style={{ flex: 'none' }}>
          <rect x="1.6" y="2.2" width="12.8" height="11.6" rx="2" fill="none" stroke="var(--text-muted)" strokeWidth="1.2" />
          <path d="M4.4 6h5M4.4 8.6h7M4.4 11.2h4" stroke="var(--text-muted)" strokeWidth="1.2" strokeLinecap="round" />
        </svg>
        <span style={{ fontSize: 12.5, fontWeight: 600 }}>任务规划</span>
        <span style={{ fontSize: 11.5, color: 'var(--text-faint)' }}>
          {done}/{steps.length}
          {running && open ? ` · 正在：${running.title}` : ''}
          {failed ? ` · ${failed} 步失败` : ''}
        </span>
        <button
          onClick={() => setOpen((v) => !v)}
          style={{ marginLeft: 'auto', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-faint)', padding: 2 }}
          aria-label={open ? '收起计划' : '展开计划'}
        >
          <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
            <path
              d={open ? 'M4 9.5L8 5.5L12 9.5' : 'M4 6.5L8 10.5L12 6.5'}
              fill="none"
              stroke="currentColor"
              strokeWidth="1.3"
              strokeLinecap="round"
            />
          </svg>
        </button>
      </div>

      {open && (
        <div style={{ marginTop: 6 }}>
          {steps.map((s, i) => (
            <div
              key={s.id}
              onMouseEnter={() => setHover(s.id)}
              onMouseLeave={() => setHover('')}
              style={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: 8,
                padding: '5px 6px',
                borderRadius: 7,
                background: s.status === 'running' ? 'var(--bg-soft)' : 'transparent',
              }}
            >
              <button
                onClick={() => patch('update', s.id, { status: s.status === 'done' ? 'pending' : 'done' })}
                title={s.status === 'done' ? '标记为未完成' : '标记为完成'}
                style={{ background: 'none', border: 'none', padding: 0, marginTop: 1, cursor: 'pointer', lineHeight: 0, flex: 'none' }}
              >
                {ICON[s.status] || ICON.pending}
              </button>

              <div style={{ minWidth: 0, flex: 1 }}>
                <p
                  style={{
                    margin: 0,
                    fontSize: 12.5,
                    lineHeight: 1.5,
                    color: s.status === 'done' || s.status === 'skipped' ? 'var(--text-dim)' : 'var(--text)',
                    textDecoration: s.status === 'skipped' ? 'line-through' : 'none',
                  }}
                >
                  <span style={{ color: 'var(--text-faint)', marginRight: 5 }}>{i + 1}</span>
                  {s.title}
                </p>
                {s.note && (
                  <p style={{ margin: '1px 0 0 18px', fontSize: 11.5, color: 'var(--text-faint)' }}>{s.note}</p>
                )}
              </div>

              {hover === s.id && s.status !== 'done' && s.status !== 'skipped' && (
                <button
                  onClick={() => patch('skip', s.id)}
                  style={{
                    background: 'none',
                    border: 'none',
                    cursor: 'pointer',
                    fontSize: 11.5,
                    color: 'var(--text-faint)',
                    padding: '0 2px',
                  }}
                >
                  跳过
                </button>
              )}
            </div>
          ))}

          <div style={{ display: 'flex', alignItems: 'center', marginTop: 4, paddingTop: 6, borderTop: '1px solid var(--border)' }}>
            <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>计划只记步骤，不记耗时</span>
            <button
              onClick={() => patch('clear', '')}
              style={{
                marginLeft: 'auto',
                background: 'none',
                border: 'none',
                cursor: 'pointer',
                fontSize: 11.5,
                color: 'var(--text-faint)',
              }}
            >
              清空
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
