import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  Activity,
  ArrowRight,
  Bot,
  Check,
  CheckCircle2,
  ChevronDown,
  CircleDashed,
  Database,
  FileJson2,
  Globe2,
  Inbox,
  LoaderCircle,
  MessageCircle,
  Network,
  Pause,
  Play,
  Power,
  RefreshCw,
  RadioTower,
  RotateCcw,
  Search,
  Send,
  Server,
  ShieldCheck,
  Sparkles,
  Trash2,
  TriangleAlert,
  Unplug,
  Wifi,
  WifiOff,
  Zap,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

type RunInfo = {
  run_id: string
  events: number
  packet_events?: number
  size_bytes: number
  mtime: number
}

type EventRecord = {
  seq?: number
  ts?: string
  run_id?: string
  phase?: string
  event?: string
  nonce?: string
  from?: string
  to?: string
  target?: string
  subject?: string
  body?: string
  status?: string
  retry_count?: number
  next_retry_at?: number | null
  narrative?: string
  [key: string]: unknown
}

type ScenarioState = {
  status: string
  run_id: string | null
  step: string
  step_index: number
  step_total: number
  message: string
  started_at: string | null
  ended_at: string | null
  error: string | null
}

type QueueCount = { total: number; pending: number; delivered: number }

type PacketSignal = {
  pseq: number
  ts?: string
  sensor?: string
  evidence: string
  scope?: "cross_domain" | "edge_agent"
  src?: string
  dst?: string
  src_node?: string
  dst_node?: string
  protocol?: string
  qname?: string
  qtype?: string
  bytes?: number
}

type PacketSummary = {
  types_seen: string[]
  cross_domain_types_seen: string[]
  edge_types_seen: string[]
  queries: string[]
  agent_edge_observed: boolean
  dns_observed: boolean
  svcb_observed: boolean
  tcp_observed: boolean
  tls_observed: boolean
  ats_lookup_observed: boolean
  atk_lookup_observed: boolean
  encrypted_data_observed: boolean
}

type PacketCaptureState = {
  run_id: string
  status: string
  source: string
  primary_evidence: boolean
  count: number
  sensors: Record<string, { status: string; error?: string }>
  started_at?: string | null
  last_packet_at?: string | null
  summary: PacketSummary
  signals: PacketSignal[]
}

type TopologyState = {
  containers: Record<string, string>
  queue_counts: Record<string, QueueCount>
  scenario: ScenarioState
  chat?: ChatState
  packet_capture?: PacketCaptureState | null
  ts: string
}

type ChatMessage = {
  id: string
  request_id: string
  role: "user" | "assistant"
  agent_id: string
  content: string
  status: string
  created_at: string
  updated_at: string
}

type ChatActivity = {
  id: string
  request_id: string
  tool: string
  status: string
  args: Record<string, unknown>
  started_at: string
  ended_at: string | null
}

type AgentAuditEvent = {
  id: string
  run_id: string
  agent_id: string
  role: "travel" | "search" | "rates" | "bill"
  kind: "user_input" | "atp_input" | "tool_input" | "tool_output" | "response"
  title: string
  data: unknown
  request_id?: string | null
  ts: string
}

type ChatState = {
  session_id: string
  status: string
  run_id: string | null
  active_request_id: string | null
  messages: ChatMessage[]
  activities: ChatActivity[]
  agent_audit: AgentAuditEvent[]
  runtime: { name: string; version: string; model: string; status: string }
  error: string | null
  updated_at: string | null
}

const EMPTY_CHAT: ChatState = {
  session_id: "",
  status: "idle",
  run_id: null,
  active_request_id: null,
  messages: [],
  activities: [],
  agent_audit: [],
  runtime: { name: "Agent", version: "", model: "", status: "stopped" },
  error: null,
  updated_at: null,
}

type LiveState = "offline" | "connecting" | "live" | "retrying"

type ProtocolStage = {
  name: string
  value: string
  state: "pass" | "fail" | "pending" | "info" | "skip"
}

const EMPTY_SCENARIO: ScenarioState = {
  status: "idle",
  run_id: null,
  step: "idle",
  step_index: 0,
  step_total: 7,
  message: "就绪",
  started_at: null,
  ended_at: null,
  error: null,
}

const NODE_POINTS: Record<string, [number, number]> = {
  "travel@family.test": [165, 326],
  "search@hotel.test": [408, 326],
  "rates@hotel.test": [548, 326],
  "bill@payment.test": [775, 326],
  "server-family": [165, 208],
  "server-hotel": [470, 208],
  "server-payment": [775, 208],
  dns: [470, 53],
}

const PHASE_STYLES: Record<string, string> = {
  setup: "border-slate-500/20 bg-slate-500/10 text-slate-600 dark:text-slate-300",
  search: "border-blue-500/20 bg-blue-500/10 text-blue-700 dark:text-blue-300",
  subscribe: "border-violet-500/20 bg-violet-500/10 text-violet-700 dark:text-violet-300",
  book: "border-rose-500/20 bg-rose-500/10 text-rose-700 dark:text-rose-300",
  ack: "border-emerald-500/20 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
}

function fmtTs(iso?: string) {
  const match = /T(\d{2}:\d{2}:\d{2}\.\d+)/.exec(iso || "")
  return match ? match[1] : (iso || "")
}

function statusLabel(status?: string) {
  const labels: Record<string, string> = {
    idle: "空闲",
    starting: "启动中",
    running: "运行中",
    capturing: "捕获中",
    complete: "已完成",
    completed: "已完成",
    passed: "已通过",
    failed: "失败",
    ready: "就绪",
    stopped: "已停止",
    queued: "排队中",
    thinking: "思考中",
    retrying: "重试中",
    offline: "离线",
    live: "实时",
  }
  return status ? labels[status] || status : "空闲"
}

function summarize(event: EventRecord) {
  switch (event.event) {
    case "send":
    case "recv":
      return `${event.from || ""} → ${event.to || ""}: ${event.body || ""}`
    case "queue_state":
      return `${event.nonce || "消息"} ${event.status || "未知"} · 重试 ${event.retry_count ?? 0}`
    case "container_stop":
    case "container_start":
      return `${event.target || "container"} · ${event.narrative || event.event}`
    default:
      return String(event.narrative || event.body || event.event || "event")
  }
}

function domainServer(agentId?: string) {
  const domain = agentId?.split("@")[1]
  if (domain === "family.test") return "server-family"
  if (domain === "hotel.test") return "server-hotel"
  if (domain === "payment.test") return "server-payment"
  return null
}

function routeForEvent(event?: EventRecord) {
  if (!event?.from || !event.to || !NODE_POINTS[event.from] || !NODE_POINTS[event.to]) return ""
  const sourceServer = domainServer(event.from)
  const targetServer = domainServer(event.to)
  if (!sourceServer || !targetServer) return `M ${NODE_POINTS[event.from].join(" ")} L ${NODE_POINTS[event.to].join(" ")}`
  const points = sourceServer === targetServer
    ? [NODE_POINTS[event.from], NODE_POINTS[sourceServer], NODE_POINTS[event.to]]
    : [NODE_POINTS[event.from], NODE_POINTS[sourceServer], NODE_POINTS.dns, NODE_POINTS[targetServer], NODE_POINTS[event.to]]
  return points.map((point, index) => `${index === 0 ? "M" : "L"} ${point[0]} ${point[1]}`).join(" ")
}

function routeForPacket(signal?: PacketSignal) {
  if (!signal?.src_node || !signal.dst_node) return ""
  const from = NODE_POINTS[signal.src_node]
  const to = NODE_POINTS[signal.dst_node]
  if (!from || !to) return ""
  if (signal.dst_node === "dns" || signal.src_node === "dns") {
    return `M ${from[0]} ${from[1]} C ${from[0]} 120, ${to[0]} 108, ${to[0]} ${to[1]}`
  }
  if (signal.scope === "cross_domain") {
    const bend = Math.min(from[1], to[1]) - 54
    return `M ${from[0]} ${from[1]} C ${from[0]} ${bend}, ${to[0]} ${bend}, ${to[0]} ${to[1]}`
  }
  return `M ${from[0]} ${from[1]} L ${to[0]} ${to[1]}`
}

function protocolStages(event?: EventRecord, capture?: PacketCaptureState): { stages: ProtocolStage[]; basis: string } {
  const stages: ProtocolStage[] = [
    { name: "本地提交", state: "skip", value: "未观测到" },
    { name: "DNS 发现", state: "skip", value: "未观测到" },
    { name: "TLS 传输", state: "skip", value: "未观测到" },
    { name: "ATS 策略", state: "skip", value: "未观测到" },
    { name: "ATK 签名", state: "skip", value: "未观测到" },
    { name: "投递", state: "skip", value: "未观测到" },
  ]
  let basis = "追踪记录"
  if (!event && !capture) return { stages, basis: "暂无证据" }

  if (event?.event === "send") {
    stages[0] = { name: "本地提交", state: event.status === "accepted" ? "pass" : "fail", value: event.status || "已提交" }
    for (let index = 1; index < stages.length; index += 1) stages[index] = { ...stages[index], state: "pending", value: "等待传输" }
  } else if (event?.event === "recv") {
    stages.splice(0, stages.length,
      { name: "本地提交", state: "pass", value: "上游已接受" },
      { name: "DNS 发现", state: "pass", value: "路径已解析" },
      { name: "TLS 传输", state: "pass", value: "已完成" },
      { name: "ATS 策略", state: "pass", value: "接收方已接受" },
      { name: "ATK 签名", state: "pass", value: "接收方已接受" },
      { name: "投递", state: "pass", value: "智能体已接收" },
    )
    basis = "根据成功投递推断"
  } else if (event?.event === "queue_state") {
    stages[0] = { name: "本地提交", state: "pass", value: "家庭域已存储" }
    if (event.status === "failed") {
      stages[1] = { name: "DNS 发现", state: "info", value: "已尝试传输" }
      stages[2] = { name: "TLS 传输", state: "fail", value: "目标离线" }
      stages[3] = { name: "ATS 策略", state: "skip", value: "尚未到达" }
      stages[4] = { name: "ATK 签名", state: "skip", value: "尚未到达" }
      stages[5] = { name: "投递", state: "pending", value: `重试 ${event.retry_count ?? 0}` }
    } else if (event.status === "delivered") {
      stages.splice(1, 5,
        { name: "DNS 发现", state: "pass", value: "路径已解析" },
        { name: "TLS 传输", state: "pass", value: "已完成" },
        { name: "ATS 策略", state: "pass", value: "接收方已接受" },
        { name: "ATK 签名", state: "pass", value: "接收方已接受" },
        { name: "投递", state: "pass", value: "远端已投递" },
      )
      basis = "队列状态与投递推断"
    } else {
      stages[5] = { name: "投递", state: "pending", value: event.status || "已入队" }
    }
  } else if (event?.event === "force_retry") {
    stages[0] = { name: "队列计时器", state: "info", value: "next_retry_at = 0" }
    stages[5] = { name: "投递", state: "pending", value: "已安排重试" }
  } else if (event?.event === "container_stop" || event?.event === "container_start") {
    stages[2] = { name: "目标端", state: event.event === "container_start" ? "pass" : "fail", value: event.target || event.event }
  } else if (event?.event === "scenario_end") {
    stages[5] = { name: "场景结果", state: event.status === "passed" ? "pass" : "fail", value: event.status || "完成" }
  }

  const packet = capture?.summary
  if (capture?.primary_evidence && capture.count > 0 && packet) {
    basis = "以数据包捕获为主"
    if (packet.agent_edge_observed) {
      stages[0] = { name: "智能体边缘", state: "info", value: "已观测到 TLS 流量" }
    }
    if (packet.svcb_observed || packet.dns_observed) {
      stages[1] = { name: "DNS 发现", state: "info", value: packet.svcb_observed ? "已观测到 SVCB 查询" : "已观测到 DNS 查询" }
    }
    if (packet.tls_observed || packet.tcp_observed) {
      stages[2] = {
        name: "TLS 传输",
        state: "info",
        value: packet.tls_observed ? "已观测到 TLS 记录" : "已观测到 TCP SYN",
      }
    }
    if (packet.ats_lookup_observed) {
      stages[3] = { name: "ATS 策略", state: "info", value: "已观测到 TXT 查询" }
    }
    if (packet.atk_lookup_observed) {
      stages[4] = { name: "ATK 签名", state: "info", value: "已观测到 TXT 查询" }
    }
    if (packet.encrypted_data_observed && !["pass", "fail"].includes(stages[5].state)) {
      stages[5] = { name: "投递", state: "info", value: "已观测到加密响应" }
    }
    if (event?.event === "recv" || (event?.event === "queue_state" && event.status === "delivered")) {
      stages[5] = { name: "投递", state: "pass", value: "智能体/服务器已确认" }
      basis = "数据包与投递证据"
    }
  }
  return { stages, basis }
}

function EventIcon({ event }: { event?: string }) {
  const className = "size-3.5"
  if (event === "send") return <Send className={className} />
  if (event === "recv") return <Inbox className={className} />
  if (event === "queue_state") return <Database className={className} />
  if (event === "container_stop") return <Power className={className} />
  if (event === "container_start") return <Zap className={className} />
  if (event === "force_retry") return <RotateCcw className={className} />
  if (event === "scenario_start") return <Play className={className} />
  if (event === "scenario_end") return <CheckCircle2 className={className} />
  return <CircleDashed className={className} />
}

function PacketIcon({ evidence }: { evidence?: string }) {
  const className = "size-3.5"
  if (["svcb_lookup", "dns_query"].includes(evidence || "")) return <Search className={className} />
  if (["ats_lookup", "atk_lookup"].includes(evidence || "")) return <ShieldCheck className={className} />
  if (evidence === "tcp_connect") return <Network className={className} />
  if (evidence === "tcp_reset") return <Unplug className={className} />
  if (["tls_handshake", "tls_appdata"].includes(evidence || "")) return <RadioTower className={className} />
  return <CircleDashed className={className} />
}

function SvgNode({
  x,
  y,
  width,
  title,
  meta,
  state = "running",
  active = false,
  kind = "agent",
}: {
  x: number
  y: number
  width: number
  title: string
  meta: string
  state?: string
  active?: boolean
  kind?: "agent" | "server" | "dns"
}) {
  const isDown = ["exited", "dead", "missing", "unknown"].includes(state)
  const height = kind === "dns" ? 58 : kind === "server" ? 64 : 68
  return (
    <g className={cn("topology-node", `topology-${kind}`, isDown && "node-down", state === "idle" && "node-idle", active && "node-active")} transform={`translate(${x} ${y})`}>
      <rect className="node-aura" x="-5" y="-5" width={width + 10} height={height + 10} rx="17" />
      <rect className="node-shell" width={width} height={height} rx="12" />
      <path className="node-highlight" d={`M 12 1 H ${width - 12}`} />
      <circle className="node-status" cx="17" cy="18" r="5" />
      <text className="node-title" x={width / 2} y={kind === "server" ? 27 : 29} textAnchor="middle">{title}</text>
      <text className="node-meta" x={width / 2} y={kind === "server" ? 46 : 49} textAnchor="middle">{meta}</text>
      {kind === "server" && <g className="server-leds"><circle cx={width - 24} cy="17" r="1.8" /><circle cx={width - 17} cy="17" r="1.8" /><circle cx={width - 10} cy="17" r="1.8" /></g>}
    </g>
  )
}

function evidenceLabel(signal?: PacketSignal) {
  if (!signal) return "等待网络证据"
  if (signal.evidence === "svcb_lookup") return `SVCB 查询 · ${signal.qname}`
  if (signal.evidence === "ats_lookup") return `ATS 记录查询 · ${signal.qname}`
  if (signal.evidence === "atk_lookup") return `ATK 记录查询 · ${signal.qname}`
  if (signal.evidence === "dns_query") return `DNS 查询 · ${signal.qname}`
  if (signal.evidence === "tcp_connect") return `TCP 连接 · ${signal.src_node} → ${signal.dst_node}`
  if (signal.evidence === "tls_handshake") return `TLS 握手记录 · ${signal.src_node} → ${signal.dst_node}`
  if (signal.evidence === "tls_appdata") return `加密 ATP 流量 · ${signal.bytes || 0} 字节`
  if (signal.evidence === "tcp_reset") return `TCP 重置 · ${signal.src_node} → ${signal.dst_node}`
  return signal.evidence.replaceAll("_", " ")
}

function CaptureEvidenceRail({ capture }: { capture?: PacketCaptureState }) {
  const summary = capture?.summary
  const items = [
    ["智能体边缘", summary?.agent_edge_observed],
    ["SVCB", summary?.svcb_observed],
    ["TCP", summary?.tcp_observed],
    ["TLS", summary?.tls_observed],
    ["ATS TXT", summary?.ats_lookup_observed],
    ["ATK TXT", summary?.atk_lookup_observed],
  ] as const
  return (
    <div className="capture-rail" aria-label="基于数据包的 ATP 进度">
      <span className="capture-rail-label"><RadioTower aria-hidden="true" />数据包证据</span>
      <div className="capture-rail-stages">
        {items.map(([label, observed], index) => (
          <span key={label} className={cn("capture-stage", observed && "is-observed")}>
            <span className="capture-stage-dot">{observed ? <Check /> : index + 1}</span>
            <span>{label}</span>
          </span>
        ))}
      </div>
    </div>
  )
}

function TopologyDiagram({ topology, capture }: { topology?: TopologyState; capture?: PacketCaptureState }) {
  const latestPacket = capture?.signals.filter((signal) => !["probe_ready", "probe_complete"].includes(signal.evidence)).at(-1)
  const path = routeForPacket(latestPacket)
  const active = new Set<string>()
  if (latestPacket?.src_node) active.add(latestPacket.src_node)
  if (latestPacket?.dst_node) active.add(latestPacket.dst_node)
  const state = (service: string) => topology?.containers?.[service] || "unknown"
  const queue = (service: string) => topology?.queue_counts?.[service]?.pending || 0
  const motionClass = latestPacket ? `capture-${latestPacket.evidence}` : "capture-idle"

  return (
      <svg className="topology-svg" viewBox="0 0 940 430" preserveAspectRatio="xMidYMid meet" role="img" aria-labelledby="topology-title topology-description">
        <title id="topology-title">包含数据包证据的 ATP 本地 Docker 拓扑</title>
        <desc id="topology-description">三个 ATP 域，以及 DNS、TCP、TLS、ATS 和 ATK 流量的实时 AF_PACKET 证据。</desc>
        <defs>
          <pattern id="topology-grid" width="24" height="24" patternUnits="userSpaceOnUse"><path d="M 24 0 L 0 0 0 24" /></pattern>
          <marker id="topology-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" /></marker>
          <filter id="orb-glow" x="-200%" y="-200%" width="400%" height="400%"><feGaussianBlur stdDeviation="5" result="blur" /><feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
        </defs>
        <rect className="topology-grid-bg" width="940" height="430" rx="16" />

        <g className="network-lines" aria-hidden="true">
          <path d="M 165 201 C 270 98, 354 84, 470 82" />
          <path d="M 470 82 C 586 84, 670 98, 775 201" />
          <path d="M 470 82 L 470 201" />
          <path className="backbone" d="M 246 201 C 300 142, 335 142, 389 201" markerEnd="url(#topology-arrow)" />
          <path className="backbone" d="M 551 201 C 605 142, 640 142, 694 201" markerEnd="url(#topology-arrow)" />
        </g>

        {[
          ["family", 20, "family.test", "172.28.1.0/24"],
          ["hotel", 325, "hotel.test", "172.28.2.0/24"],
          ["payment", 630, "payment.test", "172.28.3.0/24"],
        ].map(([name, x, title, address]) => (
          <g key={String(name)} className={`domain domain-${name}`}>
            <rect className="domain-box" x={Number(x)} y="120" width="290" height="282" rx="20" />
            <rect className="domain-accent" x={Number(x) + 18} y="120" width="48" height="3" rx="2" />
            <text className="domain-kicker" x={Number(x) + 22} y="145">ATP 域</text>
            <text className="domain-title" x={Number(x) + 22} y="164">{title}</text>
            <text className="domain-address" x={Number(x) + 268} y="161" textAnchor="end">{address}</text>
          </g>
        ))}

        <path className="local-link" d="M 165 240 L 165 292" />
        <path className="local-link" d="M 470 240 L 470 274 M 470 274 L 408 292 M 470 274 L 532 292" />
        <path className="local-link" d="M 775 240 L 775 292" />

        <SvgNode x={410} y={24} width={120} title="DNS 服务器" meta="SVCB · ATS · ATK" kind="dns" state={state("dns")} active={active.has("dns")} />
        <SvgNode x={84} y={176} width={162} title="家庭 ATP" meta={`队列 ${queue("server-family")}`} kind="server" state={state("server-family")} active={active.has("server-family")} />
        <SvgNode x={389} y={176} width={162} title="酒店 ATP" meta={`队列 ${queue("server-hotel")}`} kind="server" state={state("server-hotel")} active={active.has("server-hotel")} />
        <SvgNode x={694} y={176} width={162} title="支付 ATP" meta={`队列 ${queue("server-payment")}`} kind="server" state={state("server-payment")} active={active.has("server-payment")} />
        <SvgNode x={84} y={292} width={162} title="旅行智能体" meta="travel@family" state={capture?.summary.agent_edge_observed ? "running" : "idle"} active={active.has("travel@family.test")} />
        <SvgNode x={342} y={292} width={132} title="搜索智能体" meta="search@hotel" state={state("agent-search")} active={active.has("search@hotel.test")} />
        <SvgNode x={482} y={292} width={132} title="价格智能体" meta="rates@hotel" state={state("agent-rates")} active={active.has("rates@hotel.test")} />
        <SvgNode x={694} y={292} width={162} title="结算智能体" meta="bill@payment" state={state("agent-bill")} active={active.has("bill@payment.test")} />

        {path && latestPacket && (
          <g key={`packet-${latestPacket.pseq}-${path}`} className={cn("packet-motion", motionClass)} aria-hidden="true">
            <path className="active-packet-path" d={path} />
            <circle className="message-halo" r="16"><animateMotion dur="0.9s" path={path} fill="freeze" /></circle>
            <circle className="message-orb" r="7" filter="url(#orb-glow)"><animateMotion dur="0.9s" path={path} fill="freeze" /></circle>
            <circle className="message-core" r="2.5"><animateMotion dur="0.9s" path={path} fill="freeze" /></circle>
          </g>
        )}
      </svg>
  )
}

function TopologyEvidence({ capture }: { capture?: PacketCaptureState }) {
  const latestPacket = capture?.signals
    .filter((signal) => !["probe_ready", "probe_complete"].includes(signal.evidence))
    .at(-1)

  return (
    <div className="topology-evidence-panel">
      <CaptureEvidenceRail capture={capture} />
      <div className="capture-current"><Activity aria-hidden="true" /><span>{evidenceLabel(latestPacket)}</span><code>{latestPacket ? `数据包 #${latestPacket.pseq}` : statusLabel(capture?.status)}</code></div>
    </div>
  )
}

function ProtocolPanel({ capture }: { capture?: PacketCaptureState }) {
  const protocol = protocolStages(undefined, capture)
  const latestPacket = capture?.signals.filter((signal) => !["probe_ready", "probe_complete"].includes(signal.evidence)).at(-1)
  const stageIcon = (stage: ProtocolStage) => {
    if (stage.state === "pass") return <Check className="size-3" />
    if (stage.state === "fail") return <TriangleAlert className="size-3" />
    if (stage.state === "pending") return <LoaderCircle className="size-3 animate-spin" />
    if (stage.state === "info") return <Activity className="size-3" />
    return <span className="size-1.5 rounded-full bg-border" />
  }
  return (
    <Card className="protocol-card min-h-0 overflow-hidden">
      <CardHeader className="border-b py-3">
        <CardTitle className="flex items-center gap-2 text-sm"><ShieldCheck className="size-4 text-primary" />协议路径</CardTitle>
        <CardDescription className="line-clamp-1 text-xs">{evidenceLabel(latestPacket)}</CardDescription>
        <CardAction><Badge variant="outline" className="font-mono text-micro">{protocol.basis}</Badge></CardAction>
      </CardHeader>
      <CardContent className="grid min-h-0 flex-1 grid-cols-3 items-center gap-x-3 gap-y-4 py-4 xl:grid-cols-6">
        {protocol.stages.map((stage) => (
          <div key={stage.name} className={cn("protocol-stage", `protocol-${stage.state}`)}>
            <span className="protocol-icon">{stageIcon(stage)}</span>
            <span className="min-w-0">
              <strong>{stage.name}</strong>
              <small>{stage.value}</small>
            </span>
          </div>
        ))}
      </CardContent>
      <CardFooter className="grid grid-cols-2 gap-3 border-t py-3 text-[10px] lg:grid-cols-4">
        {[
          ["传感器", latestPacket?.sensor],
          ["来源", latestPacket?.src_node],
          ["目标", latestPacket?.dst_node],
          ["证据", latestPacket?.evidence],
        ].map(([label, value]) => (
          <span key={label} className="min-w-0"><small className="block uppercase tracking-wider text-muted-foreground">{label}</small><code className="block truncate font-mono text-foreground">{value || "—"}</code></span>
        ))}
      </CardFooter>
    </Card>
  )
}

function activitySummary(activity: ChatActivity) {
  if (activity.tool === "atp_send") return `${activity.args.subject || "消息"} → ${activity.args.to || "ATP"}`
  if (activity.tool === "atp_receive") return `等待 ${activity.args.wait_seconds || "?"} 秒接收 ATP 投递`
  if (activity.tool === "get_task_state") return "检查对话任务状态"
  return activity.tool.replaceAll("_", " ")
}

const AUDIT_AGENTS = [
  { role: "travel", id: "travel@family.test", name: "旅行", detail: "面向用户的任务编排" },
  { role: "search", id: "search@hotel.test", name: "搜索", detail: "库存查询响应" },
  { role: "rates", id: "rates@hotel.test", name: "价格", detail: "价格事件发布" },
  { role: "bill", id: "bill@payment.test", name: "结算", detail: "模拟支付授权" },
] as const

function auditPreview(data: unknown) {
  const value = typeof data === "string" ? data : JSON.stringify(data, null, 2) || "—"
  return value.length > 720 ? `${value.slice(0, 720)}\n…` : value
}

function auditKind(kind: AgentAuditEvent["kind"]) {
  if (kind === "user_input") return { label: "用户输入", icon: <MessageCircle /> }
  if (kind === "atp_input") return { label: "ATP 输入", icon: <Inbox /> }
  if (kind === "tool_input") return { label: "工具输入", icon: <Send /> }
  if (kind === "tool_output") return { label: "工具输出", icon: <FileJson2 /> }
  return { label: "响应", icon: <Bot /> }
}

function decisionSummary(role: string, events: AgentAuditEvent[]) {
  const tools = [...new Set(events.filter((event) => event.kind === "tool_input").map((event) => event.title))]
  const inbound = events.find((event) => event.kind === "user_input" || event.kind === "atp_input")
  const response = events.some((event) => event.kind === "response")
  if (!events.length) return "等待用户指令或 ATP 投递。"
  const source = inbound?.kind === "user_input" ? "用户指令" : inbound ? "一条 ATP 投递" : "当前任务状态"
  if (!tools.length) return `${role}智能体已收到${source}，正在准备响应。`
  return `${role}智能体在收到${source}后调用了 ${tools.join(" → ")}${response ? "，随后返回了响应。" : "。"}`
}

function AgentAuditPanel({ chat }: { chat: ChatState }) {
  return (
    <Card className="agent-audit-card min-h-0 overflow-hidden">
      <CardHeader className="border-b py-3">
        <CardTitle className="flex items-center gap-2 text-sm"><Activity className="size-4 text-primary" />四智能体执行视图</CardTitle>
        <CardDescription className="text-xs">应用输入输出、类型化工具结果和可观测决策摘要——与数据包证据分开展示。</CardDescription>
        <CardAction><Badge variant="outline" className="font-mono">{chat.agent_audit.length} 条记录</Badge></CardAction>
      </CardHeader>
      <CardContent className="min-h-0 flex-1 p-3">
        <div className="agent-audit-grid">
          {AUDIT_AGENTS.map((agent) => {
            const events = chat.agent_audit.filter((event) => event.role === agent.role)
            const latest = events.at(-1)
            return (
              <section key={agent.role} className="agent-audit-column" aria-label={`${agent.name}智能体审计`}>
                <header className="agent-audit-header">
                  <span className="agent-audit-avatar"><Bot /></span>
                  <span className="min-w-0"><strong>{agent.name}智能体</strong><small>{agent.id} · {agent.detail}</small></span>
                  <Badge variant={latest?.kind === "response" ? "success" : events.length ? "secondary" : "outline"}>{events.length || "空闲"}</Badge>
                </header>
                <div className="agent-decision-summary"><Sparkles /><span><strong>可观测决策摘要</strong><small>{decisionSummary(agent.name, events)}</small></span></div>
                <ScrollArea className="agent-audit-scroll">
                  <div className="agent-audit-events">
                    {!events.length && <div className="agent-audit-empty">暂无应用层记录。</div>}
                    {events.map((event) => {
                      const meta = auditKind(event.kind)
                      return (
                        <article key={event.id} className={cn("agent-audit-event", `audit-${event.kind}`)}>
                          <div className="agent-audit-event-meta"><span>{meta.icon}</span><Badge variant="outline">{meta.label}</Badge><time>{fmtTs(event.ts)}</time></div>
                          <strong>{event.title}</strong>
                          <pre>{auditPreview(event.data)}</pre>
                        </article>
                      )
                    })}
                  </div>
                </ScrollArea>
              </section>
            )
          })}
        </div>
      </CardContent>
    </Card>
  )
}

function AgentConversation({
  chat,
  draft,
  onDraftChange,
  onSend,
  onReset,
}: {
  chat: ChatState
  draft: string
  onDraftChange: (value: string) => void
  onSend: () => void
  onReset: () => void
}) {
  const busy = ["queued", "starting", "thinking"].includes(chat.status)
  const endRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    const viewport = endRef.current?.closest<HTMLElement>('[data-slot="scroll-area-viewport"]')
    viewport?.scrollTo({ top: viewport.scrollHeight, behavior: busy ? "smooth" : "auto" })
  }, [chat.messages.length, chat.activities.length, busy])

  return (
    <Card className="chat-card min-h-0 overflow-hidden">
      <CardHeader className="border-b py-3">
        <CardTitle className="flex items-center gap-2 text-sm"><MessageCircle className="size-4 text-primary" />与旅行智能体对话</CardTitle>
        <CardDescription className="text-xs">为智能体设定目标；每次服务交接仍通过 ATP 完成。</CardDescription>
        <CardAction className="flex items-center gap-2">
          <Badge variant={chat.runtime.status === "ready" ? "success" : "secondary"}><Bot />智能体</Badge>
          <Tooltip><TooltipTrigger asChild><Button variant="ghost" size="icon-sm" aria-label="新建对话" disabled={busy} onClick={onReset}><Trash2 /></Button></TooltipTrigger><TooltipContent>新建对话</TooltipContent></Tooltip>
        </CardAction>
      </CardHeader>

      <CardContent className="chat-content min-h-0 flex-1 p-0">
        <ScrollArea className="h-full">
          <div className="chat-thread" aria-live="polite">
            {!chat.messages.length && (
              <div className="chat-empty">
                <span className="chat-empty-icon"><Bot /></span>
                <strong>旅行智能体已就绪</strong>
                <p>你可以让它搜索酒店、关注价格，或授权模拟预订；也可以在同一会话中继续追问。</p>
              </div>
            )}
            {chat.messages.map((message) => {
              const activities = chat.activities.filter((activity) => activity.request_id === message.request_id)
              return (
                <div key={message.id} className={cn("chat-message", `is-${message.role}`)}>
                  <div className="chat-avatar">{message.role === "assistant" ? <Bot /> : <span>你</span>}</div>
                  <div className="chat-message-body">
                    <div className="chat-message-meta"><strong>{message.role === "assistant" ? "旅行智能体" : "你"}</strong><span>{fmtTs(message.updated_at)}</span></div>
                    <div className="chat-bubble">
                      {message.content || (message.status !== "failed" && <span className="agent-typing"><i /><i /><i /></span>)}
                    </div>
                    {message.role === "assistant" && activities.length > 0 && (
                      <details className="agent-trace" open={activities.some((activity) => activity.status === "running") || undefined}>
                        <summary>
                          <span className="agent-trace-icon"><Activity /></span>
                          <span><strong>ATP 执行轨迹</strong><small>{activities.length} 次类型化工具调用</small></span>
                          <ChevronDown className="agent-trace-chevron" />
                        </summary>
                        <div className="agent-activities" aria-label="智能体工具活动">
                          {activities.map((activity) => (
                            <div key={activity.id} className={cn("agent-activity", `is-${activity.status}`)}>
                              <span className="agent-tool-icon">{activity.status === "running" ? <LoaderCircle className="animate-spin" /> : activity.status === "completed" ? <Check /> : <TriangleAlert />}</span>
                              <span><strong>{activity.tool}</strong><small>{activitySummary(activity)}</small></span>
                            </div>
                          ))}
                        </div>
                      </details>
                    )}
                  </div>
                </div>
              )
            })}
            <div ref={endRef} />
          </div>
        </ScrollArea>
      </CardContent>

      <CardFooter className="chat-composer border-t">
        <div className="quick-prompts" aria-label="示例指令">
          <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => onDraftChange("搜索一家巴黎酒店并入住两晚，但暂时不要预订。")}>仅搜索</Button>
          <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => onDraftChange("搜索一家巴黎酒店并入住两晚，订阅 ParisGarden 的三次价格更新，并以最低价格完成模拟预订和付款。")}>完整预订</Button>
        </div>
        <div className="composer-row">
          <label className="sr-only" htmlFor="agent-message">给旅行智能体发送消息</label>
          <Textarea
            id="agent-message"
            value={draft}
            disabled={busy}
            rows={2}
            placeholder="告诉旅行智能体你想做什么……"
            onChange={(event) => onDraftChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault()
                if (draft.trim() && !busy) onSend()
              }
            }}
          />
          <Button size="icon" aria-label="发送消息" disabled={busy || !draft.trim()} onClick={onSend}>
            {busy ? <LoaderCircle className="animate-spin" /> : <Send />}
          </Button>
        </div>
        <div className="composer-meta"><span>{busy ? "智能体正在推理并调用工具……" : "按 Enter 发送 · Shift+Enter 换行"}</span><code>ATP 已连接</code></div>
      </CardFooter>
    </Card>
  )
}

export default function App() {
  const [runs, setRuns] = useState<RunInfo[]>([])
  const [activeRunId, setActiveRunId] = useState<string>("")
  const [events, setEvents] = useState<EventRecord[]>([])
  const [selectedEvent, setSelectedEvent] = useState<EventRecord | undefined>()
  const [selectedPacket, setSelectedPacket] = useState<PacketSignal | undefined>()
  const [packetFilter, setPacketFilter] = useState("all")
  const [scenario, setScenario] = useState<ScenarioState>(EMPTY_SCENARIO)
  const [chat, setChat] = useState<ChatState>(EMPTY_CHAT)
  const [draft, setDraft] = useState("")
  const [topology, setTopology] = useState<TopologyState>()
  const [packetCapture, setPacketCapture] = useState<PacketCaptureState>()
  const [liveState, setLiveState] = useState<LiveState>("offline")
  const [statusMessage, setStatusMessage] = useState("已就绪。请向旅行智能体发送指令，或选择已有运行记录。")
  const [statusMeta, setStatusMeta] = useState("")

  const sourceRef = useRef<EventSource | null>(null)
  const eventKeysRef = useRef(new Set<string>())
  const activeRunRef = useRef("")
  const lastScenarioStatusRef = useRef("idle")
  const lastPacketCountRef = useRef(0)
  const timelineEndRef = useRef<HTMLDivElement | null>(null)

  const stopStream = useCallback(() => {
    sourceRef.current?.close()
    sourceRef.current = null
    setLiveState("offline")
  }, [])

  const insertEvent = useCallback((event: EventRecord, animate = true) => {
    const key = `${event.run_id || activeRunRef.current}:${event.seq ?? `${event.ts}-${event.event}-${event.nonce || ""}`}`
    if (eventKeysRef.current.has(key)) return
    eventKeysRef.current.add(key)
    setEvents((current) => [...current, event].sort((a, b) => Number(a.seq || 0) - Number(b.seq || 0)))
    if (animate) setSelectedEvent(event)
  }, [])

  const connectStream = useCallback((runId: string, after: number) => {
    stopStream()
    const source = new EventSource(`/api/stream/${encodeURIComponent(runId)}?after=${after}`)
    sourceRef.current = source
    setLiveState("connecting")
    source.addEventListener("ready", () => setLiveState("live"))
    source.addEventListener("trace", (message) => insertEvent(JSON.parse((message as MessageEvent).data) as EventRecord, true))
    source.addEventListener("heartbeat", (message) => {
      const payload = JSON.parse((message as MessageEvent).data) as { ts?: string; last_seq?: number }
      setStatusMeta(`心跳 ${fmtTs(payload.ts)} · 序号 ${payload.last_seq ?? "—"}`)
    })
    source.addEventListener("error", () => setLiveState("retrying"))
  }, [insertEvent, stopStream])

  const loadPacketEvidence = useCallback(async (runId: string) => {
    const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/packets`)
    if (!response.ok) return
    const payload = await response.json() as PacketCaptureState
    if (activeRunRef.current === runId) {
      setPacketCapture(payload)
      setSelectedPacket(payload.signals.filter((signal) => !["probe_ready", "probe_complete"].includes(signal.evidence)).at(-1))
    }
  }, [])

  const loadTrace = useCallback(async (runId: string, _connect = false) => {
    stopStream()
    activeRunRef.current = runId
    setActiveRunId(runId)
    setEvents([])
    setSelectedEvent(undefined)
    setPacketCapture(undefined)
    eventKeysRef.current = new Set()
    if (!runId) return
    await loadPacketEvidence(runId)
    setStatusMessage("已加载数据包捕获证据。")
    setStatusMeta(runId)
  }, [loadPacketEvidence, stopStream])

  const refreshRuns = useCallback(async (preferred?: string) => {
    const response = await fetch("/api/runs")
    const payload = await response.json() as { runs: RunInfo[] }
    setRuns(payload.runs)
    if (preferred) setActiveRunId(preferred)
    return payload.runs
  }, [])

  const refreshTopology = useCallback(async () => {
    try {
      const response = await fetch("/api/state/topology")
      const payload = await response.json() as TopologyState
      setTopology(payload)
      setScenario(payload.scenario || EMPTY_SCENARIO)
      if (payload.chat) setChat(payload.chat)
      if (payload.packet_capture?.run_id === activeRunRef.current) setPacketCapture(payload.packet_capture)
      const status = payload.scenario?.status
      if (lastScenarioStatusRef.current !== status && ["passed", "failed"].includes(status)) {
        setStatusMessage(status === "passed" ? "场景已成功完成。" : "场景执行失败。")
        setStatusMeta(payload.scenario.error || payload.scenario.run_id || "")
        void refreshRuns(payload.scenario.run_id || undefined)
      }
      lastScenarioStatusRef.current = status
    } catch (error) {
      setStatusMessage("控制器状态不可用。")
      setStatusMeta(String(error))
    }
  }, [refreshRuns])

  const refreshChat = useCallback(async () => {
    try {
      const response = await fetch("/api/state/chat")
      if (!response.ok) return
      const payload = await response.json() as { chat: ChatState }
      setChat(payload.chat)
    } catch {
      // Topology polling still provides a slower fallback copy of chat state.
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const list = await refreshRuns()
        await refreshTopology()
        if (!cancelled && list.length) await loadTrace(list[0].run_id, true)
      } catch (error) {
        setStatusMessage("无法初始化演示界面。")
        setStatusMeta(String(error))
      }
    })()
    const timer = window.setInterval(() => void refreshTopology(), 1500)
    const chatTimer = window.setInterval(() => void refreshChat(), 650)
    return () => {
      cancelled = true
      window.clearInterval(timer)
      window.clearInterval(chatTimer)
      sourceRef.current?.close()
    }
  }, [loadTrace, refreshChat, refreshRuns, refreshTopology])

  useEffect(() => {
    const viewport = timelineEndRef.current?.closest<HTMLElement>('[data-slot="scroll-area-viewport"]')
    viewport?.scrollTo({ top: viewport.scrollHeight, behavior: "smooth" })
  }, [packetCapture?.count])

  useEffect(() => {
    const latest = packetCapture?.signals.filter((signal) => !["probe_ready", "probe_complete"].includes(signal.evidence)).at(-1)
    const count = packetCapture?.count || 0
    if (latest && count > lastPacketCountRef.current) setSelectedPacket(latest)
    lastPacketCountRef.current = count
  }, [packetCapture])

  const controlOp = async (endpoint: string, label: string) => {
    setStatusMessage(`${label}…`)
    setStatusMeta("")
    try {
      const response = await fetch(endpoint, { method: "POST" })
      const payload = await response.json() as { ts?: string }
      if (!response.ok) throw new Error(JSON.stringify(payload))
      setStatusMessage(`${label}已完成。`)
      setStatusMeta(fmtTs(payload.ts))
      await refreshTopology()
    } catch (error) {
      setStatusMessage(`${label}失败。`)
      setStatusMeta(String(error))
    }
  }

  const sendChatMessage = async () => {
    const message = draft.trim()
    if (!message || ["queued", "starting", "thinking"].includes(chat.status)) return
    setDraft("")
    setStatusMessage("旅行智能体正在处理你的指令……")
    setStatusMeta("")
    try {
      const response = await fetch("/api/chat/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
      })
      const payload = await response.json() as { chat?: ChatState; run_id?: string; error?: string }
      if (!response.ok || !payload.chat || !payload.run_id) throw new Error(payload.error || `HTTP ${response.status}`)
      setChat(payload.chat)
      await refreshRuns(payload.run_id)
      await loadTrace(payload.run_id)
    } catch (error) {
      setDraft(message)
      setStatusMessage("无法发送指令。")
      setStatusMeta(String(error))
    }
  }

  const resetChat = async () => {
    try {
      const response = await fetch("/api/chat/reset", { method: "POST" })
      const payload = await response.json() as { chat?: ChatState; error?: string }
      if (!response.ok || !payload.chat) throw new Error(payload.error || `HTTP ${response.status}`)
      setChat(payload.chat)
      setStatusMessage("已开始新的智能体对话。")
      setStatusMeta(payload.chat.session_id)
    } catch (error) {
      setStatusMessage("无法重置对话。")
      setStatusMeta(String(error))
    }
  }

  const packetSignals = useMemo(
    () => (packetCapture?.signals || []).filter((signal) => !["probe_ready", "probe_complete"].includes(signal.evidence)),
    [packetCapture],
  )
  const filteredPackets = useMemo(() => packetSignals.filter((signal) => {
    if (packetFilter === "all") return signal.scope === "cross_domain"
    if (packetFilter === "dns") return ["dns_query", "svcb_lookup"].includes(signal.evidence)
    if (packetFilter === "tcp") return ["tcp_connect", "tcp_reset"].includes(signal.evidence)
    if (packetFilter === "tls") return ["tls_handshake", "tls_appdata"].includes(signal.evidence) && signal.scope === "cross_domain"
    if (packetFilter === "policy") return ["ats_lookup", "atk_lookup"].includes(signal.evidence)
    if (packetFilter === "edge") return signal.scope === "edge_agent"
    return true
  }), [packetFilter, packetSignals])
  const scenarioRunning = ["starting", "running"].includes(scenario.status)
  const chatBusy = ["queued", "starting", "thinking"].includes(chat.status)
  const packetProgressFlags = packetCapture?.summary ? [
    packetCapture.summary.agent_edge_observed,
    packetCapture.summary.svcb_observed,
    packetCapture.summary.tcp_observed,
    packetCapture.summary.tls_observed,
    packetCapture.summary.ats_lookup_observed,
    packetCapture.summary.atk_lookup_observed,
  ] : []
  const progress = packetProgressFlags.length ? (packetProgressFlags.filter(Boolean).length / packetProgressFlags.length) * 100 : 0

  return (
    <TooltipProvider>
      <div className="app-shell">
        <header className="app-header">
          <div className="brand-block">
            <div className="min-w-0">
              <div className="flex items-center gap-2"><strong>ATP 本地旅行演示</strong><Badge variant="secondary" className="hidden sm:inline-flex">IETF 126</Badge></div>
              <p>family.test · hotel.test · payment.test</p>
            </div>
          </div>

          <div className="scenario-block">
            <Badge variant="secondary" className="h-8 shrink-0 px-3"><Bot />4 个智能体</Badge>
            <div className="min-w-0 flex-1">
              <div className="mb-1.5 flex items-center justify-between gap-4 text-xs"><span className="truncate font-medium">{selectedPacket ? evidenceLabel(selectedPacket) : "等待数据包证据"}</span><span className="font-mono text-muted-foreground">{packetProgressFlags.filter(Boolean).length}/6</span></div>
              <Progress value={progress} />
            </div>
          </div>

          <div className="run-block">
            <Select value={activeRunId} onValueChange={(value) => void loadTrace(value)}>
              <SelectTrigger size="sm" className="run-select w-[220px] bg-background/70"><SelectValue placeholder="选择运行记录" /></SelectTrigger>
              <SelectContent>
                {runs.map((run) => <SelectItem key={run.run_id} value={run.run_id} className="font-mono text-xs">{run.run_id} · {run.packet_events || 0} 个数据包</SelectItem>)}
              </SelectContent>
            </Select>
            <Tooltip><TooltipTrigger asChild><Button variant="outline" size="icon-sm" aria-label="刷新运行记录" onClick={() => void refreshRuns()}><RefreshCw /></Button></TooltipTrigger><TooltipContent>刷新运行记录</TooltipContent></Tooltip>
            <Badge variant={packetCapture?.status === "capturing" ? "success" : "outline"}><RadioTower className={cn(packetCapture?.status === "starting" && "animate-pulse")} />{statusLabel(packetCapture?.status)}</Badge>
          </div>
        </header>

        <div className="control-strip">
          <span className="flex items-center gap-1.5 text-micro font-semibold uppercase tracking-[0.16em] text-muted-foreground"><Sparkles className="size-3.5" />手动控制</span>
          <Button variant="destructive" size="sm" disabled={scenarioRunning} onClick={() => void controlOp("/api/control/payment-stop", "停止支付")}><Pause />停止支付</Button>
          <Button variant="outline" size="sm" disabled={scenarioRunning} onClick={() => void controlOp("/api/control/payment-start", "启动支付")}><Power />启动支付</Button>
          <Button variant="outline" size="sm" disabled={scenarioRunning} onClick={() => void controlOp("/api/control/force-retry", "强制重试")}><RotateCcw />强制重试</Button>
          <Button variant="ghost" size="sm" disabled={scenarioRunning || chatBusy} onClick={() => void controlOp("/api/control/soft-reset", "软重置")}><Database />软重置</Button>
          <span className="ml-auto hidden text-xs text-muted-foreground xl:inline">智能体目标来自对话；故障注入仍需显式触发。</span>
        </div>

        <main className="dashboard-grid">
          <Card className="topology-card min-h-0 overflow-hidden">
            <CardHeader className="border-b py-3">
              <CardTitle className="flex items-center gap-2 text-sm"><Network className="size-4 text-primary" />实时拓扑</CardTitle>
              <CardDescription className="text-xs">四个独立的智能体会话；数据包证据展示其 ATP 交接过程。</CardDescription>
              <CardAction className="flex items-center gap-2">
                <Badge variant={packetCapture?.status === "capturing" ? "success" : "secondary"}><RadioTower />{statusLabel(packetCapture?.status)}</Badge>
                <Badge variant="outline" className="font-mono">{packetCapture?.count || 0} 个数据包</Badge>
              </CardAction>
            </CardHeader>
            <CardContent className="topology-card-content p-2">
              <div className="topology-svg-frame">
                <TopologyDiagram topology={topology} capture={packetCapture} />
              </div>
              <TopologyEvidence capture={packetCapture} />
            </CardContent>
          </Card>

          <AgentConversation
            chat={chat}
            draft={draft}
            onDraftChange={setDraft}
            onSend={() => void sendChatMessage()}
            onReset={() => void resetChat()}
          />

          <Card className="timeline-card min-h-0 overflow-hidden">
            <CardHeader className="border-b py-3">
              <CardTitle className="flex items-center gap-2 text-sm"><RadioTower className="size-4 text-primary" />数据包证据 <Badge variant="secondary">{packetCapture?.count || 0}</Badge></CardTitle>
              <CardDescription className="font-mono text-micro">{activeRunId || "未选择运行记录"}</CardDescription>
            </CardHeader>
            <Tabs value={packetFilter} onValueChange={setPacketFilter} className="min-h-0 flex-1 gap-0">
              <div className="border-b px-3 py-2">
                <TabsList className="grid w-full grid-cols-6">
                  {[["all", "全部"], ["dns", "DNS"], ["tcp", "TCP"], ["tls", "TLS"], ["policy", "ATS/ATK"], ["edge", "智能体"]].map(([value, label]) => <TabsTrigger key={value} value={value}>{label}</TabsTrigger>)}
                </TabsList>
              </div>
              <CardContent className="min-h-0 flex-1 p-0">
                <ScrollArea className="h-full">
                  <div className="timeline-list">
                    {filteredPackets.map((signal) => (
                      <button
                        type="button"
                        key={`${signal.pseq}-${signal.sensor}`}
                        className={cn("timeline-row group", selectedPacket?.pseq === signal.pseq && "is-selected", signal.pseq === packetSignals.at(-1)?.pseq && "is-latest")}
                        onClick={() => setSelectedPacket(signal)}
                      >
                        <span className="timeline-event-icon"><PacketIcon evidence={signal.evidence} /></span>
                        <span className="font-mono text-micro text-muted-foreground">{signal.pseq}</span>
                        <span className="font-mono text-micro text-muted-foreground">{fmtTs(signal.ts)}</span>
                        <Badge variant="outline" className="timeline-evidence-badge font-mono text-micro uppercase">{signal.evidence.replace("_lookup", "").replace("tls_", "")}</Badge>
                        <span className="truncate text-left text-xs">{evidenceLabel(signal)}</span>
                        <ArrowRight className="size-3.5 opacity-0 transition-all group-hover:translate-x-0.5 group-hover:opacity-60" />
                      </button>
                    ))}
                    {!filteredPackets.length && <div className="grid min-h-48 place-items-center text-xs text-muted-foreground"><span className="flex items-center gap-2"><CircleDashed className="size-4" />此分类下暂无数据包证据</span></div>}
                    <div ref={timelineEndRef} />
                  </div>
                </ScrollArea>
              </CardContent>
            </Tabs>
          </Card>

          <ProtocolPanel capture={packetCapture} />

          <Card className="detail-card min-h-0 overflow-hidden">
            <CardHeader className="border-b py-3">
              <CardTitle className="flex items-center gap-2 text-sm"><FileJson2 className="size-4 text-primary" />数据包详情</CardTitle>
              <CardDescription className="text-xs">所选数据包的头部字段</CardDescription>
              <CardAction><Badge variant="secondary" className="font-mono">#{selectedPacket?.pseq ?? "—"}</Badge></CardAction>
            </CardHeader>
            <CardContent className="min-h-0 flex-1 bg-muted p-0 text-foreground">
              <ScrollArea className="h-full">
                <pre className="p-4 font-mono text-micro leading-relaxed whitespace-pre-wrap break-words">{selectedPacket ? JSON.stringify(selectedPacket, null, 2) : "请开始捕获或选择数据包证据。"}</pre>
              </ScrollArea>
            </CardContent>
          </Card>

          <AgentAuditPanel chat={chat} />
        </main>

        <footer className="status-bar">
          <span className="flex items-center gap-2"><span className={cn("size-1.5 rounded-full", chatBusy || packetCapture?.status === "capturing" ? "animate-pulse bg-emerald-500" : "bg-muted-foreground")} />智能体 · {statusLabel(chat.status)} · AF_PACKET {packetCapture?.count || 0}</span>
          <span className="font-mono text-muted-foreground">{chat.error || `${statusMessage}${statusMeta ? ` · ${statusMeta}` : ""}`}</span>
        </footer>
      </div>
    </TooltipProvider>
  )
}
