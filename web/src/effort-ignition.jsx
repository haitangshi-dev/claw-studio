// 「推理强度点火」特效（Codex 风格）
//
// 借鉴 openai/codex 的 EffortIgnition（codex-rs/tui/src/bottom_pane/effort_ignition.rs
// 与 effort_ignition_styles.rs）：推理强度被拉到最高档时，在输入框区域放一次庆祝动画。
//
//   wave   波峰自左向右掠过      Max 1000ms / Ultra 1300ms（Ultra 末尾还有一颗闪光）
//   aurora 极光带左右漂移动     Max 1300ms / Ultra 1600ms
//   pulse  自中心扩散的脉冲      Max  900ms / Ultra 1250ms
//
// 与 Codex 一致的三点：
//   1) 三种风格随机挑一种，且不与上一次重复
//   2) 动画只覆盖输入框背景，不遮挡正在输入的文字
//   3) 最高档期间保留一层常驻强调色（见 App 里的 .effort-max / .effort-ultra）
//
// 配色沿用 Codex：Max = 金橙，Ultra = 紫粉蓝。

import { useEffect, useRef, useState } from 'react'

const STYLES = ['wave', 'aurora', 'pulse']

/** 每种风格在两个档位下的总时长（毫秒），照搬 Codex 的数值。 */
const DURATION = {
  wave: { max: 1000, ultra: 1300 },
  aurora: { max: 1300, ultra: 1600 },
  pulse: { max: 900, ultra: 1250 },
}

/**
 * @param {{id:number, tier:'max'|'ultra'}|null} run
 *        id 变化一次 = 触发一次动画（用时间戳即可）；tier 决定配色与时长。
 */
export function EffortIgnition({ run }) {
  const [anim, setAnim] = useState(null)
  const prevStyle = useRef(null)
  const runId = run ? run.id : null

  useEffect(() => {
    if (!runId) return undefined
    // 随机挑一种，且尽量不与上一次重复（Codex 用的是「不同则返回」的循环）
    let style = STYLES[Math.floor(Math.random() * STYLES.length)]
    for (let i = 0; i < 8 && style === prevStyle.current; i += 1) {
      style = STYLES[Math.floor(Math.random() * STYLES.length)]
    }
    prevStyle.current = style

    const tier = run && run.tier === 'ultra' ? 'ultra' : 'max'
    const ms = (DURATION[style] || DURATION.wave)[tier]
    setAnim({ style, tier, ms, key: runId })

    const timer = setTimeout(() => setAnim(null), ms + 120)
    return () => clearTimeout(timer)
  }, [runId])

  if (!anim) return null

  const cls = `ignition ig-${anim.style} ig-${anim.tier}`
  const style = { '--ig-dur': `${anim.ms}ms` }

  if (anim.style === 'aurora') {
    return (
      <div className={cls} style={style} key={anim.key} aria-hidden="true">
        <span className="ig-orb" />
        <span className="ig-orb" />
        <span className="ig-orb" />
      </div>
    )
  }

  if (anim.style === 'pulse') {
    return (
      <div className={cls} style={style} key={anim.key} aria-hidden="true">
        <span className="ig-ring" />
        <span className="ig-ring" />
      </div>
    )
  }

  // wave（Ultra 档在末尾补一颗闪光，对应 Codex 的 spark_frame）
  return (
    <div className={cls} style={style} key={anim.key} aria-hidden="true">
      <span className="ig-wave" />
      {anim.tier === 'ultra' && <span className="ig-spark">✦</span>}
    </div>
  )
}

export default EffortIgnition
