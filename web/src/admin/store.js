import { useCallback, useEffect, useRef, useState } from 'react'

const LS_KEY = 'claw-studio-settings'

export const DEFAULT_SETTINGS = {
  appearance: {
    theme: 'warm',
    followSystem: false,
    radius: 12,
    density: 'comfortable',
  },
  general: {
    language: 'zh',
    splash: 'orbit',
    splashEnabled: true,
    sendKey: 'enter',
  },
  providers: [],
  roles: { smart: '', fast: '', vision: '', compact: '', embedding: '' },
  permission: {
    onDuty: 'ask',
    unattendedPolicy: 'safe',
    confirmDestructive: true,
  },
  memory: {
    autoExtract: true,
    autoConsolidate: true,
    budgetRatio: 6,
    forgetConfirm: true,
  },
  notify: {
    system: true,
    minSeconds: 60,
    wechat: false,
  },
  wechat: {
    enabled: false,
    autoStart: false,
  },
}

function merge(base, patch) {
  if (!patch || typeof patch !== 'object' || Array.isArray(patch)) return patch ?? base
  const out = { ...base }
  for (const [k, v] of Object.entries(patch)) {
    out[k] = v && typeof v === 'object' && !Array.isArray(v) ? merge(base?.[k] || {}, v) : v
  }
  return out
}

export function loadLocal() {
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (!raw) return DEFAULT_SETTINGS
    return merge(DEFAULT_SETTINGS, JSON.parse(raw))
  } catch {
    return DEFAULT_SETTINGS
  }
}

export function applyAppearance(settings) {
  const el = document.documentElement
  const theme = settings.appearance?.theme || 'warm'
  el.dataset.theme = theme
  el.style.setProperty('--radius', `${settings.appearance?.radius ?? 12}px`)
  el.style.setProperty('--radius-sm', `${Math.max(4, (settings.appearance?.radius ?? 12) - 4)}px`)
}

export function useSettings() {
  const [settings, setSettings] = useState(loadLocal)
  const [online, setOnline] = useState(null)
  const dirty = useRef(false)

  useEffect(() => {
    applyAppearance(settings)
  }, [settings.appearance])

  useEffect(() => {
    let alive = true
    fetch('/api/settings')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error('offline'))))
      .then((d) => {
        if (!alive || !d || !d.settings) return
        setOnline(true)
        if (!dirty.current) setSettings((cur) => merge(cur, d.settings))
      })
      .catch(() => alive && setOnline(false))
    return () => {
      alive = false
    }
  }, [])

  useEffect(() => {
    try {
      localStorage.setItem(LS_KEY, JSON.stringify(settings))
    } catch {
      /* 存储满或隐私模式：忽略 */
    }
    if (online !== true) return
    const t = setTimeout(() => {
      fetch('/api/settings', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settings),
      }).catch(() => {})
    }, 400)
    return () => clearTimeout(t)
  }, [settings, online])

  const update = useCallback((patch) => {
    dirty.current = true
    setSettings((cur) => merge(cur, patch))
  }, [])

  const set = useCallback((path, value) => {
    dirty.current = true
    setSettings((cur) => {
      const keys = path.split('.')
      const next = { ...cur }
      let node = next
      for (let i = 0; i < keys.length - 1; i++) {
        node[keys[i]] = { ...(node[keys[i]] || {}) }
        node = node[keys[i]]
      }
      node[keys.at(-1)] = value
      return next
    })
  }, [])

  return { settings, set, update, online }
}
