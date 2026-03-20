import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import {
  CircleHelp,
  Eye,
  Play,
  SlidersHorizontal,
  Square,
  Trash2,
} from "lucide-react"

import { useRuntimeStore } from "@/app/store"
import { api } from "@/lib/api"
import { getSingleStartBlockReason } from "@/lib/runtime-preflight"
import type { AppBootstrap, GoodsRecord, SingleTaskRecord } from "@/lib/types"
import { cn } from "@/lib/utils"
import { useRegisterShellToolbar } from "@/components/shell-toolbar"
import { DebugModeCard } from "@/components/debug-mode-card"
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
import { SpinnerNumberInput } from "@/components/ui/spinner-number-input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

type NoticeTone = "slate" | "emerald" | "rose"
type SingleTimingDraft = {
  detailOpenSettleMs: string
  postCloseDetailMs: string
  postSuccessClickMs: string
  buyClickSettleMs: string
  buyResultTimeoutMs: string
  buyResultPollStepMs: string
  roundCooldownEveryNRounds: string
  roundCooldownMinutes: string
  restockRetriggerWindowMinutes: string
  restockMissCooldownMinutes: string
}

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

function SingleToolbarActions({
  isRunning,
  startBlockedReason,
  onStartOrStop,
  onOpenTiming,
  onOpenLogs,
}: {
  isRunning: boolean
  startBlockedReason: string | null
  onStartOrStop: () => void
  onOpenTiming: () => void
  onOpenLogs: () => void
}) {
  const toolbarActions = useMemo(
    () => (
      <>
        <Button
          size="sm"
          variant={isRunning ? "destructive" : "default"}
          onClick={onStartOrStop}
          disabled={!isRunning && Boolean(startBlockedReason)}
          title={startBlockedReason ?? undefined}
          className="min-w-24 rounded-lg"
        >
          {isRunning ? (
            <>
              <Square className="size-4" />
              终止
            </>
          ) : (
            <>
              <Play className="size-4" />
              启动
            </>
          )}
        </Button>
        <Button
          size="sm"
          variant="secondary"
          onClick={onOpenTiming}
          className="rounded-lg border-white/0 bg-transparent hover:bg-white"
        >
          <SlidersHorizontal className="size-4" />
          运行参数
        </Button>
        <Button
          size="sm"
          variant="secondary"
          onClick={onOpenLogs}
          className="rounded-lg border-white/0 bg-transparent hover:bg-white"
        >
          <Eye className="size-4" />
          运行日志
        </Button>
      </>
    ),
    [isRunning, onOpenLogs, onOpenTiming, onStartOrStop, startBlockedReason],
  )

  useRegisterShellToolbar(toolbarActions)
  return null
}

export function SingleTasksPage() {
  const bootstrap = useRuntimeStore((state) => state.bootstrap)
  const runtime = useRuntimeStore((state) => state.runtime)
  const logs = useRuntimeStore((state) => state.logs)
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<SingleTaskRecord | null>(null)
  const [timingDialogOpen, setTimingDialogOpen] = useState(false)
  const [timingDraft, setTimingDraft] = useState<SingleTimingDraft | null>(null)
  const [timingMessage, setTimingMessage] = useState("")
  const [timingMessageTone, setTimingMessageTone] = useState<NoticeTone>("slate")
  const [debugModeSaving, setDebugModeSaving] = useState(false)
  const [debugModeOverride, setDebugModeOverride] = useState<boolean | null>(null)
  const [debugModeMessage, setDebugModeMessage] = useState("")
  const [debugModeMessageTone, setDebugModeMessageTone] = useState<NoticeTone>("slate")
  const [logDrawerOpen, setLogDrawerOpen] = useState(false)
  const [logsClearedAt, setLogsClearedAt] = useState<number | null>(null)
  const lastSavedRef = useRef("")
  const lastSavedTimingRef = useRef("")

  useEffect(() => {
    if (!bootstrap) return
    const current = bootstrap.singleTasks[0]
    const nextDraft = current ? structuredClone(current) : emptySingleTask()
    setDraft(nextDraft)
    lastSavedRef.current = normalizeTask(nextDraft)
    const nextTimingDraft = timingDraftFromConfig(bootstrap)
    setTimingDraft(nextTimingDraft)
    lastSavedTimingRef.current = normalizeTimingDraft(nextTimingDraft)
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

  const saveTimingDraft = useCallback(async (nextDraft: SingleTimingDraft) => {
    if (!bootstrap) return null
    const parsedDraft = parseTimingDraft(nextDraft)
    const saved = await api.configSave({
      ...bootstrap.config,
      multiSnipeTuning: {
        ...bootstrap.config.multiSnipeTuning,
        detailOpenSettleSec: roundMs(parsedDraft.detailOpenSettleMs) / 1000,
        postCloseDetailSec: roundMs(parsedDraft.postCloseDetailMs) / 1000,
        postSuccessClickSec: roundMs(parsedDraft.postSuccessClickMs) / 1000,
        buyClickSettleSec: roundMs(parsedDraft.buyClickSettleMs) / 1000,
        buyResultTimeoutSec: roundMs(parsedDraft.buyResultTimeoutMs) / 1000,
        buyResultPollStepSec: roundMs(parsedDraft.buyResultPollStepMs) / 1000,
        roundCooldownEveryNRounds: roundCount(parsedDraft.roundCooldownEveryNRounds),
        roundCooldownMinutes: roundMinutes(parsedDraft.roundCooldownMinutes),
        restockRetriggerWindowMinutes: roundMinutes(parsedDraft.restockRetriggerWindowMinutes),
        restockMissCooldownMinutes: roundMinutes(parsedDraft.restockMissCooldownMinutes),
      },
    })
    const savedTimingDraft = timingDraftFromConfig({ config: saved })
    const requestedTimingDraft = normalizeTimingDraft(nextDraft)
    queryClient.setQueryData<AppBootstrap>(["bootstrap"], (current) =>
      current
        ? {
            ...current,
            config: saved,
          }
        : current,
    )
    lastSavedTimingRef.current = normalizeTimingDraft(savedTimingDraft)
    setTimingDraft((current) =>
      current && normalizeTimingDraft(current) === requestedTimingDraft ? savedTimingDraft : current
    )
    setTimingMessageTone("emerald")
    setTimingMessage("运行参数已自动保存，新会话会按最新参数启动。")
    return saved
  }, [bootstrap, queryClient])

  useEffect(() => {
    if (!timingDraft) return
    const normalized = normalizeTimingDraft(timingDraft)
    if (normalized === lastSavedTimingRef.current) return
    const timer = window.setTimeout(() => {
      void saveTimingDraft(timingDraft).catch((error) => {
        setTimingMessageTone("rose")
        setTimingMessage(`运行参数未保存：${formatErrorMessage(error)}`)
      })
    }, 450)
    return () => window.clearTimeout(timer)
  }, [saveTimingDraft, timingDraft])

  useEffect(() => {
    setDebugModeOverride(null)
  }, [bootstrap?.config.debug.singleEnabled])

  const toggleDebugMode = async () => {
    if (!bootstrap) return
    const debugModeEnabled = debugModeOverride ?? bootstrap.config.debug.singleEnabled
    const debugDir = `${bootstrap.paths.debugDir}\\single\\<sessionId>`
    if (debugModeSaving || isRunning) {
      return
    }
    const nextEnabled = !debugModeEnabled
    setDebugModeOverride(nextEnabled)
    setDebugModeSaving(true)
    try {
      const saved = await api.configSave({
        ...bootstrap.config,
        debug: {
          ...bootstrap.config.debug,
          singleEnabled: nextEnabled,
        },
      })
      queryClient.setQueryData<AppBootstrap>(["bootstrap"], (current) =>
        current
          ? {
              ...current,
              config: saved,
            }
          : current,
      )
      setDebugModeMessageTone("emerald")
      setDebugModeMessage(
        nextEnabled
          ? `已开启单商品调试模式。新会话会写入 ${debugDir}`
          : "已关闭单商品调试模式。后续会话不再生成调试图。",
      )
    } catch (error) {
      setDebugModeOverride(null)
      setDebugModeMessageTone("rose")
      setDebugModeMessage(`单商品调试模式保存失败：${formatErrorMessage(error)}`)
    } finally {
      setDebugModeSaving(false)
    }
  }

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

  const isRunning = runtime.state === "running"
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
  const debugModeEnabled = debugModeOverride ?? bootstrap.config.debug.singleEnabled
  const debugDir = `${bootstrap.paths.debugDir}\\single\\<sessionId>`
  const startBlockedReason = getSingleStartBlockReason(bootstrap, draft.itemId)

  const startOrStop = async () => {
    if (isRunning) {
      await api.automationStop()
      await queryClient.invalidateQueries({ queryKey: ["bootstrap"] })
      return
    }
    if (startBlockedReason) {
      return
    }
    await saveDraft()
    await api.automationStartSingle()
    await queryClient.invalidateQueries({ queryKey: ["bootstrap"] })
  }

  return (
    <div className="grid gap-10">
      <SingleToolbarActions
        isRunning={isRunning}
        startBlockedReason={startBlockedReason}
        onStartOrStop={() => void startOrStop()}
        onOpenTiming={() => setTimingDialogOpen(true)}
        onOpenLogs={() => setLogDrawerOpen(true)}
      />

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

            <div className="space-y-3">
              <h1 className="font-display text-4xl leading-tight tracking-tight text-slate-950 md:text-5xl">
                单商品抢购
              </h1>
            </div>

            {startBlockedReason ? (
              <InlineNote tone="rose">{startBlockedReason}</InlineNote>
            ) : null}
          </div>

        </section>

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

      <Dialog open={timingDialogOpen} onOpenChange={setTimingDialogOpen}>
        <DialogContent className="max-w-3xl rounded-[32px] p-0">
          <DialogHeader className="border-b border-black/5 px-6 py-5">
            <DialogTitle className="font-display text-3xl tracking-tight">
              单商品运行参数
            </DialogTitle>
            <DialogDescription className="text-sm leading-6">
              这里调整单商品抢购的调试模式、详情稳定、结果识别与遮罩关闭时序。输入后会自动保存。
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-6 px-6 py-6 md:px-8 md:py-8">
            <DebugModeCard
              title="单商品调试模式"
              description="按轮缓存模板识别、OCR、点击和输入的调试图，当前轮结束后一次性写入磁盘。"
              enabled={debugModeEnabled}
              saving={debugModeSaving}
              isRunning={isRunning}
              onToggle={() => void toggleDebugMode()}
              message={debugModeMessage}
              messageTone={debugModeMessageTone}
              defaultMessage={`调试图将写入 ${debugDir}\\round-0001-completed。运行中不可切换，启动前设置生效。`}
              ariaLabel="切换单商品调试模式"
            />

            <div className="grid gap-8 md:grid-cols-2">
                <FormNumberDraft
                  label="购买点击后固定等待"
                  hint="点击购买按钮后，先固定等待，再开始轮询购买成功/失败模板。填 0 关闭。"
                  value={timingDraft?.buyClickSettleMs ?? "50"}
                  min={0}
                  step="1"
                  suffix="ms"
                  onChange={(value) =>
                    setTimingDraft((current) =>
                      current
                        ? {
                            ...current,
                            buyClickSettleMs: value,
                          }
                        : current
                    )
                  }
                />
                <FormNumberDraft
                  label="详情打开稳定等待"
                  hint="点击商品卡片后，到开始判定详情已打开之间的等待。当前运行时下限为 50ms。"
                  value={timingDraft?.detailOpenSettleMs ?? "50"}
                  min={50}
                  step="1"
                  suffix="ms"
                  onChange={(value) =>
                    setTimingDraft((current) =>
                      current
                        ? {
                            ...current,
                            detailOpenSettleMs: value,
                          }
                        : current
                    )
                  }
                />
                <FormNumberDraft
                  label="关闭详情后等待"
                  hint="点击详情关闭按钮后的稳定等待。当前运行时下限为 50ms。"
                  value={timingDraft?.postCloseDetailMs ?? "50"}
                  min={50}
                  step="1"
                  suffix="ms"
                  onChange={(value) =>
                    setTimingDraft((current) =>
                      current
                        ? {
                            ...current,
                            postCloseDetailMs: value,
                          }
                        : current
                    )
                  }
                />
                <FormNumberDraft
                  label="成功遮罩点击后等待"
                  hint="购买成功后关闭遮罩，再进入下一步前的稳定等待。当前运行时下限为 50ms。"
                  value={timingDraft?.postSuccessClickMs ?? "50"}
                  min={50}
                  step="1"
                  suffix="ms"
                  onChange={(value) =>
                    setTimingDraft((current) =>
                      current
                        ? {
                            ...current,
                            postSuccessClickMs: value,
                          }
                        : current
                    )
                  }
                />
                <FormNumberDraft
                  label="购买结果识别窗口"
                  hint="点击购买后，在窗口内轮询 buy_ok / buy_fail。当前运行时下限为 250ms。"
                  value={timingDraft?.buyResultTimeoutMs ?? "350"}
                  min={250}
                  step="1"
                  suffix="ms"
                  onChange={(value) =>
                    setTimingDraft((current) =>
                      current
                        ? {
                            ...current,
                            buyResultTimeoutMs: value,
                          }
                        : current
                    )
                  }
                />
                <FormNumberDraft
                  label="购买结果轮询步进"
                  hint="识别窗口内每次重新检测 buy_ok / buy_fail 的间隔。当前运行时下限为 10ms。"
                  value={timingDraft?.buyResultPollStepMs ?? "10"}
                  min={10}
                  step="1"
                  suffix="ms"
                  onChange={(value) =>
                    setTimingDraft((current) =>
                      current
                        ? {
                            ...current,
                            buyResultPollStepMs: value,
                          }
                        : current
                    )
                  }
                />
              </div>

            <div className="grid gap-8 rounded-[28px] border border-black/5 bg-white/55 px-5 py-5 md:grid-cols-2">
                <FormNumberDraft
                  label="每 N 轮冷却"
                  hint="按成功进入详情并完成本轮判定计数。填 0 关闭。"
                  value={timingDraft?.roundCooldownEveryNRounds ?? "0"}
                  min={0}
                  step="1"
                  suffix="轮"
                  onChange={(value) =>
                    setTimingDraft((current) =>
                      current
                        ? {
                            ...current,
                            roundCooldownEveryNRounds: value,
                          }
                        : current
                    )
                  }
                />
                <FormNumberDraft
                  label="每轮冷却时长"
                  hint="达到上面的轮数后，保持 running 状态原地冷却的分钟数。填 0 关闭。"
                  value={timingDraft?.roundCooldownMinutes ?? "0"}
                  min={0}
                  step="0.1"
                  suffix="分钟"
                  onChange={(value) =>
                    setTimingDraft((current) =>
                      current
                        ? {
                            ...current,
                            roundCooldownMinutes: value,
                          }
                        : current
                    )
                  }
                />
                <FormNumberDraft
                  label="补货观察窗"
                  hint="某次进入补货模式后，若后续这段时间内没再次进入补货，就触发冷却。填 0 关闭。"
                  value={timingDraft?.restockRetriggerWindowMinutes ?? "0"}
                  min={0}
                  step="0.1"
                  suffix="分钟"
                  onChange={(value) =>
                    setTimingDraft((current) =>
                      current
                        ? {
                            ...current,
                            restockRetriggerWindowMinutes: value,
                          }
                        : current
                    )
                  }
                />
                <FormNumberDraft
                  label="补货缺失冷却时长"
                  hint="补货观察窗超时后，保持 running 状态冷却的分钟数。填 0 关闭。"
                  value={timingDraft?.restockMissCooldownMinutes ?? "0"}
                  min={0}
                  step="0.1"
                  suffix="分钟"
                  onChange={(value) =>
                    setTimingDraft((current) =>
                      current
                        ? {
                            ...current,
                            restockMissCooldownMinutes: value,
                          }
                        : current
                    )
                  }
                />
              </div>

            {timingMessage || isRunning ? (
              <InlineNote tone={timingMessage ? timingMessageTone : "slate"}>
                {timingMessage || "参数会实时保存，但当前单商品会话已经持有启动时的配置；修改会在下一次点击开始后生效。"}
              </InlineNote>
            ) : null}
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={logDrawerOpen} onOpenChange={setLogDrawerOpen}>
        <DialogContent className="left-1/2 top-auto bottom-0 max-w-6xl translate-x-[-50%] translate-y-0 gap-0 rounded-b-none rounded-t-[32px] border-b-0 p-0">
          <DialogHeader className="border-b border-black/5 px-6 py-5 pr-20">
            <div className="flex items-start gap-4">
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
                className="ml-auto shrink-0 rounded-full"
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
  min,
  step,
  suffix,
  spinnerOnly,
  onChange,
}: {
  label: string
  hint?: string
  value: number
  min?: number
  step?: string
  suffix?: string
  spinnerOnly?: boolean
  onChange: (value: number) => void
}) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Label className="text-sm text-slate-500">{label}</Label>
        {hint ? <HoverHint text={hint} /> : null}
      </div>
      {spinnerOnly ? (
        <SpinnerNumberInput
          min={min}
          step={step}
          value={value}
          className="h-12 rounded-none border-x-0 border-t-0 border-b border-slate-200 bg-transparent px-0 text-lg shadow-none focus-visible:ring-0 focus-visible:ring-offset-0"
          onChange={(event) => onChange(Number(event.target.value))}
        />
      ) : (
        <Input
          type="number"
          min={min}
          step={step}
          value={value}
          className="h-12 rounded-none border-x-0 border-t-0 border-b border-slate-200 bg-transparent px-0 text-lg shadow-none focus-visible:ring-0 focus-visible:ring-offset-0"
          onChange={(event) => onChange(Number(event.target.value))}
        />
      )}
      {suffix ? (
        <p className="text-xs leading-5 text-slate-400">当前单位：{suffix}</p>
      ) : null}
    </div>
  )
}

function FormNumberDraft({
  label,
  hint,
  value,
  min,
  step,
  suffix,
  onChange,
}: {
  label: string
  hint?: string
  value: string
  min?: number
  step?: string
  suffix?: string
  onChange: (value: string) => void
}) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Label className="text-sm text-slate-500">{label}</Label>
        {hint ? <HoverHint text={hint} /> : null}
      </div>
      <Input
        type="number"
        min={min}
        step={step}
        value={value}
        className="h-12 rounded-none border-x-0 border-t-0 border-b border-slate-200 bg-transparent px-0 text-lg shadow-none focus-visible:ring-0 focus-visible:ring-offset-0"
        onChange={(event) => onChange(event.target.value)}
      />
      {suffix ? (
        <p className="text-xs leading-5 text-slate-400">当前单位：{suffix}</p>
      ) : null}
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
        spinnerOnly
        onChange={onPriceChange}
      />
      <FormNumber
        label="浮动百分比"
        hint="例如 5 代表 5%。"
        value={premiumValue}
        step="0.1"
        spinnerOnly
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

function timingDraftFromConfig(source: { config: AppBootstrap["config"] | SingleTaskPageConfig }) {
  const tuning = source.config.multiSnipeTuning
  return {
    detailOpenSettleMs: String(Math.round(tuning.detailOpenSettleSec * 1000)),
    postCloseDetailMs: String(Math.round(tuning.postCloseDetailSec * 1000)),
    postSuccessClickMs: String(Math.round(tuning.postSuccessClickSec * 1000)),
    buyClickSettleMs: String(Math.round(tuning.buyClickSettleSec * 1000)),
    buyResultTimeoutMs: String(Math.round(tuning.buyResultTimeoutSec * 1000)),
    buyResultPollStepMs: String(Math.round(tuning.buyResultPollStepSec * 1000)),
    roundCooldownEveryNRounds: String(tuning.roundCooldownEveryNRounds),
    roundCooldownMinutes: formatMinutesDraft(tuning.roundCooldownMinutes),
    restockRetriggerWindowMinutes: formatMinutesDraft(tuning.restockRetriggerWindowMinutes),
    restockMissCooldownMinutes: formatMinutesDraft(tuning.restockMissCooldownMinutes),
  }
}

type SingleTaskPageConfig = {
  multiSnipeTuning: {
    detailOpenSettleSec: number
    postCloseDetailSec: number
    postSuccessClickSec: number
    buyClickSettleSec: number
    buyResultTimeoutSec: number
    buyResultPollStepSec: number
    roundCooldownEveryNRounds: number
    roundCooldownMinutes: number
    restockRetriggerWindowMinutes: number
    restockMissCooldownMinutes: number
  }
}

function normalizeTimingDraft(draft: SingleTimingDraft) {
  return JSON.stringify(draft)
}

function parseTimingDraft(draft: SingleTimingDraft) {
  return {
    detailOpenSettleMs: parseRequiredNumber(draft.detailOpenSettleMs, "详情打开稳定等待"),
    postCloseDetailMs: parseRequiredNumber(draft.postCloseDetailMs, "关闭详情后等待"),
    postSuccessClickMs: parseRequiredNumber(draft.postSuccessClickMs, "成功遮罩点击后等待"),
    buyClickSettleMs: parseRequiredNumber(draft.buyClickSettleMs, "购买点击后固定等待"),
    buyResultTimeoutMs: parseRequiredNumber(draft.buyResultTimeoutMs, "购买结果识别窗口"),
    buyResultPollStepMs: parseRequiredNumber(draft.buyResultPollStepMs, "购买结果轮询步进"),
    roundCooldownEveryNRounds: parseRequiredNumber(draft.roundCooldownEveryNRounds, "每 N 轮冷却"),
    roundCooldownMinutes: parseRequiredNumber(draft.roundCooldownMinutes, "每轮冷却时长"),
    restockRetriggerWindowMinutes: parseRequiredNumber(draft.restockRetriggerWindowMinutes, "补货观察窗"),
    restockMissCooldownMinutes: parseRequiredNumber(draft.restockMissCooldownMinutes, "补货缺失冷却时长"),
  }
}

function parseRequiredNumber(raw: string, label: string) {
  const trimmed = raw.trim()
  if (!trimmed) {
    throw new Error(`${label}不能为空`)
  }
  const value = Number(trimmed)
  if (!Number.isFinite(value)) {
    throw new Error(`${label}必须是数字`)
  }
  return value
}

function formatErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error)
}

function roundMs(value: number) {
  return Math.round(value)
}

function roundCount(value: number) {
  return Math.max(0, Math.round(value))
}

function roundMinutes(value: number) {
  return Math.max(0, Math.round(value * 10) / 10)
}

function formatMinutesDraft(value: number) {
  const rounded = roundMinutes(value)
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1)
}
