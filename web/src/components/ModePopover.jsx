import React, { useEffect, useRef } from 'react'
import { Moon, UserRound } from 'lucide-react'

/**
 * 运行模式选择器。
 *
 * 之前它复用了「思考强度」那个纵向档位抽屉——但那是个强度滑轨的形态，
 * 用在这里语义不对：值守 / 无人监管不是「档位高低」，而是两种完全不同的工作方式，
 * 而且要不要选时长本身就是另一个维度的决定。所以单独做一个：
 * 两张卡片讲清区别，时长只在无人监管时出现。
 */

const DURATIONS = [
  { k: 30, lab: '30 分钟' },
  { k: 60, lab: '1 小时' },
  { k: 120, lab: '2 小时' },
  { k: 240, lab: '4 小时' },
  { k: 0, lab: '不限时' },
]

export default function ModePopover({
  open,
  mode,
  minutes,
  onPickMode,
  onPickMinutes,
  onClose,
  anchor = 'up',
}) {
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return undefined
    const onDoc = (e) => {
      if (ref.current && !ref.current.contains(e.target)) onClose && onClose()
    }
    const onKey = (e) => {
      if (e.key === 'Escape') onClose && onClose()
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [open, onClose])

  if (!open) return null

  const unattended = mode === 'unattended'

  return (
    <div
      ref={ref}
      className={`mode-pop ${anchor === 'up' ? 'up' : ''}`}
      role="dialog"
      aria-label="运行模式"
    >
      <div className="mp-head">
        <span>运行模式</span>
        <kbd>Esc</kbd>
      </div>

      <button
        type="button"
        className={`mp-card ${mode === 'onduty' ? 'on' : ''}`}
        onClick={() => onPickMode && onPickMode('onduty')}
      >
        <span className="mp-icon"><UserRound size={15} /></span>
        <span className="mp-body">
          <span className="mp-title">值守中</span>
          <span className="mp-desc">你在看着。遇到真正需要拍板的岔路会弹卡片问你。</span>
        </span>
      </button>

      <button
        type="button"
        className={`mp-card ${unattended ? 'on' : ''}`}
        onClick={() => onPickMode && onPickMode('unattended')}
      >
        <span className="mp-icon"><Moon size={15} /></span>
        <span className="mp-body">
          <span className="mp-title">
            无人监管
            <span className="mp-tag">自主模式</span>
          </span>
          <span className="mp-desc">
            你走开。它自己判断岔路、自己验证结果、每阶段自检；
            做完主目标继续找可优化项，到点或你手动停止时按固定结构汇报。
          </span>
        </span>
      </button>

      <div className={`mp-durations ${unattended ? '' : 'dim'}`}>
        <div className="mp-dur-head">
          无人监管时长
          {!unattended && <span className="mp-hint">（选择无人监管后生效）</span>}
        </div>
        <div className="mp-dur-row">
          {DURATIONS.map((d) => (
            <button
              key={d.k}
              type="button"
              className={`mp-dur ${minutes === d.k ? 'on' : ''}`}
              onClick={() => onPickMinutes && onPickMinutes(d.k)}
              disabled={!unattended}
            >
              {d.lab}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
