import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import {
  ChevronDown,
  ChevronUp,
  Eye,
  PencilLine,
  Play,
  Plus,
  Save,
  SlidersHorizontal,
  Square,
  Trash2,
} from "lucide-react"

import { api } from "@/lib/api"
import { useRuntimeStore } from "@/app/store"
import type { AppBootstrap, GoodsRecord, MultiTaskRecord } from "@/lib/types"
import { useRegisterShellToolbar } from "@/components/shell-toolbar"
import {
  InlineNote,
  PageHero,
  PageSurface,
  PageSurfaceContent,
  SectionHeading,
  minimalFieldClassName,
  minimalSelectTriggerClassName,
} from "@/components/minimal-page"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ScrollArea, ScrollBar } from "@/components/ui/scroll-area"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { cn } from "@/lib/utils"

const emptyMultiTask = (): MultiTaskRecord => ({
  id: crypto.randomUUID(),
  itemId: "",
  name: "",
  enabled: false,
  price: 0,
  premiumPct: 0,
  purchaseMode: "normal",
  targetTotal: 0,
  purchased: 0,
  orderIndex: 0,
  imagePath: "images/goods/_default.png",
  bigCategory: "",
  createdAt: new Date().toISOString(),
  updatedAt: new Date().toISOString(),
})

type NoticeTone = "slate" | "emerald" | "rose"

type MultiTimingDraft = {
  detailOpenSettleMs: string
  postCloseDetailMs: string
  postSuccessClickMs: string
  buyClickSettleMs: string
  buyResultTimeoutMs: string
  buyResultPollStepMs: string
}

function MultiToolbarActions({
  isRunning,
  enabledCount,
  onStartOrStop,
  onOpenTiming,
  onOpenLogs,
}: {
  isRunning: boolean
  enabledCount: number
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
          disabled={!isRunning && enabledCount === 0}
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
    [enabledCount, isRunning, onOpenLogs, onOpenTiming, onStartOrStop],
  )

  useRegisterShellToolbar(toolbarActions)
  return null
}

function moveIdOrder(ids: string[], id: string, direction: -1 | 1) {
  const index = ids.indexOf(id)
  if (index === -1) return ids
  const next = index + direction
  if (next < 0 || next >= ids.length) return ids
  const copy = [...ids]
  const [item] = copy.splice(index, 1)
  copy.splice(next, 0, item)
  return copy
}

type ModalState =
  | { open: false }
  | { open: true; mode: "create" | "edit"; draft: MultiTaskRecord }

export function MultiTasksPage() {
  const bootstrap = useRuntimeStore((state) => state.bootstrap)
  const runtime = useRuntimeStore((state) => state.runtime)
  const progress = useRuntimeStore((state) => state.progress)
  const queryClient = useQueryClient()
  const [modal, setModal] = useState<ModalState>({ open: false })
  const [timingDialogOpen, setTimingDialogOpen] = useState(false)
  const [timingDraft, setTimingDraft] = useState<MultiTimingDraft | null>(null)
  const [timingMessage, setTimingMessage] = useState("")
  const [timingMessageTone, setTimingMessageTone] = useState<NoticeTone>("slate")
  const [captureArchiveSaving, setCaptureArchiveSaving] = useState(false)
  const [captureArchiveOverride, setCaptureArchiveOverride] = useState<boolean | null>(null)
  const [captureArchiveMessage, setCaptureArchiveMessage] = useState("")
  const [captureArchiveMessageTone, setCaptureArchiveMessageTone] =
    useState<NoticeTone>("slate")
  const [logDrawerOpen, setLogDrawerOpen] = useState(false)
  const [logsClearedAt, setLogsClearedAt] = useState<number | null>(null)
  const lastSavedTimingRef = useRef("")

  const goodsMap = useMemo(() => {
    const map = new Map<string, GoodsRecord>()
    bootstrap?.goods.forEach((item) => map.set(item.id, item))
    return map
  }, [bootstrap?.goods])

  useEffect(() => {
    if (!bootstrap) return
    const nextTimingDraft = multiTimingDraftFromConfig(bootstrap)
    setTimingDraft(nextTimingDraft)
    lastSavedTimingRef.current = normalizeMultiTimingDraft(nextTimingDraft)
  }, [bootstrap])

  useEffect(() => {
    setCaptureArchiveOverride(null)
  }, [bootstrap?.config.debug.saveMultiCaptureImages])

  const multiLogs = useMemo(
    () =>
      progress.filter((item) => {
        if (item.mode !== "multi") {
          return false
        }
        if (runtime.mode === "multi" && runtime.sessionId) {
          return item.sessionId === runtime.sessionId
        }
        return true
      }),
    [progress, runtime.mode, runtime.sessionId],
  )

  const visibleLogs = useMemo(() => {
    const filtered = logsClearedAt
      ? multiLogs.filter((log) => {
          const ts = Date.parse(log.createdAt)
          return Number.isNaN(ts) || ts >= logsClearedAt
        })
      : multiLogs
    return filtered.slice(0, 80)
  }, [logsClearedAt, multiLogs])

  const isRunning = runtime.state === "running"
  const runtimeTone =
    runtime.state === "running"
      ? "default"
      : runtime.state === "failed"
        ? "destructive"
        : "outline"

  const startOrStop = async () => {
    if (!bootstrap) return
    if (isRunning) {
      await api.automationStop()
      await queryClient.invalidateQueries({ queryKey: ["bootstrap"] })
      return
    }
    await api.automationStartMulti()
    await queryClient.invalidateQueries({ queryKey: ["bootstrap"] })
  }

  const saveTimingDraft = useCallback(async (nextDraft: MultiTimingDraft) => {
    if (!bootstrap) return null
    const parsedDraft = parseMultiTimingDraft(nextDraft)
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
      },
    })
    const savedTimingDraft = multiTimingDraftFromConfig({ config: saved })
    const requestedTimingDraft = normalizeMultiTimingDraft(nextDraft)
    queryClient.setQueryData<AppBootstrap>(["bootstrap"], (current) =>
      current
        ? {
            ...current,
            config: saved,
          }
        : current,
    )
    lastSavedTimingRef.current = normalizeMultiTimingDraft(savedTimingDraft)
    setTimingDraft((current) =>
      current && normalizeMultiTimingDraft(current) === requestedTimingDraft ? savedTimingDraft : current,
    )
    setTimingMessageTone("emerald")
    setTimingMessage("运行参数已自动保存，新会话会按最新参数启动。")
    return saved
  }, [bootstrap, queryClient])

  useEffect(() => {
    if (!timingDraft) return
    const normalized = normalizeMultiTimingDraft(timingDraft)
    if (normalized === lastSavedTimingRef.current) return
    const timer = window.setTimeout(() => {
      void saveTimingDraft(timingDraft).catch((error) => {
        setTimingMessageTone("rose")
        setTimingMessage(`运行参数未保存：${formatErrorMessage(error)}`)
      })
    }, 450)
    return () => window.clearTimeout(timer)
  }, [saveTimingDraft, timingDraft])

  const toggleCaptureArchive = async () => {
    if (!bootstrap) return
    const captureArchiveEnabled =
      captureArchiveOverride ?? bootstrap.config.debug.saveMultiCaptureImages
    const captureArchiveDir = `${bootstrap.paths.debugDir}\\multi-captures\\<sessionId>`
    if (captureArchiveSaving || isRunning) {
      return
    }
    const nextEnabled = !captureArchiveEnabled
    setCaptureArchiveOverride(nextEnabled)
    setCaptureArchiveSaving(true)
    try {
      const saved = await api.configSave({
        ...bootstrap.config,
        debug: {
          ...bootstrap.config.debug,
          saveMultiCaptureImages: nextEnabled,
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
      setCaptureArchiveMessageTone("emerald")
      setCaptureArchiveMessage(
        nextEnabled
          ? `已开启商品抓图存档。新会话会保存到 ${captureArchiveDir}`
          : "已关闭商品抓图存档。后续多商品会话不再自动保存抓图。",
      )
    } catch (error) {
      setCaptureArchiveOverride(null)
      setCaptureArchiveMessageTone("rose")
      setCaptureArchiveMessage(`商品抓图存档设置保存失败：${formatErrorMessage(error)}`)
    } finally {
      setCaptureArchiveSaving(false)
    }
  }

  if (!bootstrap) return null

  const enabledCount = bootstrap.multiTasks.filter((item) => item.enabled).length
  const captureArchiveEnabled =
    captureArchiveOverride ?? bootstrap.config.debug.saveMultiCaptureImages
  const captureArchiveDir = `${bootstrap.paths.debugDir}\\multi-captures\\<sessionId>`

  const openCreate = () =>
    setModal({
      open: true,
      mode: "create",
      draft: {
        ...emptyMultiTask(),
        orderIndex: bootstrap.multiTasks.length,
      },
    })

  const openEdit = (task: MultiTaskRecord) =>
    setModal({
      open: true,
      mode: "edit",
      draft: structuredClone(task),
    })

  const saveTask = async () => {
    if (!modal.open) return
    const boundGoods = goodsMap.get(modal.draft.itemId)
    await api.multiTasksSave({
      ...modal.draft,
      name: boundGoods?.name ?? modal.draft.name,
      imagePath: boundGoods?.imagePath ?? modal.draft.imagePath,
      bigCategory: boundGoods?.bigCategory ?? modal.draft.bigCategory,
      updatedAt: new Date().toISOString(),
    })
    setModal({ open: false })
    await queryClient.invalidateQueries({ queryKey: ["bootstrap"] })
  }

  const deleteTask = async (id: string) => {
    await api.multiTasksDelete(id)
    await queryClient.invalidateQueries({ queryKey: ["bootstrap"] })
  }

  const reorder = async (id: string, direction: -1 | 1) => {
    const ids = moveIdOrder(
      bootstrap.multiTasks.map((item) => item.id),
      id,
      direction,
    )
    await api.multiTasksReorder(ids)
    await queryClient.invalidateQueries({ queryKey: ["bootstrap"] })
  }

  const toggleTaskEnabled = async (task: MultiTaskRecord) => {
    const boundGoods = goodsMap.get(task.itemId)
    await api.multiTasksSave({
      ...task,
      enabled: !task.enabled,
      name: boundGoods?.name ?? task.name,
      imagePath: boundGoods?.imagePath ?? task.imagePath,
      bigCategory: boundGoods?.bigCategory ?? task.bigCategory,
      updatedAt: new Date().toISOString(),
    })
    await queryClient.invalidateQueries({ queryKey: ["bootstrap"] })
  }

  return (
    <div className="grid gap-10">
      <MultiToolbarActions
        isRunning={isRunning}
        enabledCount={enabledCount}
        onStartOrStop={() => void startOrStop()}
        onOpenTiming={() => setTimingDialogOpen(true)}
        onOpenLogs={() => setLogDrawerOpen(true)}
      />

      <PageHero
        eyebrow="Favorites"
        badges={
          <>
            <Badge variant={runtimeTone}>{runtime.state}</Badge>
            <Badge variant="outline">已启用 {enabledCount}</Badge>
          </>
        }
        title="收藏商品抢购"
      />

      <PageSurface>
        <PageSurfaceContent className="gap-8">
          <SectionHeading
            eyebrow="Queue"
            title="任务清单"
            description="新建、排序和编辑都集中在一个清爽的列表里。"
            actions={
              <Button onClick={openCreate}>
                <Plus className="mr-2 size-4" />
                新建任务
              </Button>
            }
          />

          <ScrollArea className="rounded-[32px] border border-black/5 bg-white/55">
            <Table className="min-w-[960px]">
              <TableHeader>
                <TableRow>
                  <TableHead>物品</TableHead>
                  <TableHead>模式</TableHead>
                  <TableHead>阈值</TableHead>
                  <TableHead>分类</TableHead>
                  <TableHead>进度</TableHead>
                  <TableHead className="w-[360px] text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {bootstrap.multiTasks.length > 0 ? (
                  bootstrap.multiTasks.map((task) => (
                    <TableRow key={task.id}>
                      <TableCell className="py-5">
                        <div className="space-y-1">
                          <p className="font-medium text-slate-900">{task.name}</p>
                          <p className="text-xs text-slate-500">
                            {task.enabled ? "已启用" : "已停用"}
                          </p>
                        </div>
                      </TableCell>
                      <TableCell className="py-5 text-slate-700">{task.purchaseMode}</TableCell>
                      <TableCell className="py-5 text-slate-700">
                        {task.price} / {task.premiumPct}%
                      </TableCell>
                      <TableCell className="py-5 text-slate-700">
                        {task.bigCategory || "--"}
                      </TableCell>
                      <TableCell className="py-5 text-slate-700">
                        {task.purchased}/{task.targetTotal || "-"}
                      </TableCell>
                      <TableCell className="py-5">
                        <div className="flex justify-end gap-2">
                          <Button
                            size="sm"
                            variant={task.enabled ? "secondary" : "outline"}
                            onClick={() => void toggleTaskEnabled(task)}
                          >
                            {task.enabled ? "停用" : "启用"}
                          </Button>
                          <Button
                            size="icon"
                            variant="ghost"
                            onClick={() => void reorder(task.id, -1)}
                            aria-label="上移任务"
                          >
                            <ChevronUp className="size-4" />
                          </Button>
                          <Button
                            size="icon"
                            variant="ghost"
                            onClick={() => void reorder(task.id, 1)}
                            aria-label="下移任务"
                          >
                            <ChevronDown className="size-4" />
                          </Button>
                          <Button size="sm" variant="secondary" onClick={() => openEdit(task)}>
                            <PencilLine className="mr-2 size-4" />
                            编辑
                          </Button>
                          <Button
                            size="sm"
                            variant="destructive"
                            onClick={() => void deleteTask(task.id)}
                          >
                            <Trash2 className="mr-2 size-4" />
                            删除
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))
                ) : (
                  <TableRow>
                    <TableCell colSpan={6} className="py-16 text-center text-sm text-slate-500">
                      还没有收藏商品任务，先新建一个队列入口。
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
            <ScrollBar orientation="horizontal" />
          </ScrollArea>
        </PageSurfaceContent>
      </PageSurface>

      <Dialog
        open={modal.open}
        onOpenChange={(open) => !open && setModal({ open: false })}
      >
        <DialogContent className="max-w-4xl">
          {modal.open ? (
            <>
              <DialogHeader className="space-y-3">
                <DialogTitle className="font-display text-3xl tracking-tight">
                  {modal.mode === "create" ? "新建收藏商品任务" : "编辑收藏商品任务"}
                </DialogTitle>
                <DialogDescription className="max-w-2xl text-sm leading-6">
                  只调整与执行直接相关的字段。保存后任务会立即回到统一队列中。
                </DialogDescription>
              </DialogHeader>

              <div className="grid gap-8 py-2 md:grid-cols-2">
                <div className="space-y-3">
                  <Label>绑定物品</Label>
                  <Select
                    value={modal.draft.itemId || undefined}
                    onValueChange={(value) => {
                      const goods = goodsMap.get(value)
                      if (!goods) return
                      setModal({
                        ...modal,
                        draft: {
                          ...modal.draft,
                          itemId: goods.id,
                          name: goods.name,
                          imagePath: goods.imagePath,
                          bigCategory: goods.bigCategory,
                        },
                      })
                    }}
                  >
                    <SelectTrigger className={minimalSelectTriggerClassName}>
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

                <FormNumber
                  label="价格阈值"
                  value={modal.draft.price}
                  onChange={(value) =>
                    setModal({
                      ...modal,
                      draft: { ...modal.draft, price: value },
                    })
                  }
                />
                <FormNumber
                  label="浮动 %"
                  value={modal.draft.premiumPct}
                  step="0.1"
                  onChange={(value) =>
                    setModal({
                      ...modal,
                      draft: { ...modal.draft, premiumPct: value },
                    })
                  }
                />

                <div className="space-y-3">
                  <Label>购买模式</Label>
                  <Select
                    value={modal.draft.purchaseMode}
                    onValueChange={(value) =>
                      setModal({
                        ...modal,
                        draft: { ...modal.draft, purchaseMode: value },
                      })
                    }
                  >
                    <SelectTrigger className={minimalSelectTriggerClassName}>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="normal">normal</SelectItem>
                      <SelectItem value="restock">restock</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <FormNumber
                  label="目标数量"
                  value={modal.draft.targetTotal}
                  onChange={(value) =>
                    setModal({
                      ...modal,
                      draft: { ...modal.draft, targetTotal: value },
                    })
                  }
                />
                <FormNumber
                  label="已购数量"
                  value={modal.draft.purchased}
                  onChange={(value) =>
                    setModal({
                      ...modal,
                      draft: { ...modal.draft, purchased: value },
                    })
                  }
                />

                <div className="space-y-3">
                  <Label>大类</Label>
                  <Input
                    className={minimalFieldClassName}
                    readOnly
                    value={modal.draft.bigCategory}
                  />
                </div>
              </div>

              <DialogFooter>
                <Button variant="secondary" onClick={() => setModal({ open: false })}>
                  取消
                </Button>
                <Button onClick={() => void saveTask()}>
                  <Save className="mr-2 size-4" />
                  保存任务
                </Button>
              </DialogFooter>
            </>
          ) : null}
        </DialogContent>
      </Dialog>

      <Dialog open={timingDialogOpen} onOpenChange={setTimingDialogOpen}>
        <DialogContent className="max-h-[92vh] max-w-3xl gap-0 rounded-[32px] p-0 !overflow-hidden">
          <DialogHeader className="shrink-0 border-b border-black/5 bg-white/88 px-6 py-5 pr-20 backdrop-blur-xl">
            <DialogTitle className="font-display text-3xl tracking-tight">
              收藏商品运行参数
            </DialogTitle>
            <DialogDescription className="text-sm leading-6">
              这里调整收藏商品抢购的抓图存档、详情稳定与购买结果识别时序。输入后会自动保存。
            </DialogDescription>
          </DialogHeader>

          <ScrollArea className="max-h-[70vh]">
            <div className="grid gap-6 px-6 py-6 md:px-8 md:py-8">
              <div className="grid gap-4 rounded-[28px] border border-black/5 bg-white/60 px-5 py-5">
                <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
                  <div className="space-y-2">
                    <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-400">
                      Capture Archive
                    </p>
                    <h3 className="font-display text-2xl leading-tight tracking-tight text-slate-950">
                      商品抓图存档
                    </h3>
                    <p className="max-w-2xl text-sm leading-6 text-slate-600">
                      保存多商品流程里的商品卡片抓图和关键 OCR ROI，目录按每次会话拆分。
                    </p>
                  </div>

                  <button
                    type="button"
                    role="switch"
                    aria-checked={captureArchiveEnabled}
                    aria-label="切换商品抓图存档"
                    onClick={() => void toggleCaptureArchive()}
                    disabled={captureArchiveSaving || isRunning}
                    className={cn(
                      "inline-flex min-h-12 items-center gap-3 self-start rounded-full border px-4 py-2 text-sm font-medium transition md:self-center",
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
              </div>

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
                        : current,
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
                        : current,
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
                        : current,
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
                        : current,
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
                        : current,
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
                        : current,
                    )
                  }
                />
              </div>

              {timingMessage || isRunning ? (
                <InlineNote tone={timingMessage ? timingMessageTone : "slate"}>
                  {timingMessage || "参数会实时保存，但当前收藏商品会话已经持有启动时的配置；修改会在下一次点击启动后生效。"}
                </InlineNote>
              ) : null}
            </div>
          </ScrollArea>
        </DialogContent>
      </Dialog>

      <Dialog open={logDrawerOpen} onOpenChange={setLogDrawerOpen}>
        <DialogContent className="left-1/2 top-auto bottom-0 max-w-6xl translate-x-[-50%] translate-y-0 gap-0 rounded-b-none rounded-t-[32px] border-b-0 p-0">
          <DialogHeader className="border-b border-black/5 px-6 py-5 pr-20">
            <div className="flex items-start gap-4">
              <div className="space-y-2">
                <DialogTitle className="font-display text-3xl tracking-tight">运行日志</DialogTitle>
                <DialogDescription className="text-sm leading-6">
                  底部抽屉只显示多商品抢购的最近事件。
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
                visibleLogs.map((item) => (
                  <div
                    key={`${item.createdAt}-${item.message}`}
                    className="grid gap-2 rounded-[24px] border border-black/5 bg-white/78 px-4 py-4 md:grid-cols-[170px_72px_1fr]"
                  >
                    <span className="text-xs text-slate-500">{item.createdAt}</span>
                    <span className="text-xs font-semibold uppercase tracking-[0.18em] text-emerald-700">
                      {item.level}
                    </span>
                    <div className="space-y-1">
                      {item.step ? (
                        <p className="text-xs uppercase tracking-[0.18em] text-slate-400">
                          {item.step}
                        </p>
                      ) : null}
                      <p className="text-sm leading-6 text-slate-700">{item.message}</p>
                    </div>
                  </div>
                ))
              ) : (
                <div className="py-12 text-sm text-slate-500">当前还没有多商品运行日志。</div>
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
  value,
  step,
  onChange,
}: {
  label: string
  value: number
  step?: string
  onChange: (value: number) => void
}) {
  return (
    <div className="space-y-3">
      <Label>{label}</Label>
      <Input
        className={minimalFieldClassName}
        type="number"
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
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
      <Label>{label}</Label>
      <Input
        className={minimalFieldClassName}
        type="number"
        min={min}
        step={step}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
      {hint ? <p className="text-xs leading-5 text-slate-400">{hint}</p> : null}
      {suffix ? <p className="text-xs leading-5 text-slate-400">当前单位：{suffix}</p> : null}
    </div>
  )
}

function multiTimingDraftFromConfig(source: {
  config: AppBootstrap["config"] | MultiTaskPageConfig
}) {
  const tuning = source.config.multiSnipeTuning
  return {
    detailOpenSettleMs: String(Math.round(tuning.detailOpenSettleSec * 1000)),
    postCloseDetailMs: String(Math.round(tuning.postCloseDetailSec * 1000)),
    postSuccessClickMs: String(Math.round(tuning.postSuccessClickSec * 1000)),
    buyClickSettleMs: String(Math.round(tuning.buyClickSettleSec * 1000)),
    buyResultTimeoutMs: String(Math.round(tuning.buyResultTimeoutSec * 1000)),
    buyResultPollStepMs: String(Math.round(tuning.buyResultPollStepSec * 1000)),
  }
}

type MultiTaskPageConfig = {
  multiSnipeTuning: {
    detailOpenSettleSec: number
    postCloseDetailSec: number
    postSuccessClickSec: number
    buyClickSettleSec: number
    buyResultTimeoutSec: number
    buyResultPollStepSec: number
  }
}

function normalizeMultiTimingDraft(draft: MultiTimingDraft) {
  return JSON.stringify(draft)
}

function parseMultiTimingDraft(draft: MultiTimingDraft) {
  return {
    detailOpenSettleMs: parseRequiredNumber(draft.detailOpenSettleMs, "详情打开稳定等待"),
    postCloseDetailMs: parseRequiredNumber(draft.postCloseDetailMs, "关闭详情后等待"),
    postSuccessClickMs: parseRequiredNumber(draft.postSuccessClickMs, "成功遮罩点击后等待"),
    buyClickSettleMs: parseRequiredNumber(draft.buyClickSettleMs, "购买点击后固定等待"),
    buyResultTimeoutMs: parseRequiredNumber(draft.buyResultTimeoutMs, "购买结果识别窗口"),
    buyResultPollStepMs: parseRequiredNumber(draft.buyResultPollStepMs, "购买结果轮询步进"),
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
