// Markdown 渲染引擎：markdown-it（GFM 风格）+ highlight.js（代码高亮）
//
// 职责：把模型输出的 Markdown 文本渲染成安全的 HTML 字符串。
//   * html: false —— 关掉原始 HTML，模型输出里的 <script> 等一律当纯文本，防注入
//   * linkify   —— 裸 URL / 邮箱自动变链接
//   * breaks    —— 单个换行即换行（聊天场景更需要，模型常靠单换行排版）
//   * 支持：标题 h1-h6、粗斜体、删除线、行内代码、围栏代码块（带语言高亮 + 复制按钮）、
//           有序/无序/嵌套列表、任务列表、表格、引用、分割线、链接、图片、脚注式换行
//
// 导出：
//   renderMarkdown(text)  → HTML 字符串
//   escapeHtml(text)      → 转义（内部用，也方便外部复用）

import MarkdownIt from 'markdown-it'
import taskLists from 'markdown-it-task-lists'
import hljs from 'highlight.js/lib/common'

export function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

const md = new MarkdownIt({
  html: false,        // 安全：不接受原始 HTML
  linkify: true,
  breaks: true,
  typographer: false, // 中文场景不做智能引号替换，避免把 "" 变成弯引号
  highlight: null,    // 高亮在下面的 fence 规则里自己处理（要带语言标签和复制按钮）
})

// 任务列表（- [ ] / - [x]）
md.use(taskLists, { enabled: true, label: true, labelAfter: true })

// ---------- 代码块：语言标签 + 复制按钮 + 高亮 ----------
const AUTO_MAX = 8000 // 无语言标注时的自动识别上限（太长就不猜了，避免卡顿）

md.renderer.rules.fence = (tokens, idx) => {
  const token = tokens[idx]
  const info = String(token.info || '').trim()
  const lang = info.split(/\s+/)[0] || ''
  const raw = token.content

  let body = ''
  let shown = lang
  if (lang && hljs.getLanguage(lang)) {
    try {
      body = hljs.highlight(raw, { language: lang, ignoreIllegals: true }).value
    } catch (e) {
      body = escapeHtml(raw)
    }
  } else if (raw.length <= AUTO_MAX) {
    try {
      const auto = hljs.highlightAuto(raw)
      body = auto.value
      shown = lang || auto.language || ''
    } catch (e) {
      body = escapeHtml(raw)
    }
  } else {
    body = escapeHtml(raw)
  }

  const langLabel = shown ? escapeHtml(shown) : 'text'
  return (
    '<div class="code-wrap">' +
    '<div class="code-head">' +
    `<span class="code-lang">${langLabel}</span>` +
    '<button type="button" class="code-copy" data-copy="1">复制</button>' +
    '</div>' +
    `<pre class="hljs"><code class="language-${escapeHtml(lang || 'plaintext')}">${body}</code></pre>` +
    '</div>'
  )
}

// ---------- 链接：新窗口打开 ----------
const linkOpenDefault = md.renderer.rules.link_open
  || ((tokens, idx, options, env, self) => self.renderToken(tokens, idx, options))

md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  tokens[idx].attrSet('target', '_blank')
  tokens[idx].attrSet('rel', 'noreferrer noopener')
  return linkOpenDefault(tokens, idx, options, env, self)
}

// ---------- 图片：懒加载 ----------
md.renderer.rules.image = (tokens, idx, options, env, self) => {
  const token = tokens[idx]
  const i = token.attrIndex('alt')
  if (i >= 0) token.attrs[i][1] = self.renderInlineAsText(token.children, options, env)
  token.attrSet('loading', 'lazy')
  return self.renderToken(tokens, idx, options)
}

// ---------- 表格：外层包一层横向滚动容器 ----------
const tableOpenDefault = md.renderer.rules.table_open
  || ((tokens, idx, options, env, self) => self.renderToken(tokens, idx, options))
const tableCloseDefault = md.renderer.rules.table_close
  || ((tokens, idx, options, env, self) => self.renderToken(tokens, idx, options))

md.renderer.rules.table_open = (tokens, idx, options, env, self) =>
  '<div class="table-wrap">' + tableOpenDefault(tokens, idx, options, env, self)
md.renderer.rules.table_close = (tokens, idx, options, env, self) =>
  tableCloseDefault(tokens, idx, options, env, self) + '</div>'

/**
 * 渲染 Markdown 为 HTML 字符串。
 * @param {string} text Markdown 源文本
 * @returns {string} HTML
 */
export function renderMarkdown(text) {
  const src = String(text == null ? '' : text)
  if (!src.trim()) return ''
  try {
    return md.render(src)
  } catch (e) {
    // 渲染失败绝不能吃掉正文：退化成等宽纯文本
    return `<pre class="md-fail">${escapeHtml(src)}</pre>`
  }
}

export default renderMarkdown
