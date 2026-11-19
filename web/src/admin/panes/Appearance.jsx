import React, { useEffect } from 'react'
import { Check, Monitor, Moon, Palette, Sun } from 'lucide-react'
import { THEMES } from '../themes'
import { Badge, Button, Card, Row, Segmented, Section, Slider, Toggle } from '../../ui'

const CUSTOM_KEYS = [
  { key: 'bg', css: '--bg', label: '背景' },
  { key: 'surface', css: '--surface', label: '表面' },
  { key: 'primary', css: '--primary', label: '主色' },
  { key: 'accent', css: '--accent', label: '强调' },
  { key: 'text', css: '--text', label: '文字' },
]

export default function Appearance({ settings, set, onPreviewSplash }) {
  const a = settings.appearance
  const custom = a.custom || {}

  useEffect(() => {
    const el = document.documentElement
    for (const c of CUSTOM_KEYS) {
      if (custom[c.key]) el.style.setProperty(c.css, custom[c.key])
      else el.style.removeProperty(c.css)
    }
  }, [custom.bg, custom.surface, custom.primary, custom.accent, custom.text])

  const pick = (id) => {
    set('appearance.theme', id)
    set('appearance.custom', {})
  }

  const setCustom = (key, value) => {
    set('appearance.custom', { ...custom, [key]: value })
  }

  return (
    <>
      <Section
        title="主题"
        desc="主题切换即时生效，设置会保存在本机。深色主题适合夜间，高对比主题优先保证可读性。"
        action={
          <Button size="sm" variant="ghost" onClick={() => pick('warm')}>
            恢复默认
          </Button>
        }
      >
        <div className="grid grid-cols-3 gap-3">
          {THEMES.map((t) => {
            const active = a.theme === t.id && !Object.keys(custom).length
            return (
              <button
                key={t.id}
                onClick={() => pick(t.id)}
                className="group relative rounded-lg border bg-surface p-3 text-left transition-colors duration-150"
                style={{ borderColor: active ? 'var(--primary)' : 'var(--border)' }}
              >
                <div className="mb-2.5 flex items-center gap-1">
                  {t.swatch.slice(1, 5).map((c, i) => (
                    <span
                      key={i}
                      className="h-4 w-4 rounded-full border border-black/5"
                      style={{ background: c }}
                    />
                  ))}
                  <span className="ml-auto text-faint">
                    {t.dark ? <Moon size={13} /> : <Sun size={13} />}
                  </span>
                </div>
                <p className="m-0 flex items-center gap-1 text-[13px]">
                  {t.name}
                  {active && <Check size={13} style={{ color: 'var(--primary)' }} />}
                </p>
                <p className="m-0 mt-0.5 text-[11.5px] text-faint">{t.desc}</p>
              </button>
            )
          })}
        </div>

        <Card className="mt-3">
          <Row
            label="跟随系统深浅色"
            hint="熄灯后自动切到深色主题，白天回到浅色（仅在你的主题为暖白或纸灰时生效）"
          >
            <Toggle
              checked={a.followSystem}
              onChange={(v) => set('appearance.followSystem', v)}
            />
          </Row>
          <Row label="圆角" hint="影响按钮、卡片、输入框的整体风格">
            <Slider
              value={a.radius}
              min={4}
              max={18}
              onChange={(v) => set('appearance.radius', v)}
              suffix="px"
            />
          </Row>
          <Row label="信息密度" hint="紧凑模式缩小行高与间距，一屏能看到更多内容">
            <Segmented
              value={a.density}
              onChange={(v) => set('appearance.density', v)}
              options={[
                { value: 'comfortable', label: '舒适' },
                { value: 'compact', label: '紧凑' },
              ]}
            />
          </Row>
        </Card>
      </Section>

      <Section
        title="自定义配色"
        desc="只改这五个色，其余色阶按明度自动派生。留空表示沿用当前主题。"
        action={
          Object.keys(custom).length ? (
            <Badge tone="primary">已自定义</Badge>
          ) : (
            <Button size="sm" variant="ghost" onClick={() => set('appearance.custom', {})}>
              清空
            </Button>
          )
        }
      >
        <Card className="p-4">
          <div className="grid grid-cols-5 gap-3">
            {CUSTOM_KEYS.map((c) => (
              <label key={c.key} className="flex flex-col items-center gap-1.5">
                <span
                  className="relative h-10 w-full overflow-hidden rounded-md border border-line"
                  style={{ background: custom[c.key] || `var(${c.css})` }}
                >
                  <input
                    type="color"
                    value={custom[c.key] || '#000000'}
                    onChange={(e) => setCustom(c.key, e.target.value)}
                    className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
                  />
                </span>
                <span className="text-[11.5px] text-muted">{c.label}</span>
              </label>
            ))}
          </div>
          <p className="m-0 mt-3 flex items-center gap-1.5 text-[11.5px] text-faint">
            <Palette size={12} /> 自定义配色会覆盖当前主题，主题卡片上的选中标记随之取消。
          </p>
        </Card>
      </Section>

      <Section title="启动动画" desc="播放动画的同时并行加载资源，动画结束即进入工作区。">
        <Card>
          <Row label="启用启动动画" hint="关闭后直接进主界面，冷启动会短暂显示空白">
            <Toggle
              checked={settings.general.splashEnabled}
              onChange={(v) => set('general.splashEnabled', v)}
            />
          </Row>
          <Row label="动画样式" hint="也可以把自己的视频或序列帧放进 config/splash/ 自定义">
            <Segmented
              size="sm"
              value={settings.general.splash}
              onChange={(v) => set('general.splash', v)}
              options={[
                { value: 'orbit', label: '轨道' },
                { value: 'pulse', label: '呼吸' },
                { value: 'scan', label: '扫描' },
                { value: 'minimal', label: '极简' },
              ]}
            />
          </Row>
          <Row label="预览" hint="按当前选择播放一次">
            <Button size="sm" onClick={onPreviewSplash}>
              播放
            </Button>
          </Row>
        </Card>
      </Section>

      <Section title="窗口" desc="桌面客户端的显示偏好。">
        <Card>
          <Row label="启动时进入新会话" hint="每次启动都是空白会话，无内容的新会话不会保存到磁盘">
            <Badge tone="success">已启用</Badge>
          </Row>
          <Row label="最小窗口尺寸" hint="920 × 600，低于此尺寸布局会切到窄屏模式">
            <span className="font-mono text-[12px] text-faint">920 × 600</span>
          </Row>
        </Card>
      </Section>
    </>
  )
}
