import { useEffect, useMemo, useState } from 'react'
import { getWorkdirs, openPath, pickFolder, registerWorkdir } from '../api.js'
import { IconCheck, IconFolder, IconRefresh, IconShield, IconX } from '../icons.jsx'

const norm = (p) =>
  String(p || '')
    .replace(/\//g, '\\')
    .replace(/\\+$/, '')
    .toLowerCase()

const fmtTime = (ts) => {
  if (!ts) return '默认目录'
  const d = new Date(ts)
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  if (ts >= today) return `今天 ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  if (ts >= today - 86400000) return '昨天'
  return `${d.getMonth() + 1}月${d.getDate()}日`
}

/**
 * 工作目录选择器（自绘 UI + 后端原生资源管理器对话框）。
 * 同一目录下的会话共享该目录记忆；重要记忆会自动提取到全局。
 */
export function WorkdirPanel({ current, busy, onPick, onClose }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [picking, setPicking] = useState(false)
  const [opening, setOpening] = useState('')
  const [manual, setManual] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')

  const load = () => {
    setLoading(true)
    getWorkdirs()
      .then((d) => {
        if (!d || d.ok === false) throw new Error((d && d.error) || '目录列表读取失败')
        setData(d)
        setErr('')
      })
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false))
  }
  useEffect(() => {
    load()
  }, [])

  const items = data?.items || []
  const currentItem = useMemo(
    () => items.find((x) => norm(x.path) === norm(current)),
    [items, current]
  )
  const currentKey = currentItem?.key || ''

  const choose = (path) => {
    if (busy || !path) return
    onPick(path)
  }

  const browse = async () => {
    if (busy || picking) return
    setErr('')
    setMsg('')
    setPicking(true)
    try {
      const r = await pickFolder(current)
      if (r && r.ok && r.path) {
        onPick(r.path)
      } else if (r && r.canceled) {
        setMsg('已取消选择')
      } else {
        setErr((r && r.error) || '目录选择失败')
      }
    } catch (e) {
      setErr(String(e))
    } finally {
      setPicking(false)
    }
  }

  const useManual = async () => {
    const p = manual.trim()
    if (!p || busy) return
    setErr('')
    setMsg('')
    try {
      const r = await registerWorkdir(p)
      if (!r || r.ok === false) {
        setErr((r && r.error) || '目录无效')
        return
      }
      onPick((r.workdir && r.workdir.path) || r.path)
    } catch (e) {
      setErr(String(e))
    }
  }

  const reveal = async (path) => {
    setOpening(path)
    try {
      const r = await openPath('path', path)
      if (r && r.ok === false) setErr(r.error || '打开失败')
    } catch (e) {
      setErr(String(e))
    } finally {
      setOpening('')
    }
  }

  return (
    <div className="overlay" onClick={onClose}>
      <div className="panel wd-panel" onClick={(e) => e.stopPropagation()}>
        <div className="p-head">
          <span style={{ display: 'flex', color: 'var(--cyan)' }}><IconFolder size={17} /></span>
          <span className="p-title">选择工作目录</span>
          <span className="spacer" />
          <button className="icon-btn ghost" title="刷新" onClick={load} disabled={loading}>
            <IconRefresh />
          </button>
          <button className="icon-btn ghost" title="关闭" onClick={onClose}><IconX /></button>
        </div>

        <div className="p-body wd-body">
          <div className="wd-note">
            <div className="wd-note-title"><IconShield size={13} /> 目录记忆 · 沙箱</div>
            <p>
              同一目录下的所有会话<strong>共享该目录记忆</strong>（会话 A 能看到会话 B 的积累）；
              重要度 ≥ 4 的记忆会自动提取到<strong>全局记忆</strong>。
              标准权限下文件写入被限制在当前目录（沙箱），完全权限不设限制。
            </p>
          </div>

          {err && <div className="wd-err">{err}</div>}
          {msg && <div className="wd-msg">{msg}</div>}

          <div className="wd-current">
            <div className="wd-cur-head">
              <span className="wd-cur-label">当前会话工作目录</span>
              {currentItem?.is_default && <span className="wd-badge">默认</span>}
            </div>
            <div className="wd-cur-path" title={current || ''}>
              <IconFolder size={13} />
              <span>{current || '（未设置，将使用默认目录）'}</span>
            </div>
            <div className="wd-cur-actions">
              <button className="wd-primary" disabled={busy || picking} onClick={browse}>
                {picking ? '资源管理器已打开…' : '在资源管理器中选择…'}
              </button>
              <button
                className="wd-ghost"
                disabled={!current || !!opening}
                onClick={() => reveal(current)}
              >
                {opening ? '打开中…' : '打开当前目录'}
              </button>
            </div>
            {busy && <div className="wd-hint">会话运行中，选择器暂不可切换目录。</div>}
          </div>

          <div className="wd-manual">
            <input
              value={manual}
              placeholder="也可以直接粘贴/输入目录路径，例如 D:\projects\demo"
              onChange={(e) => setManual(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') useManual()
              }}
              disabled={busy}
            />
            <button className="wd-ghost" disabled={!manual.trim() || busy} onClick={useManual}>
              使用此目录
            </button>
          </div>

          <div className="wd-list-head">
            <span>最近使用</span>
            <span className="wd-global">全局记忆 {data?.global_count ?? 0} 条</span>
          </div>

          {loading && <div className="wd-empty">正在读取最近目录…</div>}
          {!loading && items.length === 0 && <div className="wd-empty">还没有使用过其他目录。</div>}

          <div className="wd-list">
            {items.map((it) => {
              const active = norm(it.path) === norm(current)
              return (
                <div
                  key={it.key || it.path}
                  className={`wd-item ${active ? 'active' : ''} ${it.exists ? '' : 'missing'}`}
                  onClick={() => choose(it.path)}
                  title={it.exists ? it.path : `${it.path}（目录不存在）`}
                >
                  <span className={`wd-ico ${active ? 'on' : ''}`}><IconFolder size={16} /></span>
                  <div className="wd-item-main">
                    <div className="wd-item-top">
                      <span className="wd-item-name">{it.name || it.path}</span>
                      {it.is_default && <span className="wd-badge">默认</span>}
                      {active && <span className="wd-badge on"><IconCheck size={10} /> 当前</span>}
                      {!it.exists && <span className="wd-badge bad">不存在</span>}
                    </div>
                    <div className="wd-item-path">{it.path}</div>
                    <div className="wd-item-meta">
                      <span>{fmtTime(it.last_used)}</span>
                      <span>记忆 {it.memory_count ?? 0} 条</span>
                      <span>会话 {it.session_count ?? 0} 个</span>
                      <span>使用 {it.uses ?? 0} 次</span>
                    </div>
                  </div>
                  <div className="wd-item-act">
                    <button
                      className="wd-mini"
                      title="在资源管理器中打开"
                      onClick={(e) => {
                        e.stopPropagation()
                        reveal(it.path)
                      }}
                    >
                      打开
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
          <div className="wd-foot">
            目录键：<code>{currentKey || '—'}</code>
            <span className="spacer" />
            选择后立即生效，并随会话保存。
          </div>
        </div>
      </div>
    </div>
  )
}
