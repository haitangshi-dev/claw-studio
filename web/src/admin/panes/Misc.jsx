import React, { useEffect, useRef, useState } from 'react'
import { Brain, CalendarClock, GitBranch, MessageCircle, Plus, ScrollText } from 'lucide-react'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Row,
  Section,
  Segmented,
  Select,
  Slider,
  Toggle,
  StatusDot,
  Spinner,
} from '../../ui'
import {
  getWechat,
  startWechat,
  stopWechat,
  getWechatMessages,
  sendWechatMessage,
  wechatLogin,
} from '../../api'

export function NotifyPane({ settings, set }) {
  const n = settings.notify
  return (
    <Section title="完成通知" desc="长任务跑完时提醒你，短任务不打扰。">
      <Card>
        <Row label="系统通知" hint="任务耗时超过阈值时弹一条系统提示">
          <Toggle checked={n.system} onChange={(v) => set('notify.system', v)} />
        </Row>
        <Row label="最短耗时" hint="低于这个时长不发通知">
          <Segmented
            size="sm"
            value={n.minSeconds}
            onChange={(v) => set('notify.minSeconds', v)}
            options={[
              { value: 0, label: '每次' },
              { value: 30, label: '30 秒' },
              { value: 60, label: '60 秒' },
              { value: 120, label: '2 分钟' },
            ]}
          />
        </Row>
        <Row label="微信推送" hint="任务完成时同步发一条到微信，需要先绑定">
          <Toggle checked={n.wechat} onChange={(v) => set('notify.wechat', v)} />
        </Row>
      </Card>
    </Section>
  )
}

export function WeChatPane({ settings, set }) {
  const [st, setSt] = useState(null)
  const [items, setItems] = useState([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState('')
  const [note, setNote] = useState('')
  const [qrTick, setQrTick] = useState(Date.now())
  const [showQr, setShowQr] = useState(false)
  const boxRef = useRef(null)

  const loadStatus = () =>
    getWechat()
      .then((d) => setSt(d))
      .catch(() => setSt(null))
  const loadMsgs = () =>
    getWechatMessages(80)
      .then((d) => setItems((d && d.items) || []))
      .catch(() => setItems([]))

  useEffect(() => {
    loadStatus()
    loadMsgs()
    const t1 = setInterval(loadStatus, 4000)
    const t2 = setInterval(loadMsgs, 3000)
    return () => {
      clearInterval(t1)
      clearInterval(t2)
    }
  }, [])

  useEffect(() => {
    if (boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight
  }, [items.length])

  const doLogin = async () => {
    setBusy('login')
    const d = await wechatLogin().catch(() => ({ ok: false, error: '请求失败' }))
    setBusy('')
    setNote(d.ok ? '已拉起扫码窗口，用微信扫下面的码' : d.error || '启动失败')
    if (d.ok) {
      setShowQr(true)
      setQrTick(Date.now())
      ;[2000, 5000, 9000, 14000].forEach((ms) =>
        setTimeout(() => {
          setQrTick(Date.now())
          loadStatus()
        }, ms),
      )
    }
  }

  const toggleRun = async () => {
    setBusy('run')
    const d = st && st.running ? await stopWechat().catch(() => ({})) : await startWechat().catch(() => ({}))
    setBusy('')
    if (d && d.error) setNote(d.error)
    else if (d && d.message) setNote(d.message)
    loadStatus()
  }

  const sendMsg = async () => {
    const text = draft.trim()
    if (!text) return
    setBusy('send')
    const d = await sendWechatMessage(text).catch(() => ({ ok: false, error: '请求失败' }))
    setBusy('')
    if (d.ok) {
      setDraft('')
      setNote('已投递到发件队列')
      loadMsgs()
    } else setNote(d.error || '发送失败')
  }

  const pending = items.filter((i) => i.dir === 'out' && i.state === 'pending')
  const expired = items.filter((i) => i.dir === 'out' && i.state === 'expired')

  return (
    <>
      <Section
        title="微信通道"
        desc="在微信里给机器人发消息，用的是同一个 Agent、同一份记忆。收到与回复都在本机完成。"
        action={<Badge tone={st?.running ? 'success' : 'neutral'}>{st?.running ? '运行中' : '未运行'}</Badge>}
      >
        <Card>
          <Row
            label={
              <span className="flex items-center gap-2">
                <StatusDot tone={st?.running ? 'success' : 'neutral'} pulse={!!st?.running} />
                桥接进程
              </span>
            }
            hint={st?.error ? st.error : st?.running ? '收消息、发消息都在本机，不走第三方服务器' : '未启动时不收消息'}
          >
            <div className="flex items-center gap-2">
              <Button size="sm" variant="ghost" onClick={doLogin} disabled={busy === 'login'}>
                扫码绑定
              </Button>
              <Button size="sm" onClick={toggleRun} disabled={busy === 'run'}>
                {busy === 'run' ? <Spinner size={12} /> : null}
                {st?.running ? '停止' : '启动'}
              </Button>
            </div>
          </Row>
          <Row
            label="绑定账号"
            hint={
              st?.logged_in
                ? `已登录${st?.bound_user ? ` · ${String(st.bound_user).slice(0, 14)}…` : ''}`
                : '还没有绑定：点「扫码绑定」用微信扫一次，凭证加密存在本机'
            }
          >
            <Badge tone={st?.logged_in ? 'success' : 'warning'}>{st?.logged_in ? '已绑定' : '未绑定'}</Badge>
          </Row>
          <Row label="发件队列" hint="窗口过期会被微信打回（错误 -2），这些不再无限重试，超 24 小时自动归档">
            <span className="font-mono text-[11.5px] text-faint">
              待发 {pending.length} · 已过期 {expired.length}
            </span>
          </Row>
        </Card>

        {showQr && (
          <Card className="mt-3 p-4">
            <div className="flex items-start gap-4">
              <img
                src={`/api/wechat/qr?t=${qrTick}`}
                alt="微信登录二维码"
                style={{ width: 132, height: 132, borderRadius: 8, border: '1px solid var(--border)' }}
                onError={(e) => {
                  e.currentTarget.style.opacity = 0.25
                }}
              />
              <div className="min-w-0">
                <p className="m-0 text-[13px]">用微信扫这个码</p>
                <p className="m-0 mt-1.5 text-[12px] leading-relaxed text-faint">
                  扫码后这个窗口不用管，登录成功会自动写入本机凭证。二维码 5 分钟内有效，过期再点一次「扫码绑定」。
                </p>
                <Button size="sm" variant="ghost" className="mt-2" onClick={() => setShowQr(false)}>
                  收起
                </Button>
              </div>
            </div>
          </Card>
        )}

        {note && (
          <p className="m-0 mt-2 text-[11.5px]" style={{ color: 'var(--text-faint)' }}>
            {note}
          </p>
        )}
      </Section>

      <Section title="消息" desc="这里看到的就是桥接目录里真实收发的消息，不是模拟数据。">
        <Card className="p-0">
          <div ref={boxRef} style={{ maxHeight: 320, overflowY: 'auto', padding: '12px 14px' }}>
            {items.length === 0 && (
              <p className="m-0 py-6 text-center text-[12px] text-faint">
                还没有消息。绑定后在微信里给机器人发一条，这里就会出现。
              </p>
            )}
            {items.map((m) => {
              const mine = m.dir === 'out'
              return (
                <div key={m.id} style={{ display: 'flex', justifyContent: mine ? 'flex-end' : 'flex-start', marginBottom: 8 }}>
                  <div style={{ maxWidth: '78%' }}>
                    <div
                      style={{
                        padding: '7px 10px',
                        borderRadius: 10,
                        fontSize: 12.5,
                        lineHeight: 1.55,
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-word',
                        background: mine ? 'var(--primary)' : 'var(--surface2)',
                        color: mine ? 'var(--primary-fg)' : 'var(--text)',
                        opacity: m.state === 'expired' ? 0.55 : 1,
                      }}
                    >
                      {m.text}
                    </div>
                    <div
                      style={{
                        marginTop: 2,
                        fontSize: 10.5,
                        color: 'var(--text-faint)',
                        textAlign: mine ? 'right' : 'left',
                      }}
                    >
                      {fmtTime(m.at)}
                      {mine && m.state === 'pending' ? ' · 待发' : ''}
                      {mine && m.state === 'expired' ? ' · 已过期' : ''}
                      {!mine && m.state === 'queued' ? ' · 待处理（处理进程可能没在跑）' : ''}
                      {!mine && m.state === 'ignored' ? ' · 已忽略' : ''}
                      {mine && m.state === 'sent' ? ' · 已送达' : ''}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>

          <div style={{ display: 'flex', gap: 8, padding: '10px 12px', borderTop: '1px solid var(--border)' }}>
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  sendMsg()
                }
              }}
              placeholder="发一条微信消息（需要 6 天内有会话窗口）"
              style={{
                flex: 1,
                height: 32,
                padding: '0 10px',
                fontSize: 12.5,
                border: '1px solid var(--border)',
                borderRadius: 8,
                background: 'var(--bg)',
                color: 'var(--text)',
              }}
            />
            <Button size="sm" variant="primary" onClick={sendMsg} disabled={busy === 'send' || !draft.trim()}>
              发送
            </Button>
          </div>
        </Card>
      </Section>
    </>
  )
}

function fmtTime(ms) {
  if (!ms) return ''
  const d = new Date(ms)
  const now = new Date()
  const same = d.toDateString() === now.toDateString()
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  return same ? hm : `${d.getMonth() + 1}/${d.getDate()} ${hm}`
}

export function AutomationPane({ settings, set }) {
  const a = settings.automation || {}
  return (
    <>
      <Section
        title="定时任务"
        desc="按时间自动跑一轮任务，完成后唤醒模型做汇报，也可以通过通知把结果推给你。"
        action={
          <Button size="sm" variant="primary">
            <Plus size={13} /> 新建任务
          </Button>
        }
      >
        <EmptyState
          icon={<CalendarClock size={22} />}
          title="还没有定时任务"
          desc="例如：每天早上八点整理昨天的会话要点；每周五检查依赖更新并给出升级建议。"
        />
      </Section>

      <Section title="运行策略" desc="没人看着的时候怎么跑。">
        <Card>
          <Row label="错过运行时间" hint="应用没开着时错过的任务，启动后是否补跑一次">
            <Segmented
              size="sm"
              value={a.missed || 'once'}
              onChange={(v) => set('automation.missed', v)}
              options={[
                { value: 'once', label: '补一次' },
                { value: 'skip', label: '跳过' },
              ]}
            />
          </Row>
          <Row label="完成后唤醒模型" hint="任务结束把结果作为一条事件写进会话，并让模型自己判断要不要继续处理">
            <Toggle checked={a.wakeModel !== false} onChange={(v) => set('automation.wakeModel', v)} />
          </Row>
          <Row label="保留运行历史" hint="每次运行的计划、工具调用、产出都留档，可回看">
            <Select className="w-[140px]" value={a.keep || 50} onChange={(e) => set('automation.keep', Number(e.target.value))}>
              <option value={20}>最近 20 次</option>
              <option value={50}>最近 50 次</option>
              <option value={200}>最近 200 次</option>
            </Select>
          </Row>
        </Card>
      </Section>
    </>
  )
}

export function AboutPane() {
  return (
    <>
      <Section title="关于" desc="Claw Studio —— 跑在本机的桌面智能体。">
        <Card>
          <Row label="版本" hint="构建于 2026-11-19">
            <span className="font-mono text-[12px] text-muted">2026.11.19-dev</span>
          </Row>
          <Row label="运行时" hint="Python + FastAPI，窗口由系统 WebView2 承载">
            <span className="font-mono text-[12px] text-muted">pywebview · chromium</span>
          </Row>
          <Row label="开源许可" hint="可以自由使用、修改、分发">
            <Badge tone="neutral">MIT</Badge>
          </Row>
        </Card>
      </Section>

      <Section title="资源">
        <Card>
          <Row label="源代码仓库" hint="问题与建议走 issue">
            <Button size="sm" variant="ghost">
              <GitBranch size={13} /> GitHub
            </Button>
          </Row>
          <Row label="使用文档" hint="工具清单、权限模型、记忆机制">
            <Button size="sm" variant="ghost">
              <ScrollText size={13} /> 文档
            </Button>
          </Row>
          <Row label="反馈" hint="出问题时附上 logs/ 下的日志会快很多">
            <Button size="sm" variant="ghost">
              <MessageCircle size={13} /> 联系
            </Button>
          </Row>
        </Card>
      </Section>
    </>
  )
}
