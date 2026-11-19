import React, { useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  Bug,
  Download,
  FolderOpen,
  RefreshCw,
  Trash2,
} from 'lucide-react'
import { Badge, Button, Card, Input, Segmented } from '../../ui'

/**
 * 诊断日志：出问题时的第一落点。
 *
 * 后端写 logs/diag.jsonl（结构化，带 traceback），这里是它的视图。
 * 设计原则：默认就能看到「哪里出错了」，不需要用户懂 level/事件名；
 * 每条可展开看完整字段，错误整条红色，一眼能在时间线上定位。
 */

const LEVEL_TONE = {
  error: { color: 'var(--err, #c4453f)', label: '错误' },
  warn: { color: 'var(--warn, #b8860b)', label: '警告' },
  info: { color: 'var(--text-faint, #8b7d72)', label: '信息' },
  debug: { color: 'var(--text-faint, #8b7d72)', label: '调试' },
}

const EVENT_LABEL = {
  'app.start': '应用启动',
  'chat.start': '对话开始',
  'chat.done': '对话完成',
  'chat.error_event': '对话报错',
  'chat.stream': '流中断',
  'tool.failed': '工具失败',
}

function fmtTime(ms) {
  const d = new Date(ms)
  const p = (n) => String(n).padStart(2, '0')
  const today = new Date()
  const same = d.toDateString() === today.toDateString()
  const hm = `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
  return same ? hm : `${d.getMonth() + 1}/${d.getDate()} ${hm}`
}

function summarize(it) {
  const skip = new Set(['ts', 'level', 'event', 'traceback', 'root'])
  const bits = []
  if (it.session) bits.push(`会话 ${it.session}`)
  if (it.model) bits.push(it.model)
  if (it.channel) bits.push(`通道 ${it.channel}`)
  if (it.mode) bits.push(it.mode === 'unattended' ? '无人监管' : '值守中')
  if (it.tool) bits.push(`工具 ${it.tool}`)
  if (it.elapsed_ms) bits.push(`${(it.elapsed_ms / 1000).toFixed(1)}s`)
  if (it.steps) bits.push(`${it.steps} 步`)
  if (it.chars) bits.push(`${it.chars} 字`)
  if (it.detail) bits.push(String(it.detail).slice(0, 120))
  if (it.error) bits.push(String(it.error).slice(0, 160))
  if (!bits.length) {
    const rest = Object.entries(it).filter(([k]) => !skip.has(k))
    return rest.map(([k, v]) => `${k}=${String(v).slice(0, 40)}`).join(' · ')
  }
  return bits.join(' · ')
}

export default function Logs() {
  const [items, setItems] = useState([])
  const [meta, setMeta] = useState({})
  const [level, setLevel] = useState('')
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(null)
  const [busy, setBusy] = useState('')
  const [note, setNote] = useState('')
  const [auto, setAuto] = useState(true)

  const load = async (silent = false) => {
    if (!silent) setBusy('load')
    try {
      const r = await fetch(`/api/logs?limit=300&level=${encodeURIComponent(level)}&q=${encodeURIComponent(q)}`)
      const d = r.ok ? await r.json() : null
      if (d && d.ok) {
        setItems(d.items || [])
        setMeta(d)
      } else {
        setItems([])
        setMeta({})
      }
    } catch (e) {
      setItems([])
    } finally {
      if (!silent) setBusy('')
    }
  }

  useEffect(() => {
    load()
  }, [level])

  useEffect(() => {
    if (!auto) return undefined
    const t = setInterval(() => load(true), 4000)
    return () => clearInterval(t)
  }, [auto, level, q])

  const doClear = async () => {
    setBusy('clear')
    await fetch('/api/logs/clear', { method: 'POST' }).catch(() => null)
    setBusy('')
    setNote('已清空')
    load()
    setTimeout(() => setNote(''), 2000)
  }

  const errCount = meta.errors || 0
  const text = useMemo(() => {
    if (!items.length) return ''
    return items
      .map((x) => JSON.stringify(x, null, 2))
      .join('\n---\n')
  }, [items])

  const copyAll = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setNote('已复制到剪贴板')
    } catch (e) {
      setNote('复制失败，请用「下载」')
    }
    setTimeout(() => setNote(''), 2200)
  }

  return (
    <>
      <div className="mb-4 flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="m-0 text-[15px] font-medium">诊断日志</h2>
          <p className="m-0 mt-1 text-[12px] leading-relaxed text-faint">
            每次对话、工具失败和未捕获异常都会记一条，带时间、会话、通道与耗时。
            出错时先看这里——比翻终端输出快，也比让它「再试一次」有用。
          </p>
        </div>
        <div className="flex flex-none items-center gap-2">
          <Badge tone={errCount ? 'danger' : 'neutral'}>
            {errCount ? `${errCount} 个错误` : '无错误'}
          </Badge>
          <Badge tone="neutral">
            {meta.fileBytes ? `${(meta.fileBytes / 1024).toFixed(0)} KB` : '无文件'}
          </Badge>
        </div>
      </div>

      <Card className="p-3">
        <div className="flex flex-wrap items-center gap-2">
          <Segmented
            size="sm"
            value={level}
            onChange={setLevel}
            options={[
              { value: '', label: '全部' },
              { value: 'warn', label: '警告以上' },
              { value: 'error', label: '仅错误' },
            ]}
          />
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') load()
            }}
            placeholder="搜索（事件名 / 会话 / 报错内容）"
            className="h-8 w-[240px] text-[12.5px]"
          />
          <Button size="sm" variant="ghost" onClick={() => load()} disabled={busy === 'load'}>
            <RefreshCw size={13} /> 刷新
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setAuto((v) => !v)}
            title="每 4 秒自动刷新"
          >
            {auto ? '自动刷新：开' : '自动刷新：关'}
          </Button>
          <span className="ml-auto flex items-center gap-2">
            <Button size="sm" variant="ghost" onClick={copyAll} disabled={!items.length}>
              复制全部
            </Button>
            <a
              href="/api/logs/file"
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-line px-2.5 text-[12.5px] text-ink no-underline hover:bg-surface2"
            >
              <Download size={13} /> 下载
            </a>
            <Button size="sm" variant="ghost" onClick={doClear} disabled={busy === 'clear'}>
              <Trash2 size={13} /> 清空
            </Button>
          </span>
        </div>
        {note && <p className="m-0 mt-2 text-[11.5px] text-faint">{note}</p>}
      </Card>

      <div className="mt-3">
        {!items.length ? (
          <Card className="p-8 text-center">
            <Bug size={22} className="mx-auto mb-2 text-faint" />
            <p className="m-0 text-[12.5px] text-faint">
              还没有日志。说一句话、或者跑一次工具，这里就会出现记录。
            </p>
          </Card>
        ) : (
          <div className="overflow-hidden rounded-lg border border-line bg-card">
            {items.map((it) => {
              const tone = LEVEL_TONE[it.level] || LEVEL_TONE.info
              const on = open === it.ts + it.event
              // 每条都能展开看完整字段：摘要只挑了几个，排查时往往需要原始那条
              const body = it.traceback || it.error || it.detail || JSON.stringify(it, null, 2)
              return (
                <div key={`${it.ts}-${it.event}`} className="border-b border-line last:border-0">
                  <button
                    type="button"
                    onClick={() => setOpen(on ? null : it.ts + it.event)}
                    className="flex w-full items-start gap-2.5 px-3 py-2 text-left hover:bg-surface2/60"
                  >
                    <span
                      className="mt-[5px] h-1.5 w-1.5 flex-none rounded-full"
                      style={{ background: tone.color }}
                    />
                    <span className="w-[62px] flex-none font-mono text-[11px] text-faint">
                      {fmtTime(it.ts)}
                    </span>
                    <span
                      className="w-[86px] flex-none text-[12px]"
                      style={{ color: it.level === 'error' ? tone.color : 'var(--text)' }}
                    >
                      {EVENT_LABEL[it.event] || it.event}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-[12px] text-faint">
                      {summarize(it)}
                    </span>
                    {it.level === 'error' && (
                      <AlertTriangle size={13} className="mt-[3px] flex-none" style={{ color: tone.color }} />
                    )}
                  </button>
                  {on && (
                    <div className="px-3 pb-3">
                      <pre
                        className="m-0 max-h-[260px] overflow-auto rounded-md p-2.5 text-[11px] leading-relaxed"
                        style={{ background: 'var(--surface2)', color: 'var(--text)' }}
                      >
                        {body}
                      </pre>
                      <div className="mt-2 flex gap-2">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => {
                            navigator.clipboard
                              .writeText(JSON.stringify(it, null, 2))
                              .then(() => {
                                setNote('这条已复制')
                                setTimeout(() => setNote(''), 1800)
                              })
                              .catch(() => null)
                          }}
                        >
                          复制这条
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => setOpen(null)}>
                          收起
                        </Button>
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

      <p className="m-0 mt-3 text-[11.5px] leading-relaxed text-faint">
        日志只写本机 <code className="font-mono">logs/diag.jsonl</code>，超过 8MB 自动轮转为
        <code className="font-mono"> .1</code>；工具调用的完整参数另在
        <code className="font-mono"> logs/tool-audit.jsonl</code>。
        <FolderOpen size={11} className="mx-1 inline" />
        需要把问题发出去时，用「下载」把文件带走。
      </p>
    </>
  )
}
