import React from 'react'
import clsx from 'clsx'
import { twMerge } from 'tailwind-merge'

export const cn = (...a) => twMerge(clsx(a))

export function Button({ variant = 'outline', size = 'md', className, children, ...rest }) {
  const base =
    'inline-flex items-center justify-center gap-1.5 rounded-md font-normal transition-colors duration-150 select-none disabled:opacity-45 disabled:pointer-events-none whitespace-nowrap'
  const sizes = { sm: 'h-7 px-2.5 text-[12px]', md: 'h-9 px-3.5 text-[13px]', lg: 'h-10 px-4 text-[13px]' }
  const variants = {
    primary: 'bg-primary text-primary-fg hover:opacity-90',
    outline: 'border border-line text-ink hover:border-line-strong hover:bg-surface2',
    ghost: 'text-muted hover:bg-surface2 hover:text-ink',
    danger: 'border border-danger/40 text-danger hover:bg-danger/10',
  }
  return (
    <button className={cn(base, sizes[size], variants[variant], className)} {...rest}>
      {children}
    </button>
  )
}

export function IconButton({ label, className, children, ...rest }) {
  return (
    <button
      aria-label={label}
      title={label}
      className={cn(
        'inline-flex h-7 w-7 items-center justify-center rounded-md text-muted transition-colors hover:bg-surface2 hover:text-ink',
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  )
}

export function Card({ className, children }) {
  return (
    <div className={cn('rounded-lg border border-line bg-surface', className)}>{children}</div>
  )
}

export function Section({ title, desc, action, children, className }) {
  return (
    <section className={cn('mb-7', className)}>
      <div className="mb-3 flex items-baseline gap-3">
        <div className="min-w-0">
          <h2 className="m-0 text-[14px] font-medium">{title}</h2>
          {desc && <p className="m-0 mt-1 text-[12.5px] leading-relaxed text-muted">{desc}</p>}
        </div>
        {action && <div className="ml-auto flex-none">{action}</div>}
      </div>
      {children}
    </section>
  )
}

export function Row({ label, hint, children, className }) {
  return (
    <div
      className={cn(
        'flex items-start gap-4 border-b border-line/70 px-4 py-3 last:border-b-0',
        className,
      )}
    >
      <div className="min-w-0 flex-1 pt-0.5">
        <p className="m-0 text-[13px] leading-snug">{label}</p>
        {hint && <p className="m-0 mt-1 text-[12px] leading-relaxed text-faint">{hint}</p>}
      </div>
      <div className="flex flex-none items-center gap-2">{children}</div>
    </div>
  )
}

export function Input({ className, ...rest }) {
  return (
    <input
      className={cn(
        'h-9 w-full rounded-md border border-line bg-surface px-2.5 text-[13px] text-ink outline-none transition-colors placeholder:text-faint focus:border-primary',
        className,
      )}
      {...rest}
    />
  )
}

export function Select({ className, children, ...rest }) {
  return (
    <select
      className={cn(
        'h-9 rounded-md border border-line bg-surface px-2 text-[13px] text-ink outline-none focus:border-primary',
        className,
      )}
      {...rest}
    >
      {children}
    </select>
  )
}

export function Field({ label, hint, children, className }) {
  return (
    <label className={cn('block', className)}>
      <span className="mb-1.5 block text-[12.5px] text-muted">{label}</span>
      {children}
      {hint && <span className="mt-1.5 block text-[11.5px] text-faint">{hint}</span>}
    </label>
  )
}

export function Toggle({ checked, onChange, disabled }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={!!checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        'relative h-5 w-9 flex-none rounded-full border transition-colors duration-150 disabled:opacity-40',
        checked ? 'border-primary bg-primary' : 'border-line-strong bg-surface2',
      )}
    >
      <span
        className={cn(
          'absolute top-0.5 h-3.5 w-3.5 rounded-full transition-all duration-150',
          checked ? 'left-[18px] bg-primary-fg' : 'left-0.5 bg-faint',
        )}
      />
    </button>
  )
}

export function Segmented({ value, onChange, options, size = 'md' }) {
  return (
    <div
      className={cn(
        'inline-flex flex-none items-center gap-0.5 rounded-md border border-line bg-surface2 p-0.5',
        size === 'sm' && 'text-[12px]',
      )}
    >
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          onClick={() => onChange(o.value)}
          className={cn(
            'rounded-[6px] px-2.5 transition-colors duration-150',
            size === 'sm' ? 'h-6 text-[12px]' : 'h-7 text-[12.5px]',
            value === o.value
              ? 'bg-surface text-ink shadow-[0_1px_2px_rgba(0,0,0,0.06)]'
              : 'text-muted hover:text-ink',
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function Badge({ tone = 'neutral', children, className }) {
  const tones = {
    neutral: 'bg-surface2 text-muted',
    primary: 'bg-primary-soft text-primary',
    success: 'bg-success/12 text-success',
    warning: 'bg-warning/12 text-warning',
    danger: 'bg-danger/12 text-danger',
  }
  return (
    <span
      className={cn(
        'inline-flex h-5 items-center rounded-full px-2 text-[11.5px] leading-none',
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  )
}

export function StatusDot({ tone = 'neutral', pulse }) {
  const colors = { neutral: 'bg-faint', success: 'bg-success', warning: 'bg-warning', danger: 'bg-danger' }
  return (
    <span className="relative inline-flex h-2 w-2 flex-none">
      {pulse && (
        <span className={cn('absolute inset-0 animate-ping rounded-full opacity-60', colors[tone])} />
      )}
      <span className={cn('relative h-2 w-2 rounded-full', colors[tone])} />
    </span>
  )
}

export function Spinner({ size = 14, className }) {
  return (
    <span
      className={cn('inline-block animate-spin rounded-full border-2 border-line border-t-primary', className)}
      style={{ width: size, height: size }}
    />
  )
}

export function EmptyState({ icon, title, desc, action }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-line px-6 py-10 text-center">
      {icon && <div className="mb-3 text-faint">{icon}</div>}
      <p className="m-0 text-[13px]">{title}</p>
      {desc && <p className="m-0 mt-1.5 max-w-[380px] text-[12.5px] leading-relaxed text-faint">{desc}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}

export function Slider({ value, onChange, min = 0, max = 100, step = 1, suffix }) {
  return (
    <div className="flex flex-none items-center gap-3">
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="h-1 w-32 cursor-pointer appearance-none rounded-full bg-surface2 accent-[var(--primary)]"
      />
      <span className="w-12 text-right font-mono text-[12px] text-muted">
        {value}
        {suffix || ''}
      </span>
    </div>
  )
}
