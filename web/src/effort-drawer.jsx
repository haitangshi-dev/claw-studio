// 思考强度：触发器 + 内联抽屉（照 Codex 桌面版重做）
//
// 参考实现：bakabaicai/DSH-Codex-reasoning-effort-slider（把 Codex 桌面版的推理深度
// 滑块 1:1 移植到 DSH）。要点照抄：
//   * 平时只留一个很素的触发器（28px 高、透明底、hover 才显底色、带 chevron）
//   * 点一下才展开，展开的是「输入框自己延伸出来的容器」，不是浮层弹窗
//   * 容器里是一条 26px 高的直线胶囊轨道 + 刻度点 + 30px 圆形 knob
//   * 轨道上铺一层粒子，连成一条直线的星点流；档位越高越亮、流得越快；拉满爆散
//   * 拖动用「四舍五入磁性吸附」，指针离刻度 12px 内高亮
//   * 拉到底（最高档）触发一次性点火特效，chevron 旋转 180°
//
// 相对上一版的修正（用户反馈）：
//   1) 轨道太细（4px）→ 加粗成 26px 直线胶囊
//   2) 特效太少（3 个光晕）→ 改成沿轨道铺展的密集粒子流（190 颗），拉满再爆散
//   3) 拖动改为 Math.round 四舍五入磁性吸附，松手才提交
//
// 配色：本项目是浅色暖调，粒子在填充区内用暖白、区外用暖棕，保证浅底可见。

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { IconBrain, IconChevron, IconRefresh } from './icons.jsx'

const KNOB = 30 // knob 直径（px），与 styles.css 保持一致
const MAGNET = 12 // 磁性吸附半径（px）
const PARTICLES = 190 // 轨道上粒子数量（"特效少"指的是这层太稀疏）

/** 确定性伪随机：同一 seed 恒得同一个 0~1 值，粒子分布稳定不闪跳。 */
function rand(seed) {
  const x = Math.sin(seed * 127.1 + 311.7) * 43758.5453
  return x - Math.floor(x)
}

/** 触发器：素净的一行，点击开合抽屉。 */
export function EffortTrigger({ value, options, disabled, open, onToggle, title }) {
  const opts = options && options.length ? options : []
  const cur = opts.find((o) => String(o.k) === String(value))
  const last = opts.length - 1
  const atTop = last > 0 && opts.findIndex((o) => String(o.k) === String(value)) >= last

  return (
    <button
      type="button"
      data-effort-ui="1"
      className={`effort-trigger${open ? ' open' : ''}${atTop ? ' at-top' : ''}`}
      disabled={disabled || opts.length < 2}
      aria-expanded={open}
      title={title || (cur && cur.desc ? `思考强度：${cur.lab} —— ${cur.desc}` : '思考强度')}
      onClick={onToggle}
    >
      <IconBrain size={13} />
      <span className="et-lab">{cur ? cur.lab : '默认'}</span>
      <span className="et-chev">
        <IconChevron size={12} />
      </span>
    </button>
  )
}

/**
 * 轨道上的粒子层：一条直线的密集星点流。
 *
 * @param {boolean} open      抽屉是否展开（收起时停掉动画，不空转）
 * @param {number}  ratio     当前档位占比 0~1，决定亮度与流速
 * @param {any}     topKey    最高档的 key（用于决定爆散粒子配色）
 * @param {number}  igniteId  变化一次 = 在右端爆散一次
 */
function EffortSparks({ open, ratio, topKey, igniteId }) {
  const cvs = useRef(null)
  const st = useRef({ target: 0, ratio: 0, travel: 0, burst: 0, last: 0, raf: 0 })
  const seeds = useMemo(
    () => Array.from({ length: PARTICLES * 2 }, (_, i) => rand(i * 1.7 + 0.31)),
    [],
  )

  useEffect(() => {
    st.current.target = ratio
  }, [ratio])

  useEffect(() => {
    if (igniteId) st.current.burst = performance.now()
  }, [igniteId])

  useEffect(() => {
    if (!open) return undefined
    const canvas = cvs.current
    const host = canvas && canvas.parentElement
    if (!canvas || !host) return undefined
    const ctx = canvas.getContext('2d')
    const dpr = 2

    const fit = () => {
      const box = host.getBoundingClientRect()
      if (!box.width || !box.height) return
      canvas.width = Math.round(box.width * dpr)
      canvas.height = Math.round(box.height * dpr)
    }
    fit()
    const ro = typeof ResizeObserver === 'function' ? new ResizeObserver(fit) : null
    if (ro) ro.observe(host)

    const s = st.current
    s.last = 0
    const draw = (time) => {
      const dt = s.last ? Math.min(time - s.last, 50) / 1000 : 0
      s.last = time
      // 平滑跟随目标档位，避免推进档位时亮度突跳
      s.ratio += (s.target - s.ratio) * Math.min(1, dt * 6)
      // 流动行程：档位越高流得越快（低档也缓慢漂移，保持"活着"）
      s.travel += dt * (0.03 + s.ratio * 0.75)

      const W = canvas.width
      const H = canvas.height
      ctx.clearRect(0, 0, W, H)
      const t = time / 1000
      const fillPx = s.ratio * (W - KNOB * dpr) + KNOB * dpr * 0.5 // 填充边界

      // —— 一条直线的粒子流 ——
      for (let i = 0; i < PARTICLES; i += 1) {
        const sx = seeds[i * 2]
        const sy = seeds[i * 2 + 1]
        const ph = i * 2.39
        const flow = s.travel * (0.4 + (i % 6) * 0.14)
        const x = (sx + flow) % 1
        // y 只做极小起伏，整体仍是"一条直线"
        const y = 0.5 + (sy - 0.5) * 0.62 + Math.sin(t * 1.3 + ph) * 0.045
        const tw = 0.5 + 0.5 * Math.sin(t * (1.1 + (i % 5) * 0.35) + ph)
        const base = i % 7 === 0 ? 2.0 : i % 4 === 0 ? 1.4 : 1.0
        const px = x * W
        const inFill = px <= fillPx
        const a = (0.30 + 0.55 * tw) * (0.6 + 0.4 * s.ratio) * (inFill ? 1 : 0.42)
        ctx.beginPath()
        ctx.arc(px, y * H, base * dpr * 0.85, 0, Math.PI * 2)
        ctx.fillStyle = inFill
          ? `rgba(255, 253, 248, ${a})`
          : `rgba(146, 112, 78, ${a * 0.75})`
        ctx.fill()
      }

      // —— 拉满瞬间：右端爆散一圈粒子（对应 Codex 的 burst） ——
      const age = (time - s.burst) / 1000
      if (s.burst && age >= 0 && age < 0.62) {
        const cx = W - KNOB * dpr * 0.5
        const cy = H / 2
        const purple = topKey === 'ultra' || topKey === 'xhigh'
        for (let i = 0; i < 26; i += 1) {
          const ang = i * 2.399 + 0.18
          const dist = (12 + (24 + (i % 5) * 7) * age * 2.8) * dpr
          const a = 0.78 * (1 - age / 0.62)
          ctx.beginPath()
          ctx.arc(
            cx + Math.cos(ang) * dist,
            cy + Math.sin(ang) * dist,
            (i % 6 === 0 ? 2.2 : 1.3) * dpr * (1 - age),
            0,
            Math.PI * 2,
          )
          ctx.fillStyle = purple ? `rgba(140, 78, 210, ${a})` : `rgba(206, 96, 30, ${a})`
          ctx.fill()
        }
      }

      s.raf = requestAnimationFrame(draw)
    }
    s.raf = requestAnimationFrame(draw)
    return () => {
      cancelAnimationFrame(s.raf)
      if (ro) ro.disconnect()
    }
  }, [open, seeds, topKey])

  return <canvas ref={cvs} className="ed-particles" />
}

/**
 * 抽屉：从输入框内部自然撑开的一块容器。
 *
 * @param {boolean}  open       是否展开
 * @param {any}      value      当前档位
 * @param {Array}    options    档位列表 [{k, lab, desc}]
 * @param {Function} onPick     选中某档
 * @param {Function} onIgnite   从非最高档走到最高档时触发点火特效
 * @param {any}      defaultKey 默认档（用于"恢复默认"）
 * @param {Function} onReset    恢复默认
 */
export function EffortDrawer({ open, value, options, onPick, onIgnite, defaultKey, onReset,
                             heading = '思考等级', ariaLabel = '思考等级' }) {
  const opts = options && options.length ? options : []
  const last = opts.length - 1
  const idx = Math.max(0, opts.findIndex((o) => String(o.k) === String(value)))
  const cur = opts[idx] || {}

  const [drag, setDrag] = useState(null) // 拖动中的预览档位（松手才提交）
  const [hover, setHover] = useState(-1) // 磁性命中的刻度
  const [igniteId, setIgniteId] = useState(0)
  const railRef = useRef(null)

  const show = drag == null ? idx : drag // 拖动时优先显示预览
  const ratio = last > 0 ? show / last : 1
  const atTop = last > 0 && show >= last
  const dirty = defaultKey != null && String(value) !== String(defaultKey)
  const topKey = opts[last] ? opts[last].k : ''

  /** 提交某一档（唯一写入点，保证点火只在真正跨到最高档时触发一次）。 */
  const commit = useCallback(
    (i) => {
      if (i < 0 || i > last || !opts[i]) return
      const was = idx
      onPick(opts[i].k)
      if (i >= last && was < last) {
        setIgniteId(Date.now())
        if (onIgnite) onIgnite()
      }
    },
    [idx, last, opts, onPick, onIgnite],
  )

  /**
   * 四舍五入磁性吸附：把指针横坐标换算成最近档位。
   * 行程扣掉 knob 直径，两端各留半个 knob，首尾刻度与 knob 圆心对齐。
   */
  const nearest = useCallback(
    (clientX) => {
      const el = railRef.current
      if (!el || last < 1) return 0
      const box = el.getBoundingClientRect()
      const travel = box.width - KNOB
      if (travel <= 0) return 0
      const rel = (clientX - box.left - KNOB / 2) / travel
      return Math.round(Math.max(0, Math.min(1, rel)) * last)
    },
    [last],
  )

  /** 某个刻度点的屏幕横坐标（判断磁性距离用）。 */
  const tickX = useCallback(
    (i) => {
      const el = railRef.current
      if (!el) return null
      const box = el.getBoundingClientRect()
      const travel = box.width - KNOB
      return box.left + KNOB / 2 + (last > 0 ? (i / last) * travel : 0)
    },
    [last],
  )

  const onDown = (e) => {
    if (e.button !== 0 || last < 1) return
    try {
      e.currentTarget.setPointerCapture(e.pointerId)
    } catch {
      /* 某些环境不支持指针捕获，忽略 */
    }
    const i = nearest(e.clientX)
    setDrag(i)
    setHover(i)
  }

  const onMove = (e) => {
    if (drag != null) {
      setDrag(nearest(e.clientX))
      return
    }
    // 未拖动时只做磁性高亮：指针离刻度 12px 以内才算命中
    const i = nearest(e.clientX)
    const x = tickX(i)
    setHover(x != null && Math.abs(e.clientX - x) < MAGNET ? i : -1)
  }

  const onUp = (e) => {
    if (drag == null) return
    try {
      e.currentTarget.releasePointerCapture(e.pointerId)
    } catch {
      /* 同上 */
    }
    const i = nearest(e.clientX)
    setDrag(null)
    setHover(-1)
    commit(i)
  }

  const onKey = (e) => {
    let next = idx
    if (e.key === 'ArrowRight' || e.key === 'ArrowUp') next = idx + 1
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') next = idx - 1
    else if (e.key === 'Home') next = 0
    else if (e.key === 'End') next = last
    else return
    e.preventDefault()
    commit(Math.max(0, Math.min(last, next)))
  }

  const pickTick = (i) => {
    setHover(-1)
    commit(i)
  }

  return (
    <div className={`effort-drawer${open ? ' open' : ''}`} data-effort-ui="1" aria-hidden={!open}>
      <div className="ed-clip">
        <div className="ed-card">
          <div className="ed-head">
            <span className="ed-title">{heading}</span>
            {cur.desc && <span className="ed-desc">{cur.desc}</span>}
            {dirty && (
              <button
                type="button"
                className="ed-reset"
                title="恢复该模型的默认档位"
                onClick={() => onReset && onReset(defaultKey)}
              >
                <IconRefresh size={11} />
              </button>
            )}
          </div>

          <div
            className={`ed-track${drag != null ? ' dragging' : ''}${atTop ? ' at-top' : ''}`}
            role="slider"
            tabIndex={0}
            aria-label={ariaLabel}
            aria-valuemin={0}
            aria-valuemax={last > 0 ? last : 0}
            aria-valuenow={idx}
            aria-valuetext={cur.lab}
            onPointerDown={onDown}
            onPointerMove={onMove}
            onPointerUp={onUp}
            onPointerCancel={onUp}
            onPointerLeave={() => drag == null && setHover(-1)}
            onKeyDown={onKey}
          >
            <div className="ed-rail" ref={railRef}>
              <div className="ed-fill" style={{ width: `${ratio * 100}%` }} />
            </div>

            <div className="ed-sparks">
              <EffortSparks open={open} ratio={ratio} topKey={atTop ? topKey : ''} igniteId={igniteId} />
              {atTop && igniteId ? <span className="ed-sheen" key={igniteId} /> : null}
            </div>

            <div className="ed-ticks">
              {opts.map((o, i) => (
                <button
                  key={String(o.k)}
                  type="button"
                  className={`ed-tick${i <= show ? ' passed' : ''}${i === show ? ' on' : ''}${
                    hover === i ? ' hover' : ''
                  }`}
                  style={{ '--ed-i': last > 0 ? i / last : 0 }}
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={() => pickTick(i)}
                  tabIndex={-1}
                  aria-label={o.lab}
                />
              ))}
            </div>

            <div className={`ed-knob${atTop ? ' at-top' : ''}`} style={{ '--ed-pct': ratio }} />
          </div>

          <div className="ed-labels">
            {opts.map((o, i) => (
              <span
                key={String(o.k)}
                className={`ed-label${i === show ? ' on' : ''}${hover === i ? ' hover' : ''}`}
                style={{ '--ed-i': last > 0 ? i / last : 0 }}
                onClick={() => pickTick(i)}
              >
                {o.lab}
              </span>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

export default EffortDrawer
