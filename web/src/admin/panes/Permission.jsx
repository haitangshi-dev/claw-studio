import React from 'react'
import { Ban, CircleHelp, ShieldCheck, ShieldOff } from 'lucide-react'
import { Badge, Card, Row, Section, Segmented, Toggle } from '../../ui'

const TIERS = [
  {
    key: 'readonly',
    label: '只读',
    icon: Ban,
    desc: '不写文件、不改系统',
    detail: '写类工具直接拒绝，命令只允许只读白名单。拒绝会在对话里显示红卡，不会悄悄失败。',
  },
  {
    key: 'ask',
    label: '询问',
    icon: CircleHelp,
    desc: '每次有副作用都先问你',
    detail: '写文件、改文件、删文件、跑命令、发消息，全部弹卡确认。授权可只给一次，也可给整个会话。',
    recommended: true,
  },
  {
    key: 'standard',
    label: '标准',
    icon: ShieldCheck,
    desc: '工作目录内放行',
    detail: '工作目录内读写不问；删文件、越界路径、危险命令、安装插件仍然弹卡。',
  },
  {
    key: 'full',
    label: '完全',
    icon: ShieldOff,
    desc: '不询问，直接执行',
    detail: '不做路径限制，不拦危险命令。所有操作照常写审计日志。只建议在隔离环境里用。',
  },
]

export default function Permission({ settings, set }) {
  const p = settings.permission
  return (
    <>
      <Section
        title="权限档位"
        desc="决定模型能对你电脑做什么。默认「询问」：每一步有副作用的操作，你点头之后才落地。"
      >
        <div className="grid grid-cols-2 gap-3">
          {TIERS.map((t) => {
            const Icon = t.icon
            const active = p.onDuty === t.key
            return (
              <button
                key={t.key}
                onClick={() => set('permission.onDuty', t.key)}
                className="rounded-lg border bg-surface p-3.5 text-left transition-colors duration-150"
                style={{
                  borderColor: active ? 'var(--primary)' : 'var(--border)',
                  background: active ? 'var(--primary-soft)' : 'var(--surface)',
                }}
              >
                <div className="mb-1.5 flex items-center gap-2">
                  <Icon size={14} style={{ color: active ? 'var(--primary)' : 'var(--text-muted)' }} />
                  <span className="text-[13px] font-medium">{t.label}</span>
                  {t.recommended && <Badge tone="primary">推荐</Badge>}
                </div>
                <p className="m-0 text-[12px] text-muted">{t.desc}</p>
                <p className="m-0 mt-1.5 text-[11.5px] leading-relaxed text-faint">{t.detail}</p>
              </button>
            )
          })}
        </div>
      </Section>

      <Section title="审批细节" desc="卡片的粒度与记忆范围。">
        <Card>
          <Row
            label="危险操作必须确认"
            hint="删除、覆盖已有文件、终止进程这类不可逆操作，即使选了「标准」也会拦下来问一次"
          >
            <Toggle
              checked={p.confirmDestructive}
              onChange={(v) => set('permission.confirmDestructive', v)}
            />
          </Row>
          <Row
            label="「本会话允许此类」的范围"
            hint="记住的最小粒度：同工具 + 同目录前缀。换目录或换工具会重新问"
          >
            <Segmented
              size="sm"
              value={p.scope || 'tool_dir'}
              onChange={(v) => set('permission.scope', v)}
              options={[
                { value: 'once', label: '只这次' },
                { value: 'tool_dir', label: '同工具同目录' },
                { value: 'session', label: '整个会话' },
              ]}
            />
          </Row>
          <Row label="确认超时" hint="超过这个时间没人点，按「拒绝」处理，并把原因告诉模型让它换方案">
            <Segmented
              size="sm"
              value={p.timeout || 120}
              onChange={(v) => set('permission.timeout', v)}
              options={[
                { value: 60, label: '60 秒' },
                { value: 120, label: '2 分钟' },
                { value: 300, label: '5 分钟' },
              ]}
            />
          </Row>
        </Card>
      </Section>

      <Section title="无人值守策略" desc="你不在场时没人能点确认，所以按预设走，结束后在汇报里列出拒了什么。">
        <Card>
          <Row label="策略" hint="勾选「无人值守」时自动生效，不影响值守模式">
            <Segmented
              size="sm"
              value={p.unattendedPolicy}
              onChange={(v) => set('permission.unattendedPolicy', v)}
              options={[
                { value: 'safe', label: '保守：高危拒绝' },
                { value: 'normal', label: '常规：越界才拒' },
                { value: 'loose', label: '宽松：只拦不可逆' },
              ]}
            />
          </Row>
          <Row label="结束后汇报被拒操作" hint="收尾消息里附一段清单：哪一步被拦、原因、建议怎么做">
            <Badge tone="success">始终开启</Badge>
          </Row>
        </Card>
      </Section>

      <Section title="审计" desc="所有工具调用都要留痕，方便事后追责与排查。">
        <Card>
          <Row label="记录到 logs/tool-audit.jsonl" hint="含工具名、参数摘要、耗时、成败与拒绝原因；自动滚动，不占空间">
            <Badge tone="success">已启用</Badge>
          </Row>
          <Row label="在界面显示参数全文" hint="默认只显示摘要，敏感内容（密钥、长文本）不落屏">
            <Toggle checked={p.showFullArgs || false} onChange={(v) => set('permission.showFullArgs', v)} />
          </Row>
        </Card>
      </Section>
    </>
  )
}
