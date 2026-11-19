import React, { useEffect, useState } from 'react'
import {
  AlertTriangle,
  Copy,
  Download,
  Layers,
  RefreshCw,
  Trash2,
} from 'lucide-react'
import { Badge, Button, Card, Row, Section, Segmented, Slider, Toggle } from '../../ui'

/**
 * 记忆面板：策略 + 体检 + 库管理。
 *
 * 体检这一块接的是 /api/memory/health（后端真的算出来的重复项、注入了却没用过的条目、
 * 过期、被取代、L1 占用），不是展示给人看的假数字。
 */

const LAYER_NAME = { 1: 'L1 常驻核心', 2: 'L2 重要经验', 3: 'L3 情景记录', 4: 'L4 会话记录' }

export default function MemoryPane({ settings, set }) {
  const m = settings.memory
  const [health, setHealth] = useState(null)
  const [entries, setEntries] = useState([])
  const [busy, setBusy] = useState('')
  const [note, setNote] = useState('')
  const [confirming, setConfirming] = useState(false)
  const [layerFilter, setLayerFilter] = useState('')

  const load = async () => {
    try {
      const h = await fetch('/api/memory/health').then((r) => (r.ok ? r.json() : null))
      if (h && h.ok) setHealth(h)
    } catch (e) {
      /* 离线忽略 */
    }
    try {
      const d = await fetch('/api/memory?limit=60').then((r) => (r.ok ? r.json() : null))
      if (d && d.ok) setEntries(d.entries || [])
    } catch (e) {
      /* 离线忽略 */
    }
  }

  useEffect(() => {
    load()
    const t = setInterval(load, 8000)
    return () => clearInterval(t)
  }, [])

  const flash = (text) => {
    setNote(text)
    setTimeout(() => setNote(''), 3200)
  }

  const doExport = async () => {
    setBusy('export')
    const d = await fetch('/api/memory/export', { method: 'POST' })
      .then((r) => r.json())
      .catch(() => ({}))
    setBusy('')
    flash(d && d.ok ? `已导出到 ${d.dir || d.path}` : '导出失败')
  }

  const doPurge = async () => {
    setBusy('purge')
    const d = await fetch('/api/memory/purge', { method: 'POST' })
      .then((r) => r.json())
      .catch(() => ({}))
    setBusy('')
    setConfirming(false)
    if (d && d.ok) {
      flash(`已清空 ${d.deleted} 条（备份：${d.backup || '无'}）`)
      load()
    } else {
      flash(`清空失败：${(d && d.error) || '未知错误'}`)
    }
  }

  const shown = layerFilter ? entries.filter((e) => String(e.layer) === layerFilter) : entries
  const issues = health
    ? {
        duplicates: health.duplicates || [],
        never: health.never_hit || [],
        expired: health.expired || [],
        superseded: health.superseded || 0,
      }
    : { duplicates: [], never: [], expired: [], superseded: 0 }
  const issueCount = issues.duplicates.length + issues.never.length + issues.expired.length + issues.superseded

  return (
    <>
      <Section
        title="记忆库体检"
        desc="实时统计，不是装饰数字。重复、过期、注入了却从没用过的条目会直接列出来。"
        action={
          <Button size="sm" variant="ghost" onClick={load}>
            <RefreshCw size={13} /> 刷新
          </Button>
        }
      >
        <Card>
          <div className="flex flex-wrap items-center gap-3">
            <Stat label="总条数" value={health ? health.total : '—'} />
            <Stat label="L1 常驻" value={health ? (health.l1?.count ?? 0) : '—'} />
            <Stat
              label="L1 占用"
              value={health ? `${health.l1?.used ?? 0}/${health.l1?.budget ?? 0}` : '—'}
              tone={health && health.l1?.over > 0 ? 'danger' : 'default'}
              hint="常驻记忆占用的字符数 / 预算（超了会自动降级最久未用的条目）"
            />
            <Stat label="问题项" value={issueCount} tone={issueCount ? 'warn' : 'default'} />
          </div>

          {health && (
            <div className="mt-3 flex flex-wrap gap-2">
              {Object.entries(health.layers || {}).map(([k, v]) => (
                <Badge key={k} tone="neutral">
                  {LAYER_NAME[k] || `L${k}`} {v}
                </Badge>
              ))}
              {issues.superseded > 0 && (
                <Badge tone="neutral">被取代的历史结论 {issues.superseded}</Badge>
              )}
            </div>
          )}

          {issueCount > 0 && (
            <div className="mt-3 space-y-2">
              {issues.duplicates.length > 0 && (
                <IssueList
                  tone="warn"
                  icon={<Copy size={12} />}
                  title={`重复条目 ${issues.duplicates.length} 组`}
                  hint="同标题同时存在多条活跃记忆，内容可能已经互相矛盾"
                  items={issues.duplicates.map((d) => `#${d.ids.join(' #')} ${d.title}`)}
                />
              )}
              {issues.never.length > 0 && (
                <IssueList
                  tone="warn"
                  icon={<AlertTriangle size={12} />}
                  title={`注入了却从没被真正用过 ${issues.never.length} 条`}
                  hint="这些条目每次对话都被塞进上下文，但模型一次都没引用——纯占额度"
                  items={issues.never.map((n) => `#${n.id} ${n.title}`)}
                />
              )}
              {issues.expired.length > 0 && (
                <IssueList
                  tone="danger"
                  icon={<AlertTriangle size={12} />}
                  title={`已过期未清理 ${issues.expired.length} 条`}
                  hint="valid_until 已过，但还留在库里"
                  items={issues.expired.map((n) => `#${n.id} ${n.title}（${n.valid_until}）`)}
                />
              )}
            </div>
          )}

          {health && issueCount === 0 && (
            <p className="m-0 mt-3 text-[12px] text-faint">
              没有发现问题：没有重复、没有过期、没有白占额度的条目。
            </p>
          )}
        </Card>
      </Section>

      <Section title="最近写入" desc="最新写进记忆库的条目，含来源会话，方便判断是不是自己写的。">
        <Card className="p-0">
          <div className="flex items-center gap-2 border-b border-line px-3 py-2">
            <Segmented
              size="sm"
              value={layerFilter}
              onChange={setLayerFilter}
              options={[
                { value: '', label: '全部' },
                { value: '1', label: 'L1' },
                { value: '2', label: 'L2' },
                { value: '3', label: 'L3' },
                { value: '4', label: 'L4' },
              ]}
            />
            <span className="ml-auto text-[11.5px] text-faint">
              共 {entries.length} 条{shown.length !== entries.length ? `（筛出 ${shown.length}）` : ''}
            </span>
          </div>
          <div style={{ maxHeight: 280, overflowY: 'auto' }}>
            {!shown.length && (
              <p className="m-0 px-3 py-6 text-center text-[12px] text-faint">
                记忆库是空的。对话中模型会自动记下值得长期保留的事实与偏好。
              </p>
            )}
            {shown.map((e) => (
              <div
                key={e.id}
                className="flex items-start gap-2 border-b border-line px-3 py-2 last:border-0"
              >
                <span className="mt-[2px] font-mono text-[10.5px] text-faint">
                  L{e.layer}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[12.5px]">{e.title}</div>
                  <div className="truncate text-[11.5px] text-faint">
                    {e.summary || e.content}
                  </div>
                </div>
                <span className="flex-none font-mono text-[10.5px] text-faint">
                  {e.source_session ? e.source_session.slice(0, 12) : '手动'}
                </span>
              </div>
            ))}
          </div>
        </Card>
      </Section>

      <Section
        title="写入策略"
        desc="记忆分四层：核心事实常驻、经验按需检索、情景留档、会话记录随任务产生。"
      >
        <Card>
          <Row
            label="自动抽取记忆"
            hint="每轮任务结束后用模型从对话里提取你明确说过的偏好或决定。尚未接入：它会额外消耗一次模型调用，且需要谨慎的判定策略，先不默认开启。"
          >
            <Badge tone="neutral">未接入</Badge>
          </Row>
          <Row
            label="自动巩固"
            hint="同层里重复的碎片合成一条原则，原条目标记合并。尚未接入：合并会改变记忆的语义，误合并比不合并更难察觉，先保持手动。"
          >
            <Badge tone="neutral">未接入</Badge>
          </Row>
          <Row label="冲突处理" hint="同一主题出现相反结论时，保留旧条目并标记被取代，不静默覆盖">
            <Badge tone="primary">保留历史</Badge>
          </Row>
        </Card>
      </Section>

      <Section title="注入预算" desc="常驻记忆占上下文的比例。超预算时自动把最久未使用的条目降级到可检索层。">
        <Card>
          <Row label="预算上限" hint="按模型上下文的百分比动态计算，另有 1200 字下限">
            <Slider
              value={m.budgetRatio}
              min={2}
              max={15}
              onChange={(v) => set('memory.budgetRatio', v)}
              suffix="%"
            />
          </Row>
          <Row label="检索条数" hint="每次注入多少条经验记忆与情景记忆">
            <Segmented
              size="sm"
              value={String(m.topK)}
              onChange={(v) => set('memory.topK', Number(v))}
              options={[
                { value: '2', label: '2 条' },
                { value: '4', label: '4 条' },
                { value: '6', label: '6 条' },
                { value: '10', label: '10 条' },
              ]}
            />
          </Row>
        </Card>
      </Section>

      <Section
        title="记忆库管理"
        desc="导出备份随时可做；清空是不可逆操作，所以会自动先备份一份。"
      >
        <Card>
          <Row label="导出备份" hint="把全部记忆导出为 Markdown，放在 memory/export/">
            <Button size="sm" variant="ghost" onClick={doExport} disabled={busy === 'export'}>
              <Download size={13} /> 导出
            </Button>
          </Row>
          <Row
            label="清空记忆库"
            hint="删除全部条目（包括 L1 常驻记忆）。会自动先备份到 memory/export/memory-purge-*.db"
          >
            {confirming ? (
              <div className="flex items-center gap-2">
                <Button size="sm" variant="danger" onClick={doPurge} disabled={busy === 'purge'}>
                  <Trash2 size={13} /> 确认清空
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
                  取消
                </Button>
              </div>
            ) : (
              <Button size="sm" variant="ghost" onClick={() => setConfirming(true)}>
                <Trash2 size={13} /> 清空
              </Button>
            )}
          </Row>
        </Card>
      </Section>

      {note && (
        <p className="m-0 mt-2 text-[11.5px] text-faint">{note}</p>
      )}
    </>
  )
}

function Stat({ label, value, tone = 'default', hint }) {
  const color =
    tone === 'danger' ? 'var(--err, #c4453f)' : tone === 'warn' ? 'var(--warn, #b8860b)' : 'var(--text)'
  return (
    <div className="min-w-[92px]" title={hint}>
      <div className="text-[10.5px] text-faint">{label}</div>
      <div className="text-[16px] font-medium leading-tight" style={{ color }}>
        {value}
      </div>
    </div>
  )
}

function IssueList({ tone, icon, title, hint, items }) {
  const color = tone === 'danger' ? 'var(--err, #c4453f)' : 'var(--warn, #b8860b)'
  return (
    <div
      style={{
        border: `1px solid ${color}33`,
        borderRadius: 8,
        padding: '8px 10px',
        background: 'var(--surface2)',
      }}
    >
      <div className="flex items-center gap-1.5 text-[12px]" style={{ color }}>
        {icon}
        <span className="font-medium">{title}</span>
      </div>
      <p className="m-0 mt-1 text-[11px] leading-relaxed text-faint">{hint}</p>
      <ul className="m-0 mt-1.5 list-none p-0">
        {items.slice(0, 8).map((t, i) => (
          <li key={i} className="truncate text-[11.5px] text-faint">
            · {t}
          </li>
        ))}
        {items.length > 8 && (
          <li className="text-[11px] text-faint">…还有 {items.length - 8} 条</li>
        )}
      </ul>
    </div>
  )
}
