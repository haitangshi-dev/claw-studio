import React, { useEffect, useMemo, useState } from 'react'
import {
  Check,
  Eye,
  EyeOff,
  KeyRound,
  Plug,
  Plus,
  Server,
  Trash2,
  Zap,
} from 'lucide-react'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Field,
  IconButton,
  Input,
  Row,
  Section,
  Select,
  Spinner,
  StatusDot,
} from '../../ui'
import ModelPicker from '../../components/ModelPicker'

const ROLES = [
  { key: 'smart', label: '主力模型', hint: '日常对话与工具调用' },
  { key: 'fast', label: '快速模型', hint: '摘要、分类、标题这类小事' },
  { key: 'vision', label: '视觉模型', hint: '图片理解' },
  { key: 'compact', label: '压缩模型', hint: '上下文压缩专家' },
  { key: 'embedding', label: '向量模型', hint: '记忆检索用，可留空' },
]

const EMPTY = {
  id: '',
  name: '',
  baseUrl: 'https://api.deepseek.com/v1',
  apiKey: '',
  model: '',
  models: [],
  timeout: 120,
  proxy: '',
}

const uid = () => `p_${Math.random().toString(36).slice(2, 9)}`

export default function Providers({ settings, set, online }) {
  const [editing, setEditing] = useState(null)
  const [showKey, setShowKey] = useState(false)
  // 必须在 allModels 的 useMemo 之前声明：依赖数组在渲染时就求值，晚声明会 TDZ 崩页

  const providers = settings.providers || []
  // 角色绑定的值必须是后端认识的模型 key：api:<pid>/<model>,
  // 写成 <pid>:<model> 时后端查不到供应商，表现为「绑了但对话还是走原模型」。
  const allModels = useMemo(() => {
    const own = providers.flatMap((p) =>
      (p.models?.length ? p.models : p.model ? [p.model] : []).map((m) => ({
        value: `api:${p.id}/${m}`,
        label: `${p.name} · ${m}`,
      })),
    )
    return own
  }, [providers])

  const startAdd = () => {
    setEditing({ ...EMPTY, id: uid() })
    setShowKey(false)
  }

  const startEdit = (p) => {
    setEditing({ ...EMPTY, ...p })
    setShowKey(false)
  }

  const save = () => {
    if (!editing?.name?.trim() || !editing?.baseUrl?.trim()) return
    const next = [...providers]
    const i = next.findIndex((p) => p.id === editing.id)
    const payload = { ...editing, name: editing.name.trim(), baseUrl: editing.baseUrl.trim() }
    if (i >= 0) next[i] = payload
    else next.push(payload)
    set('providers', next)
    setEditing(null)
  }

  const remove = (id) => {
    set('providers', providers.filter((p) => p.id !== id))
    if (editing?.id === id) setEditing(null)
  }

  // 自动抓取：ModelPicker 填完端点/Key 后自己调，不需要用户点「测试连接」
  const probeModels = async (payload) => {
    try {
      const r = await fetch('/api/providers/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      return r.ok ? r.json() : { ok: false, error: `HTTP ${r.status}` }
    } catch (e) {
      return { ok: false, error: `连接失败（${e.name || 'Error'}）` }
    }
  }

  return (
    <>
      <Section
        title="推理供应商"
        desc="所有推理都走 OpenAI 兼容接口。密钥用 Windows DPAPI 加密后存放在本机，不会写进配置文件，也不进版本库。"
        action={
          <div className="flex items-center gap-2">
            <Badge tone={online ? 'success' : 'warning'}>
              {online ? '已连接后端' : '离线预览'}
            </Badge>
            <Button size="sm" variant="primary" onClick={startAdd}>
              <Plus size={13} /> 新增
            </Button>
          </div>
        }
      >
        {providers.length === 0 && !editing ? (
          <EmptyState
            icon={<Server size={22} />}
            title="还没有配置任何供应商"
            desc="填入一个 OpenAI 兼容端点就能开始对话：DeepSeek、OpenRouter、自建网关都可以。"
            action={
              <Button size="sm" variant="primary" onClick={startAdd}>
                <Plus size={13} /> 添加第一个供应商
              </Button>
            }
          />
        ) : (
          <Card>
            {providers.map((p) => (
              <Row
                key={p.id}
                label={
                  <span className="flex items-center gap-2">
                    <StatusDot tone={p.apiKey ? 'success' : 'warning'} />
                    {p.name}
                    {String(settings.roles?.smart || "").startsWith(`api:${p.id}/`) && (
                      <Badge tone="primary">主力</Badge>
                    )}
                  </span>
                }
                hint={`${p.baseUrl}${p.model ? ` · ${p.model}` : ''}`}
              >
                <Button size="sm" variant="ghost" onClick={() => startEdit(p)}>
                  编辑
                </Button>
                <IconButton label="删除供应商" onClick={() => remove(p.id)}>
                  <Trash2 size={13} />
                </IconButton>
              </Row>
            ))}
          </Card>
        )}

        {editing && (
          <Card className="anim-in mt-3 p-4">
            <div className="mb-4 flex items-center gap-2">
              <Plug size={14} className="text-primary" />
              <p className="m-0 text-[13px] font-medium">
                {providers.some((p) => p.id === editing.id) ? '编辑供应商' : '新增供应商'}
              </p>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <Field label="名称">
                <Input
                  placeholder="例如 DeepSeek"
                  value={editing.name}
                  onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                />
              </Field>
              <Field label="Base URL" hint="要带 /v1 后缀">
                <Input
                  placeholder="https://api.deepseek.com/v1"
                  value={editing.baseUrl}
                  onChange={(e) => setEditing({ ...editing, baseUrl: e.target.value })}
                />
              </Field>
            </div>

            <Field label="API Key" className="mt-3" hint="留空表示沿用已保存的密钥">
              <div className="relative">
                <KeyRound
                  size={13}
                  className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-faint"
                />
                <Input
                  type={showKey ? 'text' : 'password'}
                  className="pl-8 pr-9 font-mono"
                  placeholder="sk-..."
                  value={editing.apiKey || ''}
                  onChange={(e) => setEditing({ ...editing, apiKey: e.target.value })}
                />
                <button
                  type="button"
                  aria-label={showKey ? '隐藏密钥' : '显示密钥'}
                  onClick={() => setShowKey((v) => !v)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-faint hover:text-ink"
                >
                  {showKey ? <EyeOff size={13} /> : <Eye size={13} />}
                </button>
              </div>
            </Field>

            <div className="mt-3 grid grid-cols-2 gap-3">
              <Field label="默认模型" hint="填好端点与密钥后自动识别，也可手动输入">
                <ModelPicker
                  value={editing.model}
                  models={editing.models || []}
                  baseUrl={editing.baseUrl}
                  apiKey={editing.apiKey}
                  providerName={editing.name || 'API'}
                  probe={probeModels}
                  onChange={(m, ms) => setEditing((e) => ({ ...e, model: m, models: ms }))}
                />
              </Field>
              <Field label="超时（秒）">
                <Input
                  type="number"
                  value={editing.timeout}
                  onChange={(e) => setEditing({ ...editing, timeout: Number(e.target.value) })}
                />
              </Field>
            </div>

            <div className="mt-4 flex items-center gap-2 border-t border-line pt-3">
              <span className="text-[11.5px] text-faint">
                模型列表在填好端点与密钥后自动识别；识别不到也能手动填，保存时会自动再试一次。
              </span>
              <div className="ml-auto flex gap-2">
                <Button size="sm" variant="ghost" onClick={() => setEditing(null)}>
                  取消
                </Button>
                <Button
                  size="sm"
                  variant="primary"
                  onClick={save}
                  disabled={!editing.name?.trim() || !editing.baseUrl?.trim()}
                >
                  保存
                </Button>
              </div>
            </div>
          </Card>
        )}
      </Section>

      <Section title="角色绑定" desc="不同任务用不同模型，省钱也能把好钢用在刀刃上。">
        <Card>
          {ROLES.map((r) => (
            <Row key={r.key} label={r.label} hint={r.hint}>
              <Select
                className="w-[260px]"
                value={settings.roles?.[r.key] || ''}
                onChange={(e) => set('roles', { ...settings.roles, [r.key]: e.target.value })}
              >
                <option value="">未绑定</option>
                {allModels.map((m) => (
                  <option key={m.value} value={m.value}>
                    {m.label}
                  </option>
                ))}
              </Select>
            </Row>
          ))}
        </Card>
      </Section>
    </>
  )
}
