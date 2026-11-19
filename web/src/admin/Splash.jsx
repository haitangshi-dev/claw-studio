import React, { useEffect, useMemo, useState } from 'react'

const STEPS = [
  '加载运行时配置',
  '读取会话与记忆',
  '同步模型供应商',
  '准备工作目录',
  '就绪',
]

const VARIANTS = {
  orbit: { ring: 'conic', speed: 1 },
  pulse: { ring: 'pulse', speed: 1.2 },
  scan: { ring: 'scan', speed: 0.9 },
  minimal: { ring: 'none', speed: 0 },
}

export function ClawMark({ size = 34 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 40 40" aria-hidden="true">
      <path
        d="M20 32C17 26 15.2 18 14.6 11.8"
        fill="none"
        stroke="var(--primary)"
        strokeWidth="3.2"
        strokeLinecap="round"
      />
      <path
        d="M20 32C20 25 20 16 20 8.2"
        fill="none"
        stroke="var(--primary)"
        strokeWidth="3.2"
        strokeLinecap="round"
      />
      <path
        d="M20 32C23 26 24.8 18 25.4 11.8"
        fill="none"
        stroke="var(--primary)"
        strokeWidth="3.2"
        strokeLinecap="round"
      />
      <path
        d="M14.8 32.6C18.2 34.6 21.8 34.6 25.2 32.6"
        fill="none"
        stroke="var(--primary)"
        strokeWidth="2.6"
        strokeLinecap="round"
        opacity="0.4"
      />
    </svg>
  )
}

export default function Splash({ variant = 'orbit', minDuration = 1600, onDone }) {
  const [step, setStep] = useState(0)
  const [leaving, setLeaving] = useState(false)
  const conf = VARIANTS[variant] || VARIANTS.orbit

  useEffect(() => {
    const total = Math.max(minDuration, 900)
    const per = total / STEPS.length
    const timers = STEPS.map((_, i) =>
      setTimeout(() => setStep(i), Math.round(per * i)),
    )
    timers.push(
      setTimeout(() => {
        setLeaving(true)
        setTimeout(() => onDone && onDone(), 320)
      }, total),
    )
    return () => timers.forEach(clearTimeout)
  }, [minDuration, onDone, variant])

  const pct = useMemo(() => Math.round(((step + 1) / STEPS.length) * 100), [step])

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-bg transition-opacity duration-300"
      style={{ opacity: leaving ? 0 : 1 }}
    >
      <style>{`
        @keyframes cs-spin { to { transform: rotate(360deg) } }
        @keyframes cs-pulse { 0%,100% { transform: scale(.94); opacity:.5 } 50% { transform: scale(1.04); opacity:1 } }
        @keyframes cs-scan { 0% { transform: translateY(-14px); opacity:0 } 40% { opacity:1 } 100% { transform: translateY(14px); opacity:0 } }
        @keyframes cs-bar { 0% { transform: translateX(-60%) } 100% { transform: translateX(240%) } }
        @media (prefers-reduced-motion: reduce) { .cs-anim { animation: none !important } }
      `}</style>

      <div className="relative flex h-[92px] w-[92px] items-center justify-center">
        {conf.ring === 'conic' && (
          <svg
            className="absolute inset-0"
            width="92"
            height="92"
            viewBox="0 0 92 92"
            style={{ animation: `cs-spin ${1.6 / (conf.speed || 1)}s linear infinite` }}
            aria-hidden="true"
          >
            <circle
              cx="46"
              cy="46"
              r="43"
              fill="none"
              stroke="var(--primary)"
              strokeWidth="2"
              strokeLinecap="round"
              strokeDasharray="66 204"
              opacity="0.75"
            />
          </svg>
        )}
        {conf.ring === 'pulse' && (
          <span
            className="cs-anim absolute inset-0 rounded-full border border-primary/40"
            style={{ animation: `cs-pulse ${1.8 / (conf.speed || 1)}s ease-in-out infinite` }}
          />
        )}
        {conf.ring === 'scan' && (
          <span
            className="cs-anim absolute h-px w-16 bg-primary/70"
            style={{ animation: `cs-scan ${1.4 / (conf.speed || 1)}s ease-in-out infinite` }}
          />
        )}
        <div className="flex h-[64px] w-[64px] items-center justify-center rounded-xl bg-primary-soft">
          <ClawMark size={38} />
        </div>
      </div>

      <p className="m-0 mt-6 text-[15px] font-medium tracking-wide">Claw Studio</p>
      <p className="m-0 mt-1.5 h-5 text-[12.5px] text-muted transition-opacity duration-200">
        {STEPS[step]}
      </p>

      <div className="mt-6 flex w-[220px] items-center gap-3">
        <div className="relative h-[3px] flex-1 overflow-hidden rounded-full bg-surface2">
          <div
            className="absolute inset-y-0 left-0 rounded-full bg-primary transition-[width] duration-500 ease-smooth"
            style={{ width: `${pct}%` }}
          />
        </div>
        <span className="w-8 text-right font-mono text-[11px] text-faint">{pct}%</span>
      </div>
    </div>
  )
}
