// 办公室房间与员工名册（按会话绑定；最终都只在主聊天里呈现）
export const OFFICE_ROOMS = [
  {
    id: 'dev',
    name: '项目开发部',
    color: '#D9713A',
    staff: ['项目主管', '前端开发员', '全栈设计师', '后端开发', '美术设计'],
  },
  {
    id: 'life',
    name: '生活部',
    color: '#2C7F9E',
    staff: ['生活主管', '生活助理', '健康顾问'],
  },
  {
    id: 'memory',
    name: '记忆管理部',
    color: '#6A5A8A',
    staff: ['记忆主管', '记忆整理员'],
  },
]

export const findRoom = (id) => OFFICE_ROOMS.find((r) => r.id === id) || OFFICE_ROOMS[0]

// 群聊视图用：按「部门 id 或部门名」反查部门，查不到返回 null（不能回退成第一个部门）
export const roomOf = (idOrName) =>
  OFFICE_ROOMS.find((r) => r.id === idOrName || r.name === idOrName) || null

export const roomNameOf = (idOrName, fallback = '') => roomOf(idOrName)?.name || fallback

export const roomColorOf = (idOrName, fallback = '#D9713A') => roomOf(idOrName)?.color || fallback

// 群成员名册：所有部门的所有员工，扁平化后供群聊侧栏与筛选使用
export const ALL_STAFF = OFFICE_ROOMS.flatMap((r) =>
  r.staff.map((name) => ({ name, room: r.name, roomId: r.id, color: r.color }))
)

export const roomOfStaff = (name) => ALL_STAFF.find((s) => s.name === name) || null
