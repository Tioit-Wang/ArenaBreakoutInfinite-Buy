import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import {
  CircleHelp,
  Eye,
  Play,
  Square,
  Trash2,
} from "lucide-react"

import { useRuntimeStore } from "@/app/store"
import { api } from "@/lib/api"
import type { GoodsRecord, SingleTaskRecord } from "@/lib/types"
import { cn } from "@/lib/utils"
import { InlineNote } from "@/components/minimal-page"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
} from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

type NoticeTone = "slate" | "emerald" | "rose"

const emptySingleTask = (): SingleTaskRecord => ({
  id: crypto.randomUUID(),
  itemId: "",
  itemName: "",
  enabled: true,
  priceThreshold: 0,
  pricePremiumPct: 0,
  restockPrice: 0,
  restockPremiumPct: 0,
  targetTotal: 0,
  purchased: 0,
  durationMin: 10,
  orderIndex: 0,
  createdAt: new Date().toISOString(),
  updatedAt: new Date().toISOString(),
})

export function SingleTasksPage() {
  const bootstrap = useRuntimeStore((state) => state.bootstrap)
  const runtime = useRuntimeStore((state) => state.runtime)
  const logs = useRuntimeStore((state) => state.logs)
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<SingleTaskRecord | null>(null)
  const [logDrawerOpen, setLogDrawerOpen] = useState(false)
  const [logsClearedAt, setLogsClearedAt] = useState<number | null>(null)
  const [captureArchiveSaving, setCaptureArchiveSaving] = useState(false)
  const [captureArchiveOverride, setCaptureArchiveOverride] = useState<boolean | null>(null)
  const [captureArchiveMessage, setCaptureArchiveMessage] = useState("")
  const [captureArchiveMessageTone, setCaptureArchiveMessageTone] =
    useState<NoticeTone>("slate")
  const lastSavedRef = useRef("")

  useEffect(() => {
    if (!bootstrap) return
    const current = bootstrap.singleTasks[0]
    const nextDraft = current ? structuredClone(current) : emptySingleTask()
    setDraft(nextDraft)
    lastSavedRef.current = normalizeTask(nextDraft)
  }, [bootstrap])

  const goodsMap = useMemo(() => {
    const map = new Map<string, GoodsRecord>()
    bootstrap?.goods.forEach((item) => map.set(item.id, item))
    return map
  }, [bootstrap?.goods])

  const saveDraft = useCallback(async () => {
    if (!bootstrap || !draft?.itemId) return null
    const saved = await api.singleTasksSave({
      ...draft,
      enabled: true,
      orderIndex: 0,
      updatedAt: new Date().toISOString(),
    })
    await Promise.all(
      bootstrap.singleTasks
        .filter((item) => item.id !== saved.id)
        .map((item) => api.singleTasksDelete(item.id)),
    )
    await queryClient.invalidateQueries({ queryKey: ["bootstrap"] })
    lastSavedRef.current = normalizeTask(saved)
    return saved
  }, [bootstrap, draft, queryClient])

  useEffect(() => {
    if (!draft?.itemId) return
    const normalized = normalizeTask(draft)
    if (normalized === lastSavedRef.current) return
    const timer = window.setTimeout(() => {
      void saveDraft()
    }, 450)
    return () => window.clearTimeout(timer)
  }, [draft, saveDraft])

  useEffect(() => {
    setCaptureArchiveOverride(null)
  }, [bootstrap?.config.debug.saveSingleCaptureImages])

  const singleLogs = logs.filter((log) => {
    if (log.scope !== "automation:single") {
      return false
    }
    if (runtime.mode === "single" && runtime.sessionId) {
      return log.sessionId === runtime.sessionId
    }
    return true
  })

  const visibleLogs = useMemo(() => {
    const filtered = logsClearedAt
      ? singleLogs.filter((log) => {
          const ts = Date.parse(log.createdAt)
          return Number.isNaN(ts) || ts >= logsClearedAt
        })
      : singleLogs
    return filtered.slice(0, 80)
  }, [logsClearedAt, singleLogs])

  if (!bootstrap || !draft) return null

  const isRunning = runtime.state === "running" || runtime.state === "paused"
  const runtimeTone =
    runtime.state === "running"
      ? "default"
      : runtime.state === "failed"
        ? "destructive"
        : "outline"

  const restockCeiling =
    draft.restockPrice > 0
      ? Math.round(draft.restockPrice * (1 + draft.restockPremiumPct / 100))
      : 0
  const normalCeiling =
    draft.priceThreshold > 0
      ? Math.round(draft.priceThreshold * (1 + draft.pricePremiumPct / 100))
      : 0
  const captureArchiveEnabled =
    captureArchiveOverride ?? bootstrap.config.debug.saveSingleCaptureImages
  const captureArchiveDir = `${bootstrap.paths.debugDir}\\single-captures\\<sessionId>`

  const startOrStop = async () => {
    if (isRunning) {
      await api.automationStop()
      await queryClient.invalidateQueries({ queryKey: ["bootstrap"] })
      return
    }
    await saveDraft()
    await api.automationStartSingle()
    await queryClient.invalidateQueries({ queryKey: ["bootstrap"] })
  }

  const toggleCaptureArchive = async () => {
    if (!bootstrap || captureArchiveSaving || isRunning) {
      return
    }
    const nextEnabled = !captureArchiveEnabled
    setCaptureArchiveOverride(nextEnabled)
    setCaptureArchiveSaving(true)
    try {
      await api.configSave({
        ...bootstrap.config,
        debug: {
          ...bootstrap.config.debug,
          saveSingleCaptureImages: nextEnabled,
        },
      })
      setCaptureArchiveMessageTone("emerald")
      setCaptureArchiveMessage(
        nextEnabled
          ? `已开启抓图存档。新会话会保存到 ${captureArchiveDir}`
          : "已关闭抓图存档。后续单商品会话不再自动保存抓图。",
      )
      await queryClient.invalidateQueries({ queryKey: ["bootstrap"] })
    } catch (error) {
      setCaptureArchiveOverride(null)
      setCaptureArchiveMessageTone("rose")
      setCaptureArchiveMessage(`抓图存档设置保存失败：${String(error)}`)
    } finally {
      setCaptureArchiveSaving(false)
    }
  }

  return (
    <div className="grid gap-10">
      <div className="grid gap-10">
        <section className="px-1 pt-4 md:pt-8">
          <div className="space-y-5">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={runtimeTone} className="rounded-full px-3 py-1">
                {runtime.state}
              </Badge>
              {draft.itemName ? (
                <Badge variant="outline" className="rounded-full px-3 py-1 text-slate-600">
                  {draft.itemName}
                </Badge>
              ) : null}
            </div>

            <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
              <h1 className="font-display text-4xl leading-tight tracking-tight text-slate-950 md:text-5xl">
                单商品抢购
              </h1>

              <div className="flex flex-wrap items-center gap-3">
                <Button
                  size="lg"
                  variant={isRunning ? "destructive" : "default"}
                  onClick={() => void startOrStop()}
                  disabled={!draft.itemId}
                  className="h-12 min-w-32 rounded-full px-8"
                >
                  {isRunning ? (
                    <>
                      <Square className="mr-2 size-4" />
                      终止
                    </>
                  ) : (
                    <>
                      <Play className="mr-2 size-4" />
                      开始
                    </>
                  )}
                </Button>
                <Button
                  size="lg"
                  variant="secondary"
                  onClick={() => setLogDrawerOpen(true)}
                  className="h-12 rounded-full px-6"
                >
                  <Eye className="mr-2 size-4" />
                  查看日志
                </Button>
              </div>
            </div>
          </div>

        </section>

        <Card className="overflow-visible rounded-[36px] border-white/60 bg-white/72 shadow-none backdrop-blur-sm">
          <CardContent className="grid gap-6 p-6 md:p-8">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
              <div className="space-y-2">
                <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-400">
                  Capture Archive
                </p>
                <h3 className="font-display text-2xl leading-tight tracking-tight text-slate-950">
                  单商品抓图存档
                </h3>
                <p className="max-w-2xl text-sm leading-6 text-slate-600">
                  保存单商品流程里的窗口抓图和关键 OCR ROI，目录按每次会话拆分。
                </p>
              </div>

              <button
                type="button"
                role="switch"
                aria-checked={captureArchiveEnabled}
                aria-label="切换单商品抓图存档"
                onClick={() => void toggleCaptureArchive()}
                disabled={captureArchiveSaving || isRunning}
                className={cn(
                  "inline-flex min-h-12 items-center gap-3 rounded-full border px-4 py-2 text-sm font-medium transition",
                  captureArchiveEnabled
                    ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                    : "border-slate-200 bg-white text-slate-600",
                  (captureArchiveSaving || isRunning) && "cursor-not-allowed opacity-60",
                )}
              >
                <span
                  className={cn(
                    "relative inline-flex h-7 w-12 shrink-0 rounded-full transition",
                    captureArchiveEnabled ? "bg-emerald-500" : "bg-slate-300",
                  )}
                >
                  <span
                    className={cn(
                      "absolute top-1 size-5 rounded-full bg-white shadow-sm transition",
                      captureArchiveEnabled ? "left-6" : "left-1",
                    )}
                  />
                </span>
                <span>
                  {captureArchiveSaving
                    ? "保存中..."
                    : captureArchiveEnabled
                      ? "已开启"
                      : "已关闭"}
                </span>
              </button>
            </div>

            <InlineNote tone={captureArchiveMessage ? captureArchiveMessageTone : "slate"}>
              {captureArchiveMessage || `抓图将保存到 ${captureArchiveDir}。运行中不可切换，启动前设置生效。`}
            </InlineNote>
          </CardContent>
        </Card>

        <Card className="overflow-visible rounded-[36px] border-white/60 bg-white/72 shadow-none backdrop-blur-sm">
          <CardContent className="grid gap-12 p-6 md:p-10">
            <section className="grid gap-8">
              <div className="space-y-2">
                <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-400">
                  Task
                </p>
                <h3 className="font-display text-3xl leading-tight tracking-tight text-slate-950">
                  任务对象与策略
                </h3>
              </div>

              <div className="grid gap-10 lg:grid-cols-[minmax(0,1.2fr)_280px]">
                <div className="space-y-2">
                  <div className="space-y-2">
                    <Label className="text-sm text-slate-500">任务对象</Label>
                    <Select
                      value={draft.itemId || undefined}
                      onValueChange={(value) => {
                        const goods = goodsMap.get(value)
                        if (!goods) return
                        setDraft({
                          ...draft,
                          itemId: goods.id,
                          itemName: goods.name,
                          purchased: draft.itemId === goods.id ? draft.purchased : 0,
                        })
                      }}
                    >
                      <SelectTrigger className="h-14 rounded-none border-x-0 border-t-0 border-b border-slate-200 bg-transparent px-0 text-lg shadow-none focus:ring-0 focus:ring-offset-0">
                        <SelectValue placeholder="请选择物品" />
                      </SelectTrigger>
                      <SelectContent>
                        {bootstrap.goods.map((item) => (
                          <SelectItem key={item.id} value={item.id}>
                            {item.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>

                <div className="space-y-6 border-t border-black/5 pt-6 lg:border-l lg:border-t-0 lg:pl-8 lg:pt-0">
                  <div className="space-y-2">
                    <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-400">
                      Limit
                    </p>
                    <h4 className="text-lg font-semibold text-slate-900">停止条件</h4>
                  </div>
                  <FormNumber
                    label="总购买数量"
                    hint="0 表示不按数量自动停。"
                    value={draft.targetTotal}
                    onChange={(value) => setDraft({ ...draft, targetTotal: value })}
                  />
                </div>
              </div>

              <div className="grid gap-8 border-t border-black/5 pt-10 md:grid-cols-2">
                <StrategyEditor
                  title="补货策略"
                  subtitle="优先执行"
                  tone="emerald"
                  priceLabel="补货最高单价"
                  priceValue={draft.restockPrice}
                  premiumValue={draft.restockPremiumPct}
                  ceiling={restockCeiling}
                  onPriceChange={(value) => setDraft({ ...draft, restockPrice: value })}
                  onPremiumChange={(value) =>
                    setDraft({ ...draft, restockPremiumPct: value })
                  }
                />
                <StrategyEditor
                  title="普通购买"
                  subtitle="补货未命中时执行"
                  tone="amber"
                  priceLabel="普通最高单价"
                  priceValue={draft.priceThreshold}
                  premiumValue={draft.pricePremiumPct}
                  ceiling={normalCeiling}
                  onPriceChange={(value) => setDraft({ ...draft, priceThreshold: value })}
                  onPremiumChange={(value) =>
                    setDraft({ ...draft, pricePremiumPct: value })
                  }
                />
              </div>
            </section>
          </CardContent>
        </Card>
      </div>

      <Dialog open={logDrawerOpen} onOpenChange={setLogDrawerOpen}>
        <DialogContent className="left-1/2 top-auto bottom-0 max-w-6xl translate-x-[-50%] translate-y-0 gap-0 rounded-b-none rounded-t-[32px] border-b-0 p-0">
          <DialogHeader className="border-b border-black/5 px-6 py-5">
            <div className="flex items-start justify-between gap-4">
              <div className="space-y-2">
                <DialogTitle className="font-display text-3xl tracking-tight">
                  运行日志
                </DialogTitle>
                <DialogDescription className="text-sm leading-6">
                  底部抽屉只显示单商品抢购的最近日志。
                </DialogDescription>
              </div>
              <Button
                size="icon"
                variant="ghost"
                onClick={() => setLogsClearedAt(Date.now())}
                aria-label="清空当前界面日志"
                className="rounded-full"
              >
                <Trash2 className="size-4" />
              </Button>
            </div>
          </DialogHeader>

          <ScrollArea className="h-[min(62vh,540px)] px-6 py-5">
            <div className="grid gap-3 pb-4">
              {visibleLogs.length > 0 ? (
                visibleLogs.map((log) => (
                  <div
                    key={`${log.createdAt}-${log.message}`}
                    className="grid gap-2 rounded-[24px] border border-black/5 bg-white/78 px-4 py-4 md:grid-cols-[170px_72px_1fr]"
                  >
                    <span className="text-xs text-muted-foreground">{log.createdAt}</span>
                    <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                      {log.level}
                    </span>
                    <p className="text-sm leading-6 text-slate-700">{log.message}</p>
                  </div>
                ))
              ) : (
                <div className="py-12 text-sm leading-6 text-slate-500">
                  当前没有单商品抢购日志。
                </div>
              )}
            </div>
          </ScrollArea>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function FormNumber({
  label,
  hint,
  value,
  step,
  onChange,
}: {
  label: string
  hint?: string
  value: number
  step?: string
  onChange: (value: number) => void
}) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Label className="text-sm text-slate-500">{label}</Label>
        {hint ? <HoverHint text={hint} /> : null}
      </div>
      <Input
        type="number"
        step={step}
        value={value}
        className="h-12 rounded-none border-x-0 border-t-0 border-b border-slate-200 bg-transparent px-0 text-lg shadow-none focus-visible:ring-0 focus-visible:ring-offset-0"
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </div>
  )
}

function StrategyEditor({
  title,
  subtitle,
  tone,
  priceLabel,
  priceValue,
  premiumValue,
  ceiling,
  onPriceChange,
  onPremiumChange,
}: {
  title: string
  subtitle: string
  tone: "emerald" | "amber"
  priceLabel: string
  priceValue: number
  premiumValue: number
  ceiling: number
  onPriceChange: (value: number) => void
  onPremiumChange: (value: number) => void
}) {
  const headingClass =
    tone === "emerald"
      ? "text-emerald-800"
      : "text-amber-800"
  const lineClass =
    tone === "emerald"
      ? "border-emerald-200/80"
      : "border-amber-200/90"
  const noteClass =
    tone === "emerald"
      ? "text-emerald-700/80"
      : "text-amber-700/80"

  return (
    <div className={cn("space-y-6 border-t pt-8 md:border-t-0 md:pt-0", lineClass)}>
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-1">
          <h3 className={cn("text-sm font-semibold uppercase tracking-[0.22em]", headingClass)}>
            {title}
          </h3>
          <p className="text-sm text-slate-500">{subtitle}</p>
        </div>
        <p className={cn("text-sm font-medium", noteClass)}>
          {ceiling > 0 ? `上限 ${ceiling}` : "已关闭"}
        </p>
      </div>
      <FormNumber
        label={priceLabel}
        hint="填 0 关闭。"
        value={priceValue}
        onChange={onPriceChange}
      />
      <FormNumber
        label="浮动百分比"
        hint="例如 5 代表 5%。"
        value={premiumValue}
        step="0.1"
        onChange={onPremiumChange}
      />
      <p className="text-sm leading-6 text-slate-500">
        {ceiling > 0 ? `${priceValue} + ${premiumValue}% = ${ceiling}` : "这一组策略当前不参与购买。"}
      </p>
    </div>
  )
}

function HoverHint({ text }: { text: string }) {
  return (
    <div className="group relative inline-flex">
      <span className="inline-flex size-5 items-center justify-center rounded-full border border-slate-200 text-slate-400">
        <CircleHelp className="size-3.5" />
      </span>
      <div className="pointer-events-none absolute left-1/2 top-full z-10 mt-2 hidden w-72 -translate-x-1/2 rounded-3xl border border-slate-200 bg-white px-4 py-3 text-sm leading-6 text-slate-700 shadow-lg group-hover:block group-focus-within:block">
        {text}
      </div>
    </div>
  )
}

function normalizeTask(task: SingleTaskRecord) {
  return JSON.stringify({
    itemId: task.itemId,
    itemName: task.itemName,
    enabled: true,
    priceThreshold: task.priceThreshold,
    pricePremiumPct: task.pricePremiumPct,
    restockPrice: task.restockPrice,
    restockPremiumPct: task.restockPremiumPct,
    targetTotal: task.targetTotal,
    purchased: task.purchased,
  })
}
