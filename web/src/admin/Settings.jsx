import React, { useEffect, useMemo, useState } from 'react'
import {
  Brain,
  CalendarClock,
  Cpu,
  Info,
  MessageCircle,
  Palette,
  Plug,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  X,
} from 'lucide-react'
import { Badge, Card, IconButton, Input, Row, Section, Segmented, Select } from '../ui'
import { useSettings } from './store'
import { CrabMark } from '../ui/brand'
import Appearance from './panes/Appearance'
import Providers from './panes/Providers'
import Permission from './panes/Permission'
import { AboutPane, AutomationPane, MemoryPane, NotifyPane, WeChatPane } from './panes/Misc'

function GeneralPane({ settings, set }) {
  const g = settings.general
  return (
    <>
      <Section title="启动" desc="打开应用后的第一屏。">
        <Card>
          <Row label="进入新会话" hint="启动时总是新建一个空会话；没有任何内容的新会话不会写入磁盘">
            <Badge tone="success">已启用</Badge>
          </Row>
          <Row label="恢复上次会话" hint="关闭后每次启动都从空白开始，适合把应用当工具而不是笔记本">
            <Segmented
              size="sm"
              value={g.restoreLast ? 'on' : 'off'}
              onChange={(v) => set('general.restoreLast', v === 'on')}
              options={[
                { value: 'off', label: '不恢复' },
                { value: 'on', label: '恢复' },
              ]}
            />
          </Row>
        </Card>
      </Section>

      <Section title="输入" desc="聊天框的行为。">
        <Card>
          <Row label="发送快捷键" hint="换行始终可以用 Shift + Enter">
            <Segmented
              size="sm"
              value={g.sendKey}
              onChange={(v) => set('general.sendKey', v)}
              options={[
                { value: 'enter', label: 'Enter 发送' },
                { value: 'mod', label: 'Ctrl + Enter' },
              ]}
            />
          </Row>
          <Row label="保存输入草稿" hint="切换会话后回来，没发出去的内容还在">
            <Segmented
              size="sm"
              value={g.keepDraft === false ? 'off' : 'on'}
              onChange={(v) => set('general.keepDraft', v === 'on')}
              options={[
                { value: 'on', label: '保存' },
                { value: 'off', label: '不保存' },
              ]}
            />
          </Row>
        </Card>
      </Section>

      <Section title="界面语言" desc="目前只有简体中文，其他语言按需再加。">
        <Card>
          <Row label="语言">
            <Select className="w-[160px]" value={g.language} onChange={(e) => set('general.language', e.target.value)}>
              <option value="zh">简体中文</option>
            </Select>
          </Row>
        </Card>
      </Section>
    </>
  )
}

function ExtensionsPane() {
  return (
    <Section title="扩展" desc="技能是外部脚本，插件是进程内工具，两者都会出现在模型可调用的清单里。">
      <Card>
        <Row label="技能目录" hint="skills/<名称>/ 下的 skill.json + SKILL.md + run.py">
          <span className="font-mono text-[12px] text-faint">skills/</span>
        </Row>
        <Row label="插件目录" hint="plugins/<名称>/plugin.py，注册后即时生效">
          <span className="font-mono text-[12px] text-faint">plugins/</span>
        </Row>
        <Row label="管理" hint="技能的增删改在对话里让模型做，也可以直接改目录">
          <Badge tone="neutral">即将开放独立页</Badge>
        </Row>
      </Card>
    </Section>
  )
}

const NAV = [
  { id: 'general', label: '通用', icon: SlidersHorizontal, kw: '启动 语言 快捷键 会话' },
  { id: 'appearance', label: '外观', icon: Palette, kw: '主题 深色 配色 圆角 启动动画' },
  { id: 'providers', label: '模型与 API', icon: Plug, kw: '供应商 api key 模型 端点' },
  { id: 'permission', label: '权限与安全', icon: ShieldCheck, kw: '只读 询问 审批 沙箱 审计 无人值守' },
  { id: 'memory', label: '记忆', icon: Brain, kw: '记忆 预算 遗忘 巩固 抽取' },
  { id: 'wechat', label: '微信', icon: MessageCircle, kw: '微信 绑定 补发 消息' },
  { id: 'notify', label: '通知', icon: Cpu, kw: '通知 提醒 耗时' },
  { id: 'automation', label: '定时任务', icon: CalendarClock, kw: '定时 计划 cron 自动化' },
  { id: 'extensions', label: '扩展', icon: Cpu, kw: '技能 插件 工具' },
  { id: 'about', label: '关于', icon: Info, kw: '版本 许可 仓库' },
]

export default function Settings({ onClose, onPreviewSplash }) {
  const { settings, set, online } = useSettings()
  const [active, setActive] = useState('appearance')
  const [q, setQ] = useState('')

  const nav = useMemo(() => {
    const k = q.trim().toLowerCase()
    if (!k) return NAV
    return NAV.filter((n) => n.label.toLowerCase().includes(k) || n.kw.includes(k))
  }, [q])

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape' && onClose) {
        e.preventDefault()
        onClose()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const current = nav.find((n) => n.id === active) || nav[0] || NAV[0]

  const pane = {
    general: <GeneralPane settings={settings} set={set} />,
    appearance: (
      <Appearance settings={settings} set={set} onPreviewSplash={onPreviewSplash} />
    ),
    providers: <Providers settings={settings} set={set} online={online} />,
    permission: <Permission settings={settings} set={set} />,
    memory: <MemoryPane settings={settings} set={set} />,
    wechat: <WeChatPane settings={settings} set={set} />,
    notify: <NotifyPane settings={settings} set={set} />,
    automation: <AutomationPane settings={settings} set={set} />,
    extensions: <ExtensionsPane />,
    about: <AboutPane />,
  }[current.id]

  return (
    <div className="flex h-full min-h-0 bg-bg text-ink">
      <aside className="flex w-[212px] flex-none flex-col border-r border-line bg-surface2/50">
        <div className="flex items-center gap-2 px-4 pb-3 pt-4">
          <CrabMark size={32} />
          <div>
            <p className="m-0 text-[13px] font-medium leading-tight">Claw Studio</p>
            <p className="m-0 text-[11px] leading-tight text-faint">设置</p>
          </div>
          <IconButton label="关闭设置" className="ml-auto" onClick={onClose}>
            <X size={14} />
          </IconButton>
        </div>

        <div className="px-3 pb-2">
          <div className="relative">
            <Search size={12} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-faint" />
            <Input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="搜索设置项"
              className="h-8 bg-surface pl-7 text-[12.5px]"
            />
          </div>
        </div>

        <nav className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
          {nav.map((n) => {
            const Icon = n.icon
            const on = n.id === current.id
            return (
              <button
                key={n.id}
                onClick={() => setActive(n.id)}
                className="mb-0.5 flex h-8 w-full items-center gap-2 rounded-md px-2 text-left text-[12.5px] transition-colors"
                style={{
                  background: on ? 'var(--surface)' : 'transparent',
                  color: on ? 'var(--text)' : 'var(--text-muted)',
                }}
              >
                <Icon size={13} />
                {n.label}
              </button>
            )
          })}
          {nav.length === 0 && (
            <p className="px-2 py-3 text-[12px] text-faint">没有匹配的设置项</p>
          )}
        </nav>

        <div className="border-t border-line px-3 py-2.5">
          <p className="m-0 text-[11px] text-faint">
            2026.11.19-dev · {online === false ? '离线' : online ? '已连接' : '连接中'}
          </p>
        </div>
      </aside>

      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-[52px] flex-none items-center gap-3 border-b border-line px-6">
          <h1 className="m-0 text-[14px] font-medium">{current.label}</h1>
          {online === false && (
            <Badge tone="warning">
              离线预览：后端未启动，改动只存在浏览器本地
            </Badge>
          )}
          <div className="ml-auto flex items-center gap-2 text-[11.5px] text-faint">
            <span>Esc 关闭</span>
          </div>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
          <div className="mx-auto max-w-[720px] pb-10">{pane}</div>
        </div>
      </main>
    </div>
  )
}
