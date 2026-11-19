// 线稿 SVG 图标集（stroke 1.5px，无 emoji）

const base = {
  width: 16, height: 16, viewBox: '0 0 24 24',
  fill: 'none', stroke: 'currentColor',
  strokeWidth: 1.5, strokeLinecap: 'round', strokeLinejoin: 'round',
}

let logoSeq = 0

// 品牌图形：三根爪痕（默认单色；twoTone 时紫→青渐变）
export function ClawLogo({ size = 24, color = '#2B2117', twoTone = false }) {
  const id = `clawG${logoSeq++}`
  const stroke = twoTone ? `url(#${id})` : color
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none">
      {twoTone && (
        <defs>
          <linearGradient id={id} x1="4" y1="4" x2="28" y2="28" gradientUnits="userSpaceOnUse">
            <stop offset="0%" stopColor="#F0A05C" />
            <stop offset="100%" stopColor="#2C7F9E" />
          </linearGradient>
        </defs>
      )}
      <path d="M9 21.5C6.8 17.5 6.3 12.5 7.8 8" stroke={stroke} strokeWidth="3.4" strokeLinecap="round" />
      <path d="M16 23c-2.2-4.6-2.7-10-1.1-15" stroke={stroke} strokeWidth="3.4" strokeLinecap="round" />
      <path d="M23 21.5C20.8 17.5 20.3 12.5 21.8 8" stroke={stroke} strokeWidth="3.4" strokeLinecap="round" />
      <path d="M7.5 26.5c5.6 3 12 3 17 0" stroke={stroke} strokeWidth="3.4" strokeLinecap="round" />
    </svg>
  )
}

export const IconSend = (p) => (
  <svg {...base} {...p} width={p?.size || 16} height={p?.size || 16}>
    <path d="M5 12h13M13 6l6 6-6 6" />
  </svg>
)

export const IconStop = (p) => (
  <svg {...base} {...p} width={p?.size || 16} height={p?.size || 16}>
    <rect x="7" y="7" width="10" height="10" rx="1.5" />
  </svg>
)

export const IconPlus = (p) => (
  <svg {...base} {...p} width={p?.size || 16} height={p?.size || 16}>
    <path d="M12 5v14M5 12h14" />
  </svg>
)

export const IconChevron = (p) => (
  <svg {...base} {...p} width={p?.size || 14} height={p?.size || 14}>
    <path d="M9 6l6 6-6 6" />
  </svg>
)

export const IconBrain = (p) => (
  <svg {...base} {...p} width={p?.size || 15} height={p?.size || 15}>
    <path d="M9.5 4a2.5 2.5 0 0 0-2.45 3A2.5 2.5 0 0 0 5 9.5c0 .74.32 1.4.83 1.86A2.5 2.5 0 0 0 6.5 16c.34 0 .66-.07.95-.19A2.5 2.5 0 0 0 12 17.5V6.5A2.5 2.5 0 0 0 9.5 4Z" />
    <path d="M14.5 4a2.5 2.5 0 0 1 2.45 3A2.5 2.5 0 0 1 19 9.5c0 .74-.32 1.4-.83 1.86A2.5 2.5 0 0 1 17.5 16c-.34 0-.66-.07-.95-.19A2.5 2.5 0 0 1 12 17.5" />
  </svg>
)

export const IconTerminal = (p) => (
  <svg {...base} {...p} width={p?.size || 15} height={p?.size || 15}>
    <rect x="3" y="4.5" width="18" height="15" rx="2" />
    <path d="M7 9.5l3 3-3 3M12.5 15.5H17" />
  </svg>
)

export const IconFile = (p) => (
  <svg {...base} {...p} width={p?.size || 15} height={p?.size || 15}>
    <path d="M6 3h8l4 4v14H6z" />
    <path d="M14 3v4h4" />
  </svg>
)

export const IconFolder = (p) => (
  <svg {...base} {...p} width={p?.size || 15} height={p?.size || 15}>
    <path d="M3 6.5A2 2 0 0 1 5 4.5h4l2 2.5h8a2 2 0 0 1 2 2v8.5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
  </svg>
)

export const IconImage = (p) => (
  <svg {...base} {...p} width={p?.size || 15} height={p?.size || 15}>
    <rect x="3" y="4.5" width="18" height="15" rx="2" />
    <circle cx="8.5" cy="10" r="1.6" />
    <path d="M3.5 17l5-5 4 4 2.5-2.5 5.5 5.5" />
  </svg>
)

export const IconMemory = (p) => (
  <svg {...base} {...p} width={p?.size || 16} height={p?.size || 16}>
    <path d="M12 3.5 4 7.5v9l8 4 8-4v-9z" />
    <path d="M4 7.5l8 4 8-4M12 11.5v9" />
  </svg>
)

export const IconX = (p) => (
  <svg {...base} {...p} width={p?.size || 16} height={p?.size || 16}>
    <path d="M6 6l12 12M18 6L6 18" />
  </svg>
)

export const IconTrash = (p) => (
  <svg {...base} {...p} width={p?.size || 14} height={p?.size || 14}>
    <path d="M4 7h16M9 7V5h6v2M6.5 7l1 13h9l1-13" />
  </svg>
)

export const IconCheck = (p) => (
  <svg {...base} {...p} width={p?.size || 13} height={p?.size || 13}>
    <path d="M4.5 12.5l5 5 10-11" />
  </svg>
)

export const IconDot = (p) => (
  <svg {...base} {...p} width={p?.size || 10} height={p?.size || 10}>
    <circle cx="12" cy="12" r="4" fill="currentColor" stroke="none" />
  </svg>
)

export const IconRefresh = (p) => (
  <svg {...base} {...p} width={p?.size || 14} height={p?.size || 14}>
    <path d="M20 12a8 8 0 1 1-2.34-5.66M20 5v4h-4" />
  </svg>
)

export const IconDownload = (p) => (
  <svg {...base} {...p} width={p?.size || 15} height={p?.size || 15}>
    <path d="M12 4v11M7 10l5 5 5-5M5 20h14" />
  </svg>
)

export const IconCopy = (p) => (
  <svg {...base} {...p} width={p?.size || 14} height={p?.size || 14}>
    <rect x="9" y="9" width="11" height="11" rx="2" />
    <path d="M5 15V6a2 2 0 0 1 2-2h9" />
  </svg>
)

export const IconArrowDown = (p) => (
  <svg {...base} {...p} width={p?.size || 15} height={p?.size || 15}>
    <path d="M12 5v13M6 12l6 6 6-6" />
  </svg>
)

export const IconShield = (p) => (
  <svg {...base} {...p} width={p?.size || 13} height={p?.size || 13}>
    <path d="M12 3.5 5 6.5v5c0 4.2 2.9 7.4 7 8.9 4.1-1.5 7-4.7 7-8.9v-5z" />
  </svg>
)

export const IconSliders = (p) => (
  <svg {...base} {...p} width={p?.size || 15} height={p?.size || 15}>
    <path d="M4 8h10M18 8h2M4 16h4M12 16h8" />
    <circle cx="16" cy="8" r="2.2" />
    <circle cx="10" cy="16" r="2.2" />
  </svg>
)

export const IconGear = (p) => (
  <svg {...base} {...p} width={p?.size || 16} height={p?.size || 16}>
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
  </svg>
)

export const IconChat = (p) => (
  <svg {...base} {...p} width={p?.size || 13} height={p?.size || 13}>
    <path d="M20 13.5a2.5 2.5 0 0 1-2.5 2.5H8l-4 3.5V6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5z" />
  </svg>
)

export const IconPanelLeft = (p) => (
  <svg {...base} {...p} width={p?.size || 16} height={p?.size || 16}>
    <rect x="3.5" y="4.5" width="17" height="15" rx="2.5" />
    <path d="M10 4.5v15" />
  </svg>
)

export const IconSearch = (p) => (
  <svg {...base} {...p} width={p?.size || 14} height={p?.size || 14}>
    <circle cx="11" cy="11" r="6.5" />
    <path d="M20 20l-3.8-3.8" />
  </svg>
)

export const IconEdit = (p) => (
  <svg {...base} {...p} width={p?.size || 13} height={p?.size || 13}>
    <path d="M4 20h4l11-11a2.1 2.1 0 0 0-3-3L5 17z" />
    <path d="M13.5 7.5l3 3" />
  </svg>
)

export const IconEraser = (p) => (
  <svg {...base} {...p} width={p?.size || 15} height={p?.size || 15}>
    <path d="M4 20h16M7.5 17.5 4.6 14.6a2 2 0 0 1 0-2.8l7-7a2 2 0 0 1 2.8 0l4.6 4.6a2 2 0 0 1 0 2.8l-5.6 5.6a2 2 0 0 1-2.8 0Z" />
    <path d="M10.5 8.5l5 5" />
  </svg>
)

export const IconMic = (p) => (
  <svg {...base} {...p} width={p?.size || 15} height={p?.size || 15}>
    <rect x="9" y="3" width="6" height="11" rx="3" />
    <path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M8.5 21h7" />
  </svg>
)

export const IconVolume = (p) => (
  <svg {...base} {...p} width={p?.size || 15} height={p?.size || 15}>
    <path d="M4 9.5h3l4-3.5v12l-4-3.5H4z" />
    <path d="M14.5 9a4.5 4.5 0 0 1 0 6M17 6.5a8 8 0 0 1 0 11" />
  </svg>
)

export const IconVolumeOff = (p) => (
  <svg {...base} {...p} width={p?.size || 15} height={p?.size || 15}>
    <path d="M4 9.5h3l4-3.5v12l-4-3.5H4z" />
    <path d="M15 9.5l5 5M20 9.5l-5 5" />
  </svg>
)

export const TOOL_ICONS = {
  run_shell: IconTerminal,
  file_read: IconFile,
  file_create: IconFile,
  file_edit: IconFile,
  file_delete: IconTrash,
  file_search: IconFolder,
  file_move: IconFolder,
  file_copy: IconFolder,
  memory_write: IconMemory,
  memory_search: IconMemory,
  memory_forget: IconMemory,
}

export function toolIcon(name) {
  const C = TOOL_ICONS[name] || IconTerminal
  return <C size={15} />
}
