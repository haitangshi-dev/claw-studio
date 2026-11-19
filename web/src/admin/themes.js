export const THEMES = [
  {
    id: 'warm',
    name: '暖白',
    desc: '纸感米白，暖橙主色',
    swatch: ['#fbf7f0', '#fffdf9', '#d9713a', '#2c7f9e', '#2b2117'],
  },
  {
    id: 'paper',
    name: '纸灰',
    desc: '中性冷灰，蓝色主色',
    swatch: ['#f4f5f7', '#ffffff', '#3b6fe0', '#0e8f7e', '#1f2430'],
  },
  {
    id: 'forest',
    name: '森林',
    desc: '淡绿纸面，深绿主色',
    swatch: ['#f3f6f1', '#ffffff', '#2f7a4a', '#a9722b', '#1c2a1e'],
  },
  {
    id: 'ink',
    name: '墨黑',
    desc: '深色，低亮度护眼',
    swatch: ['#16161a', '#1e1e23', '#e08a54', '#5fb0ce', '#eceae6'],
    dark: true,
  },
  {
    id: 'indigo',
    name: '靛蓝',
    desc: '深蓝夜色，紫罗兰主色',
    swatch: ['#0f1226', '#171b36', '#8b7cf6', '#4ecdc4', '#e8eaf6'],
    dark: true,
  },
  {
    id: 'contrast',
    name: '高对比',
    desc: '纯黑白，无障碍优先',
    swatch: ['#ffffff', '#ffffff', '#0b57d0', '#b00020', '#000000'],
  },
]

export const THEME_MAP = Object.fromEntries(THEMES.map((t) => [t.id, t]))
