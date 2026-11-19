import React, { useCallback, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import '../theme.css'
import Splash from './Splash'
import Settings from './Settings'
import { loadLocal } from './store'

// 嵌在主窗口里（设置覆盖层）时不要播启动动画，也不显示「重新打开」兜底页——
// 那两样都是独立窗口才需要的。
const EMBEDDED = (() => {
  try {
    return window.self !== window.top
  } catch (e) {
    return true
  }
})()

function App() {
  const boot = loadLocal()
  const [splash, setSplash] = useState(!EMBEDDED && boot.general?.splashEnabled !== false)
  const [open, setOpen] = useState(true)
  const [round, setRound] = useState(0)

  const replay = useCallback(() => {
    setSplash(true)
    setOpen(true)
    setRound((r) => r + 1)
  }, [])

  const close = () => {
    setOpen(false)
    if (EMBEDDED) {
      try {
        window.parent.postMessage({ type: 'claw:close-settings' }, '*')
      } catch (e) {
        /* 跨域时忽略，父窗口还有自己的关闭方式 */
      }
    }
  }

  return (
    <div className="h-full">
      {open ? (
        <Settings onClose={close} onPreviewSplash={replay} />
      ) : (
        <div className="flex h-full flex-col items-center justify-center gap-3">
          <p className="m-0 text-[13px] text-muted">设置已关闭（预览模式）</p>
          <button
            onClick={() => setOpen(true)}
            className="h-9 rounded-md bg-primary px-4 text-[13px] text-primary-fg"
          >
            重新打开设置
          </button>
        </div>
      )}
      {splash && (
        <Splash
          key={round}
          variant={boot.general?.splash || 'orbit'}
          onDone={() => setSplash(false)}
        />
      )}
    </div>
  )
}

createRoot(document.getElementById('root')).render(<App />)
