import { useEffect, useMemo, useRef, useState } from 'react'
import { ALL_STAFF, OFFICE_ROOMS, findRoom, roomOf } from '../office.js'
import { ClawLogo, IconCheck, IconFolder, IconX } from '../icons.jsx'
import { CrabFace, MessageView } from './Chat.jsx'

const FILTERS = [
  ['all', '全部'], ['boss', '老板'], ['supervisor', '主管'],
  ['employee', '员工'], ['claw', 'Claw'], ['framework', '框架'],
]
const SEATS = [[32, 43], [51, 43], [72, 43], [32, 67], [51, 67], [72, 67]]
const EMPTY_MESSAGES = []

const messageKind = (m) => {
  const role = String(m.sender?.role || '')
  if (m.origin === 'framework' || /框架|系统/.test(role)) return 'framework'
  if (m.role === 'user') return 'boss'
  if (role.includes('主管')) return 'supervisor'
  if (m.sender) return 'employee'
  return 'claw'
}
const messageName = (m) => m.sender?.name || (m.role === 'user' ? '老板' : 'Claw')
// 群聊里这条消息属于哪个部门：后端 sender.room 优先，历史消息按 team_id 反查
const messageRoom = (m) => {
  if (!m || m.role === 'user') return ''
  return m.sender?.room || roomOf(m.team_id)?.name || ''
}

export function OfficePanel({ currentRoomId, onChangeRoom, onClose, session, workdir = '', qoderQuota, busy, onSpeak, speakingId }) {
  const [filter, setFilter] = useState('all')
  const [person, setPerson] = useState('')
  const [roomFilter, setRoomFilter] = useState('')
  const panelRef = useRef(null)
  const feedRef = useRef(null)
  const followRef = useRef(true)
  const room = findRoom(currentRoomId)
  const messages = session?.messages || EMPTY_MESSAGES

  const tasks = useMemo(() => messages.flatMap((m) => (m.toolCalls || [])
    .filter((tc) => String(tc.name || '').replace(/^mcp__claw-agent__/, '') === 'office_dispatch')
    .map((tc, i) => {
      let args = {}
      try { args = typeof tc.arguments === 'string' ? JSON.parse(tc.arguments || '{}') : tc.arguments || {} } catch { /* incomplete streaming arguments */ }
      return { ...tc, id: tc.id || `${m.id}_${i}`, task: args?.task, room: args?.room }
    })).reverse(), [messages])

  const feed = useMemo(() => {
    let visible = messages.filter((m) => m && (m.role === 'user' || m.role === 'assistant'))
    // 群聊：默认不分部门，全部来源同一条消息流；仅在用户点了部门筛选时收窄
    if (roomFilter) visible = visible.filter((m) => messageRoom(m) === roomFilter)
    if (person) return visible.filter((m) => messageName(m) === person)
    if (filter === 'all') return visible
    if (filter !== 'framework') return visible.filter((m) => messageKind(m) === filter)
    return visible.flatMap((m) => {
      if (messageKind(m) === 'framework') return [m]
      const notices = m.timeline?.length
        ? m.timeline.filter((s) => s.kind === 'notice').map((s) => s.text)
        : m.notices || []
      return notices.map((text, i) => ({ id: `${m.id}_notice_${i}`, role: 'assistant', content: text, done: true, team: true, sender: { name: '框架', role: '框架', room: '框架', room_id: 'framework', color: '#6A5A8A' } }))
    })
  }, [messages, filter, person, roomFilter])

  useEffect(() => {
    const previous = document.activeElement
    panelRef.current?.focus()
    return () => { if (previous?.isConnected) previous.focus() }
  }, [])

  useEffect(() => {
    setFilter('all')
    setPerson('')
    setRoomFilter('')
  }, [session?.id])

  useEffect(() => { followRef.current = true }, [filter, person, roomFilter])
  useEffect(() => {
    if (followRef.current && feedRef.current) feedRef.current.scrollTop = feedRef.current.scrollHeight
  }, [feed])

  const selectFilter = (value) => { setPerson(''); setFilter(value) }
  const selectRoom = (id) => { setPerson(''); setFilter('all'); onChangeRoom(id) }
  const selectPerson = (name) => { setPerson((prev) => prev === name ? '' : name); setFilter('all') }
  const q = qoderQuota?.ok ? qoderQuota : null
  const seats = [...room.staff, 'Claw']
  const countFor = (name) => messages.filter((m) => messageName(m) === name).length
  const toggleRoomFilter = (name) => setRoomFilter((prev) => (prev === name ? '' : name))
  const runningTasks = tasks.filter((t) => t.status === 'running').length
  const isWorking = (name) => messages.some((m) => messageName(m) === name && m.at && Number(m.at) * 1000 > Date.now() - 90000)

  const onKeyDown = (event) => {
    if (event.key === 'Escape') { event.stopPropagation(); onClose(); return }
    if (event.key !== 'Tab') return
    const items = [...panelRef.current.querySelectorAll('button:not(:disabled), a[href], input, textarea, select, [tabindex="0"]')]
      .filter((el) => el.getClientRects().length)
    const first = items[0], last = items[items.length - 1]
    if (event.shiftKey && (document.activeElement === first || document.activeElement === panelRef.current)) {
      event.preventDefault(); last?.focus()
    } else if (!event.shiftKey && (document.activeElement === last || document.activeElement === panelRef.current)) {
      event.preventDefault(); first?.focus()
    }
  }

  return (
    <div className="office-screen" ref={panelRef} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="office-title" onKeyDown={onKeyDown}>
      <header className="os-head">
        <span className="os-brand"><ClawLogo size={22} /><strong id="office-title">Claw 办公室</strong></span>
        <span className="os-divider" />
        <span className="os-session-title">{session?.title || '新会话'}</span>
        <span className="os-live"><i className={busy ? 'active' : ''} />{busy ? '当前会话执行中' : '与主聊天同步'}</span>
        <button className="os-back" onClick={onClose}>返回聊天 <span>↗</span></button>
        <button className="os-close" aria-label="关闭办公室" onClick={onClose}><IconX size={16} /></button>
      </header>

      <div className="os-body">
        <aside className="os-left" aria-label="房间与成员">
          <div className="os-sec-title">工作空间 <span>03</span></div>
          <nav className="os-rooms" aria-label="办公室房间">
            {OFFICE_ROOMS.map((r) => (
              <button key={r.id} className={`os-room-card ${room.id === r.id ? 'on' : ''}`} aria-pressed={room.id === r.id} onClick={() => selectRoom(r.id)}>
                <span className="os-dot" style={{ background: r.color }} />
                <span>{r.name}<small>{r.staff.length} 位成员 · Qoder</small></span>
                {room.id === r.id && <IconCheck size={13} />}
              </button>
            ))}
          </nav>
          <div className="os-sec-title">群成员 · 全部部门 <span>{ALL_STAFF.length}</span></div>
          <div className="os-people os-people-all">
            {OFFICE_ROOMS.map((r) => (
              <div className={`os-group ${roomFilter === r.name ? 'on' : ''}`} key={r.id}>
                <button
                  className="os-group-head"
                  onClick={() => toggleRoomFilter(r.name)}
                  aria-pressed={roomFilter === r.name}
                  title={`只看「${r.name}」的消息`}
                >
                  <span className="os-dot" style={{ background: r.color }} />
                  <strong>{r.name}</strong>
                  <span className="os-group-count">{r.staff.length}</span>
                </button>
                {r.staff.map((name) => (
                  <button className={`os-person ${person === name ? 'on' : ''}`} key={name} aria-pressed={person === name} onClick={() => selectPerson(name)}>
                    <span className="os-avatar" style={{ background: `${r.color}28`, color: r.color }}>{name.slice(0, 1)}</span>
                    <span className="os-person-main"><strong>{name}</strong><small>{name.includes('主管') ? '主管' : '员工'} · {countFor(name)} 条消息</small></span>
                  </button>
                ))}
              </div>
            ))}
          </div>
          <div className="os-sec-title">协作参与者</div>
          {['Claw', '老板'].map((name) => (
            <button className={`os-person ${person === name ? 'on' : ''}`} key={name} aria-pressed={person === name} onClick={() => selectPerson(name)}>
              <span className={`os-avatar ${name === '老板' ? 'boss' : 'claw'}`}>{name === 'Claw' ? <ClawLogo size={18} /> : '老'}</span>
              <span className="os-person-main"><strong>{name}</strong><small>{name === 'Claw' ? '老板助理' : '你'} · {countFor(name)} 条消息</small></span>
            </button>
          ))}
          <div className="os-left-note">只使用 Qoder<br />切换房间不会隐藏其他部门的消息。</div>
        </aside>

        <main className="os-center">
          <section className="os-room" aria-label={`${room.name}工位视图`}>
            <div className="os-scene-head"><span className="os-dot" style={{ background: room.color }} /><strong>{room.name}</strong><span>{room.staff.length} 位成员 + Claw 助理</span></div>
            <div className="os-scene">
              <img className="os-scene-bg" src="/office/assets/office-bg-empty.png" alt="夜间办公室，六个工位、书架和茶水区" />
              {seats.map((name, i) => (
                <button key={name} className={`os-seat ${person === name ? 'selected' : ''} ${isWorking(name) ? 'working' : ''}`} style={{ left: `${SEATS[i][0]}%`, top: `${SEATS[i][1]}%`, '--seat-color': name === 'Claw' ? '#81bbbd' : room.color }} onClick={() => selectPerson(name)} aria-label={`查看${name}的消息`} aria-pressed={person === name}>
                  <span className="os-seat-avatar"><CrabFace size={23} /></span>
                  <span className="os-seat-name">{name}</span>
                </button>
              ))}
              <span className="os-scene-caption">工位展示 · 点击成员查看发言</span>
            </div>
          </section>

          <section className="os-feed" aria-label="当前会话完整消息">
            <div className="os-feed-head">
              <div><strong>会话消息</strong><span className="os-feed-count">{feed.length} / {messages.length}</span></div>
              <span className="os-muted">群聊 · {roomFilter || '所有部门'}</span>
            </div>
            <div className="os-room-tabs" aria-label="部门筛选">
              <button className={`os-filter ${roomFilter ? '' : 'on'}`} aria-pressed={!roomFilter} onClick={() => setRoomFilter('')}>全部部门</button>
              {OFFICE_ROOMS.map((r) => (
                <button
                  key={r.id}
                  className={`os-filter ${roomFilter === r.name ? 'on' : ''}`}
                  aria-pressed={roomFilter === r.name}
                  onClick={() => toggleRoomFilter(r.name)}
                >
                  <span className="os-dot" style={{ background: r.color }} />{r.name}
                </button>
              ))}
            </div>
            <div className="os-filters" aria-label="消息来源筛选">
              {FILTERS.map(([key, label]) => (
                <button key={key} className={`os-filter ${filter === key && !person ? 'on' : ''}`} aria-pressed={filter === key && !person} onClick={() => selectFilter(key)}>{label}</button>
              ))}
              {person && <button className="os-person-filter" onClick={() => selectFilter('all')}>{person}<IconX size={12} /></button>}
            </div>
            <div className="os-feed-list" ref={feedRef} tabIndex={0} onScroll={(e) => {
              const el = e.currentTarget
              followRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60
            }}>
              {!feed.length ? <div className="os-empty">{filter !== 'all' || person ? '暂无此来源的消息，点击「全部」查看完整会话。' : '还没有消息。返回聊天交代任务，老板、Claw、主管、员工和框架的消息都会同步显示在这里。'}</div> : feed.map((m) => (
                <div className={`os-message os-${messageKind(m)}`} data-message-id={m.id} key={m.id}>
                  <MessageView msg={m} streaming={busy && !m.done} onSpeak={onSpeak} speaking={speakingId === m.id} />
                </div>
              ))}
            </div>
            <div className="os-feed-foot">这里展示完整会话记录，不另开对话。<button onClick={onClose}>回聊天发消息 ↗</button></div>
          </section>
        </main>

        <aside className="os-right" aria-label="任务与额度">
          <section className="os-card">
            <div className="os-card-eyebrow">QODER · 账户额度</div>
            <div className="os-big">{q?.remaining != null ? Number(q.remaining).toLocaleString('zh-CN', { maximumFractionDigits: 2 }) : '—'}<small>credits</small></div>
            <div className="os-muted">{q ? q.plan || '当前套餐' : '额度尚未同步'}</div>
            <div className="os-quota-note">{q?.at ? `最近同步 ${new Date(q.at * 1000).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })}` : '暂无可用快照，不估算余额'}<br />账户共享额度，并非本会话 Token。</div>
          </section>
          <section className="os-card os-task-card">
            <div className="os-card-title"><strong>办公室任务</strong><span>{tasks.length}</span></div>
            <div className="os-muted os-task-summary">{runningTasks ? `${runningTasks} 项正在调度` : '来自本会话的实际调度记录'}</div>
            {!tasks.length ? <div className="os-task-empty"><IconFolder size={25} /><span>暂未派发办公室任务</span><small>交给 Claw 转达给主管</small></div> : tasks.map((t) => {
              const state = t.status === 'running' ? (busy ? 'running' : 'unknown') : t.status === 'done' ? (t.ok === false ? 'failed' : t.ok === true ? 'done' : 'unknown') : 'unknown'
              const labels = { running: '调度中', failed: '调用失败', done: '调度已结束', unknown: '状态待确认' }
              return <div className="os-task" key={t.id}>
                <div className="os-task-title">{t.task || '办公室任务'}</div>
                <div className="os-task-meta"><span>{t.room ? findRoom(t.room).name : '办公室'}</span><span className={`os-pill ${state}`}>{labels[state]}</span></div>
                {t.seconds != null && <small className="os-muted">耗时 {t.seconds}s</small>}
              </div>
            })}
          </section>
          <section className="os-card os-session-card">
            <div className="os-card-title"><strong>当前会话</strong></div>
            <div className="os-muted">{session?.title || '新会话'}</div>
            <div className="os-mode">{session?.agentMode === 'unattended' ? `无人监管 · ${session.unattendedMinutes === 0 ? '无限' : `${session.unattendedMinutes || 30} 分钟`}` : '有人值守'}</div>
            <div className="os-workdir" title={workdir}><IconFolder size={13} />{workdir || '默认工作目录'}</div>
          </section>
        </aside>
      </div>
    </div>
  )
}
