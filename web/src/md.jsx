// Markdown 渲染组件：把模型输出的 Markdown 安全地渲染成 React 节点。
//
// 引擎见 ./md-render.js（markdown-it + highlight.js）。
// 本文件负责：
//   * 挂载渲染结果（html:false 保证安全，唯一注入点是转义后的 HTML）
//   * 流式输出时补全未闭合的代码块围栏（否则打字过程中代码块会乱）
//   * 代码块「复制」按钮（事件委托，一个监听器管全部代码块）
//   * 局部降级：万一渲染抛错，退回等宽纯文本，绝不丢正文
//
// 导出 MiniMarkdown（保持旧调用点兼容）与 Markdown。

import { useEffect, useMemo, useRef } from 'react'
import { renderMarkdown, escapeHtml } from './md-render.js'

/**
 * 流式补全：模型正在往 ``` 代码块里打字时，结尾围栏还没吐出来。
 * 这里按「奇数个围栏 ⇒ 补一个」处理，让中间态也能正确显示成代码块。
 */
function closeOpenFence(text) {
  const s = String(text || '')
  const fences = (s.match(/^\s*```/gm) || []).length
  if (fences % 2 === 1) return s + '\n```'
  return s
}

/** 把渲染后的容器里所有代码块的纯文本取出来，供复制 */
function codeTextOf(btn) {
  const wrap = btn && btn.closest ? btn.closest('.code-wrap') : null
  const code = wrap ? wrap.querySelector('pre code') : null
  if (!code) return ''
  // 高亮后的 HTML 还原成纯文本：用 textContent 最稳（含 &lt; 等实体自动还原）
  return code.textContent || ''
}

export function Markdown({ text, streaming = false, className = '' }) {
  const ref = useRef(null)

  const html = useMemo(() => {
    const src = streaming ? closeOpenFence(text) : text
    try {
      return renderMarkdown(src)
    } catch (e) {
      return `<pre class="md-fail">${escapeHtml(src)}</pre>`
    }
  }, [text, streaming])

  // 代码块复制：事件委托，容器上只挂一个监听（避免每个代码块一个 handler）
  useEffect(() => {
    const el = ref.current
    if (!el) return undefined
    const onClick = async (e) => {
      const btn = e.target && e.target.closest ? e.target.closest('.code-copy') : null
      if (!btn || !el.contains(btn)) return
      const code = codeTextOf(btn)
      const done = (ok) => {
        btn.textContent = ok ? '已复制' : '复制失败'
        btn.classList.toggle('ok', ok)
        setTimeout(() => {
          btn.textContent = '复制'
          btn.classList.remove('ok')
        }, 1400)
      }
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(code)
          done(true)
        } else {
          // 降级：老内核 / 非安全上下文
          const ta = document.createElement('textarea')
          ta.value = code
          ta.style.position = 'fixed'
          ta.style.opacity = '0'
          document.body.appendChild(ta)
          ta.select()
          const ok = document.execCommand && document.execCommand('copy')
          document.body.removeChild(ta)
          done(!!ok)
        }
      } catch (err) {
        done(false)
      }
    }
    el.addEventListener('click', onClick)
    return () => el.removeEventListener('click', onClick)
  }, [])

  if (!html) return null
  return (
    <div
      ref={ref}
      className={`md-body ${className}`.trim()}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  )
}

/** 兼容旧调用点（原先的迷你渲染器叫 MiniMarkdown） */
export function MiniMarkdown({ text, streaming }) {
  return <Markdown text={text} streaming={streaming} />
}

export default Markdown
