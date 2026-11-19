// 后端接口封装：模型 / 记忆 / SSE 聊天流

export async function getModels() {
  const r = await fetch('/api/models')
  return r.json()
}

export async function switchModel(key) {
  const r = await fetch('/api/models/switch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key }),
  })
  return r.json()
}

export async function getMemory(limit = 500, workspace = '') {
  const ws = workspace ? `&workspace=${encodeURIComponent(workspace)}` : ''
  const r = await fetch(`/api/memory?limit=${limit}${ws}`)
  return r.json()
}

// 标签设置：覆盖 / 追加 / 移除（数量不限，后端只在过多时给一句提醒）
export async function setMemoryTags(id, { tags, add, remove } = {}) {
  const r = await fetch(`/api/memory/${id}/tags`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tags: tags ?? '', add: add ?? '', remove: remove ?? '' }),
  })
  return r.json()
}

// 局部更新一条记忆（标题 / 正文 / 结论 / 标签 / 重要度）
export async function patchMemory(id, patch) {
  const r = await fetch(`/api/memory/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  return r.json()
}

export async function getMemoryTags() {
  const r = await fetch('/api/memory/tags')
  return r.json()
}

// 遗忘机制：先算候选（只提议），确认后再降级
export async function getForgetCandidates(minAgeDays) {
  const q = minAgeDays != null ? `?min_age_days=${minAgeDays}` : ''
  const r = await fetch(`/api/memory/forget_candidates${q}`)
  return r.json()
}

export async function demoteMemory(ids) {
  const r = await fetch('/api/memory/demote', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids: Array.isArray(ids) ? ids.join(',') : String(ids) }),
  })
  return r.json()
}

export async function pinMemory(id, on = true) {
  const r = await fetch(`/api/memory/${id}/pin`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ on }),
  })
  return r.json()
}

export async function getStatus() {
  const r = await fetch('/api/status')
  return r.json()
}

// 模型加载参数（ctx / KV 量化 / ngl / flash_attn；保存后自动重启模型生效）
export async function getLoadParams(model) {
  const r = await fetch(`/api/models/load_params?model=${encodeURIComponent(model)}`)
  return r.json()
}

export async function setLoadParams(model, params) {
  const r = await fetch('/api/models/load_params', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model, params }),
  })
  return r.json()
}

// 专家模型（压缩 / 图片识别）
export async function getExperts() {
  const r = await fetch('/api/experts')
  return r.json()
}

export async function setExperts(patch) {
  const r = await fetch('/api/experts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  return r.json()
}

/**
 * 发送对话请求，消费 SSE 流。
 * onEvent(ev) 处理 {type: reasoning|content|tool_call|tool_result|step|done|error, ...}
 * 返回一个 abort 函数。
 */
export function streamChat(payload, onEvent, onClose) {
  const ctrl = new AbortController()
  ;(async () => {
    try {
      const r = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: ctrl.signal,
      })
      if (!r.ok || !r.body) {
        onEvent({ type: 'error', message: `HTTP ${r.status}` })
        return
      }
      const reader = r.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let buf = ''
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        let idx
        while ((idx = buf.indexOf('\n\n')) >= 0) {
          const block = buf.slice(0, idx)
          buf = buf.slice(idx + 2)
          for (const line of block.split('\n')) {
            if (!line.startsWith('data: ')) continue
            try {
              onEvent(JSON.parse(line.slice(6)))
            } catch (e) {
              /* 忽略解析失败的单行 */
            }
          }
        }
      }
    } catch (e) {
      if (e.name !== 'AbortError') onEvent({ type: 'error', message: String(e) })
    } finally {
      if (onClose) onClose()
    }
  })()
  return () => ctrl.abort()
}

// 技能库（模型可创建/管理）
export async function getSkills() {
  const r = await fetch('/api/skills')
  return r.json()
}

export async function getSkillDetail(name) {
  const r = await fetch(`/api/skills/${encodeURIComponent(name)}`)
  return r.json()
}

export async function deleteSkill(name) {
  const r = await fetch(`/api/skills/${encodeURIComponent(name)}`, { method: 'DELETE' })
  return r.json()
}

// Qoder CLI 接入状态（可用/已登录/模型数/用量）
export async function getQoderStatus() {
  const r = await fetch('/api/qoder/status')
  return r.json()
}

// 额度（积分）管理：按接入方各注册一个 provider，前端按 provider 渲染多套额度设置
export async function getQuotaProviders() {
  const r = await fetch('/api/quota')
  return r.json()
}

export async function getProviderQuota(id, refresh = false) {
  const r = await fetch(`/api/quota/${encodeURIComponent(id)}${refresh ? '?refresh=1' : ''}`)
  return r.json()
}

// 插件（一切皆插件）
export async function getPlugins() {
  const r = await fetch('/api/plugins')
  return r.json()
}

export async function reloadPlugins(name) {
  const r = await fetch('/api/plugins/reload', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(name ? { name } : {}),
  })
  return r.json()
}

export async function togglePlugin(name, enabled) {
  const r = await fetch('/api/plugins/toggle', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, enabled }),
  })
  return r.json()
}

export async function deletePlugin(name) {
  const r = await fetch(`/api/plugins/${encodeURIComponent(name)}`, { method: 'DELETE' })
  return r.json()
}

// 框架自更新
export async function getUpdateInfo() {
  const r = await fetch('/api/update/info')
  return r.json()
}

export async function checkUpdate() {
  const r = await fetch('/api/update/check')
  return r.json()
}

export async function applyUpdate() {
  const r = await fetch('/api/update/apply', { method: 'POST' })
  return r.json()
}

// 完成通知设置
export async function getNotify() {
  const r = await fetch('/api/notify')
  return r.json()
}

export async function setNotify(patch) {
  const r = await fetch('/api/notify', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  return r.json()
}

// 打开本地目录（资源管理器）
export async function openPath(target, path) {
  const r = await fetch('/api/open_path', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target, path }),
  })
  return r.json()
}

// ---- 工作目录（最近目录 / 原生资源管理器选择框）----
export async function getWorkdirs() {
  const r = await fetch('/api/workdirs')
  return r.json()
}

export async function registerWorkdir(path) {
  const r = await fetch('/api/workdirs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  })
  return r.json()
}

// 后端弹出 Windows 资源管理器式目录选择框（阻塞到用户确认/取消）
export async function pickFolder(initial) {
  const r = await fetch('/api/fs/pick_folder', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ initial }),
  })
  return r.json()
}

export async function promoteMemory(id) {
  const r = await fetch(`/api/memory/${id}/promote`, { method: 'POST' })
  return r.json()
}

// ---- 界面内提问（ask_user 卡片）----
// 后端在需要用户拍板时写一条 pending 提问；主界面轮询它、浮出卡片，用户点选后回传。
export async function getPendingAsk() {
  const r = await fetch('/api/ask/pending')
  return r.json()
}

export async function answerAsk(id, answer) {
  const r = await fetch('/api/ask/answer', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, answer }),
  })
  return r.json()
}

export async function skipAsk(id) {
  const r = await fetch('/api/ask/skip', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id }),
  })
  return r.json()
}

// ---- 桌面宠物（Claw 宠物：启动 / 停止 / 随应用启动）----
export async function getPet() {
  const r = await fetch('/api/pet')
  return r.json()
}

export async function startPet() {
  const r = await fetch('/api/pet/start', { method: 'POST' })
  return r.json()
}

export async function stopPet() {
  const r = await fetch('/api/pet/stop', { method: 'POST' })
  return r.json()
}

export async function setPetAuto(autoStart) {
  const r = await fetch('/api/pet/auto', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ auto_start: autoStart }),
  })
  return r.json()
}

// ---- 微信桥接（微信直连 Claw Agent：启动 / 停止 / 随应用启动）----
export async function getWechat() {
  const r = await fetch('/api/wechat')
  return r.json()
}

export async function startWechat() {
  const r = await fetch('/api/wechat/start', { method: 'POST' })
  return r.json()
}

export async function stopWechat() {
  const r = await fetch('/api/wechat/stop', { method: 'POST' })
  return r.json()
}

export async function setWechatAuto(autoStart) {
  const r = await fetch('/api/wechat/auto', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ auto_start: autoStart }),
  })
  return r.json()
}

// ---- 语音：本地识别 + Edge TTS 朗读 ----
export async function getVoice() {
  const r = await fetch('/api/voice')
  return r.json()
}

export async function saveVoice(patch) {
  const r = await fetch('/api/voice/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch || {}),
  })
  return r.json()
}

export async function fetchTts(text, { voice, rate, volume } = {}) {
  const r = await fetch('/api/tts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, voice, rate, volume }),
  })
  if (!r.ok) {
    let msg = '朗读失败'
    try {
      const j = await r.json()
      if (j && j.error) msg = j.error
    } catch {
      /* 非 JSON 错误 */
    }
    throw new Error(msg)
  }
  return r.blob()
}

// ---------- 工具审批闸门 ----------
export async function getApprovals() {
  try {
    const r = await fetch('/api/approval/pending')
    if (!r.ok) return { ok: false, items: [] }
    return await r.json()
  } catch (e) {
    return { ok: false, items: [] }
  }
}

export async function answerApproval(id, action, reason = '') {
  const r = await fetch('/api/approval/answer', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, action, reason }),
  })
  return r.json()
}
