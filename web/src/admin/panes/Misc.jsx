import React from 'react'
import { Brain, CalendarClock, GitBranch, MessageCircle, Plus, ScrollText } from 'lucide-react'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Row,
  Section,
  Segmented,
  Select,
  Slider,
  Toggle,
} from '../../ui'

export function MemoryPane({ settings, set }) {
  const m = settings.memory
  return (
    <>
      <Section
        title="写入策略"
        desc="记忆分四层：核心事实常驻、经验按需检索、情景留档、会话记录随任务产生。"
      >
        <Card>
          <Row
            label="自动抽取记忆"
            hint="每轮任务结束后，用便宜模型从对话里提取 0–3 条你明确说过的偏好或决定，并标注来源"
          >
            <Toggle checked={m.autoExtract} onChange={(v) => set('memory.autoExtract', v)} />
          </Row>
          <Row
            label="自动巩固"
            hint="同层里重复的碎片达到三条以上时，合成一条原则，原条目标记合并、可回溯"
          >
            <Toggle checked={m.autoConsolidate} onChange={(v) => set('memory.autoConsolidate', v)} />
          </Row>
          <Row label="冲突处理" hint="同一主题出现相反结论时，保留旧条目并标记被取代，不静默覆盖">
            <Badge tone="primary">保留历史</Badge>
          </Row>
        </Card>
      </Section>

      <Section title="注入预算" desc="常驻记忆占上下文的比例。超预算时自动把最久未使用的条目降级到可检索层。">
        <Card>
          <Row label="预算上限" hint="按模型上下文的百分比动态计算，另有 1200 字下限">
            <Slider
              value={m.budgetRatio}
              min={2}
              max={15}
              onChange={(v) => set('memory.budgetRatio', v)}
              suffix="%"
            />
          </Row>
          <Row label="检索条数" hint="每次注入多少条经验记忆与情景记忆">
            <Segmented
              size="sm"
              value={m.topK || 4}
              onChange={(v) => set('memory.topK', v)}
              options={[
                { value: 2, label: '2 条' },
                { value: 4, label: '4 条' },
                { value: 6, label: '6 条' },
              ]}
            />
          </Row>
        </Card>
      </Section>

      <Section title="遗忘" desc="长期不用才会淡出，重要内容不会因为一时没用就被删掉。">
        <Card>
          <Row label="遗忘前需要我确认" hint="系统只给候选清单，勾选后才降级或归档">
            <Toggle checked={m.forgetConfirm} onChange={(v) => set('memory.forgetConfirm', v)} />
          </Row>
          <Row label="半衰期" hint="强度越高，衰减越慢；每次真正被用到时强度会回升">
            <Select
              className="w-[140px]"
              value={m.halfLife || 30}
              onChange={(e) => set('memory.halfLife', Number(e.target.value))}
            >
              <option value={30}>30 天</option>
              <option value={60}>60 天</option>
              <option value={90}>90 天</option>
              <option value={180}>180 天</option>
            </Select>
          </Row>
        </Card>
      </Section>
    </>
  )
}

export function NotifyPane({ settings, set }) {
  const n = settings.notify
  return (
    <Section title="完成通知" desc="长任务跑完时提醒你，短任务不打扰。">
      <Card>
        <Row label="系统通知" hint="任务耗时超过阈值时弹一条系统提示">
          <Toggle checked={n.system} onChange={(v) => set('notify.system', v)} />
        </Row>
        <Row label="最短耗时" hint="低于这个时长不发通知">
          <Segmented
            size="sm"
            value={n.minSeconds}
            onChange={(v) => set('notify.minSeconds', v)}
            options={[
              { value: 0, label: '每次' },
              { value: 30, label: '30 秒' },
              { value: 60, label: '60 秒' },
              { value: 120, label: '2 分钟' },
            ]}
          />
        </Row>
        <Row label="微信推送" hint="任务完成时同步发一条到微信，需要先绑定">
          <Toggle checked={n.wechat} onChange={(v) => set('notify.wechat', v)} />
        </Row>
      </Card>
    </Section>
  )
}

export function WeChatPane({ settings, set }) {
  const w = settings.wechat
  return (
    <>
      <Section
        title="微信通道"
        desc="内置的对话入口：在微信里给机器人发消息，它就是同一个 Agent、同一份记忆。"
        action={<Badge tone="warning">未绑定</Badge>}
      >
        <Card>
          <Row label="启用微信通道" hint="启用后桥接进程随应用启动，收消息、回消息都在本机完成">
            <Toggle checked={w.enabled} onChange={(v) => set('wechat.enabled', v)} />
          </Row>
          <Row label="绑定状态" hint="扫码登录后显示绑定的微信账号，凭证加密存在本机">
            <Button size="sm">扫码绑定</Button>
          </Row>
          <Row label="回复风格" hint="微信不渲染 Markdown，回复会自动压成纯文本并限制长度">
            <Select className="w-[150px]" value={w.style || 'plain'} onChange={(e) => set('wechat.style', e.target.value)}>
              <option value="plain">纯文本</option>
              <option value="brief">精简成三句</option>
            </Select>
          </Row>
        </Card>
      </Section>

      <Section
        title="排队与补发"
        desc="官方通道只能在会话窗口内回复。窗口过期时不硬发、不丢消息，排队等你下次说话时合并补发。"
      >
        <Card>
          <Row label="无窗口时排队" hint="最多保留最近 20 条，超出的按任务合并成一条摘要">
            <Badge tone="success">已启用</Badge>
          </Row>
          <Row label="补发策略" hint="用户下次发消息时，先补一条「你不在时完成了这些事」">
            <Segmented
              size="sm"
              value={w.replay || 'digest'}
              onChange={(v) => set('wechat.replay', v)}
              options={[
                { value: 'each', label: '逐条补' },
                { value: 'digest', label: '合并摘要' },
                { value: 'drop', label: '不补' },
              ]}
            />
          </Row>
          <Row label="接收图片与文件" hint="收到的图片会走视觉模型理解，文件暂存到工作目录">
            <Toggle checked={w.media || false} onChange={(v) => set('wechat.media', v)} />
          </Row>
        </Card>
      </Section>
    </>
  )
}

export function AutomationPane({ settings, set }) {
  const a = settings.automation || {}
  return (
    <>
      <Section
        title="定时任务"
        desc="按时间自动跑一轮任务，完成后唤醒模型做汇报，也可以通过通知把结果推给你。"
        action={
          <Button size="sm" variant="primary">
            <Plus size={13} /> 新建任务
          </Button>
        }
      >
        <EmptyState
          icon={<CalendarClock size={22} />}
          title="还没有定时任务"
          desc="例如：每天早上八点整理昨天的会话要点；每周五检查依赖更新并给出升级建议。"
        />
      </Section>

      <Section title="运行策略" desc="没人看着的时候怎么跑。">
        <Card>
          <Row label="错过运行时间" hint="应用没开着时错过的任务，启动后是否补跑一次">
            <Segmented
              size="sm"
              value={a.missed || 'once'}
              onChange={(v) => set('automation.missed', v)}
              options={[
                { value: 'once', label: '补一次' },
                { value: 'skip', label: '跳过' },
              ]}
            />
          </Row>
          <Row label="完成后唤醒模型" hint="任务结束把结果作为一条事件写进会话，并让模型自己判断要不要继续处理">
            <Toggle checked={a.wakeModel !== false} onChange={(v) => set('automation.wakeModel', v)} />
          </Row>
          <Row label="保留运行历史" hint="每次运行的计划、工具调用、产出都留档，可回看">
            <Select className="w-[140px]" value={a.keep || 50} onChange={(e) => set('automation.keep', Number(e.target.value))}>
              <option value={20}>最近 20 次</option>
              <option value={50}>最近 50 次</option>
              <option value={200}>最近 200 次</option>
            </Select>
          </Row>
        </Card>
      </Section>
    </>
  )
}

export function AboutPane() {
  return (
    <>
      <Section title="关于" desc="Claw Studio —— 跑在本机的桌面智能体。">
        <Card>
          <Row label="版本" hint="构建于 2026-11-19">
            <span className="font-mono text-[12px] text-muted">2026.11.19-dev</span>
          </Row>
          <Row label="运行时" hint="Python + FastAPI，窗口由系统 WebView2 承载">
            <span className="font-mono text-[12px] text-muted">pywebview · chromium</span>
          </Row>
          <Row label="开源许可" hint="可以自由使用、修改、分发">
            <Badge tone="neutral">MIT</Badge>
          </Row>
        </Card>
      </Section>

      <Section title="资源">
        <Card>
          <Row label="源代码仓库" hint="问题与建议走 issue">
            <Button size="sm" variant="ghost">
              <GitBranch size={13} /> GitHub
            </Button>
          </Row>
          <Row label="使用文档" hint="工具清单、权限模型、记忆机制">
            <Button size="sm" variant="ghost">
              <ScrollText size={13} /> 文档
            </Button>
          </Row>
          <Row label="反馈" hint="出问题时附上 logs/ 下的日志会快很多">
            <Button size="sm" variant="ghost">
              <MessageCircle size={13} /> 联系
            </Button>
          </Row>
        </Card>
      </Section>
    </>
  )
}
