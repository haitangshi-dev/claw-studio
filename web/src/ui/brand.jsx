/* 品牌标记：螃蟹。沿用源码 CrabFace 的语义（两只螯、两只眼），
   改为「壳 + 眼柄 + 钳形螯」的螃蟹剪影——眼柄是关键，否则 24px 下会被读成猴脸。 */

export function CrabMark({ size = 24, withLegs }) {
  const legs = withLegs ?? size >= 28
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      {legs && (
        <g stroke="var(--primary)" strokeWidth="1.2" strokeLinecap="round" opacity="0.5">
          <path d="M6 15.6 3.6 16.6" />
          <path d="M6.2 17.2 4.2 18.4" />
          <path d="M18 15.6 20.4 16.6" />
          <path d="M17.8 17.2 19.8 18.4" />
        </g>
      )}

      <g stroke="var(--primary)" strokeWidth="2.1" strokeLinecap="round" fill="none">
        <path d="M6.9 13.7 3.5 12.5" />
        <path d="M6.9 13.7 4.3 15.6" />
        <path d="M17.1 13.7 20.5 12.5" />
        <path d="M17.1 13.7 19.7 15.6" />
      </g>

      <g stroke="var(--primary)" strokeWidth="1.5" strokeLinecap="round">
        <path d="M9.5 10.9V8.3" />
        <path d="M14.5 10.9V8.3" />
      </g>

      <circle cx="9.5" cy="7.7" r="1.6" fill="var(--primary)" />
      <circle cx="14.5" cy="7.7" r="1.6" fill="var(--primary)" />
      <circle cx="9.5" cy="7.9" r="0.62" fill="var(--primary-fg)" />
      <circle cx="14.5" cy="7.9" r="0.62" fill="var(--primary-fg)" />

      <ellipse cx="12" cy="14.1" rx="6.9" ry="4.0" fill="var(--primary)" />
    </svg>
  )
}

export function Wordmark({ size = 13, sub }) {
  return (
    <div className="min-w-0">
      <p className="m-0 font-medium leading-tight" style={{ fontSize: size }}>
        Claw Studio
      </p>
      {sub && <p className="m-0 text-[11px] leading-tight text-faint">{sub}</p>}
    </div>
  )
}
