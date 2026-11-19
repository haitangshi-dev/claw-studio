import React, { useCallback, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import '../theme.css'
import Splash from './Splash'
import Settings from './Settings'
import { loadLocal } from './store'

function App() {
  const boot = loadLocal()
  const [splash, setSplash] = useState(boot.general?.splashEnabled !== false)
  const [open, setOpen] = useState(true)
  const [round, setRound] = useState(0)

  const replay = useCallback(() => {
    setSplash(true)
    setOpen(true)
    setRound((r) => r + 1)
  }, [])

  return (
    <div className="h-full">
      {open ? (
        <Settings onClose={() => setOpen(false)} onPreviewSplash={replay} />
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
