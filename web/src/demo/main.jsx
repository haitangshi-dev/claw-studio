import React from 'react'
import { createRoot } from 'react-dom/client'
import '../styles.css'
import { installDemoMock } from './mock'
import App from '../App.jsx'

installDemoMock()

function DemoBadge() {
  return (
    <div
      style={{
        position: 'fixed',
        left: 14,
        bottom: 14,
        zIndex: 9999,
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        padding: '5px 10px',
        fontSize: 11.5,
        borderRadius: 999,
        background: 'rgba(43,33,23,.82)',
        color: '#FBF7F0',
        pointerEvents: 'none',
        letterSpacing: '.2px',
      }}
    >
      <span
        style={{ width: 6, height: 6, borderRadius: 999, background: '#E0925C', display: 'inline-block' }}
      />
      演示模式 · 无推理后端
    </div>
  )
}

createRoot(document.getElementById('root')).render(
  <>
    <App />
    <DemoBadge />
  </>,
)
