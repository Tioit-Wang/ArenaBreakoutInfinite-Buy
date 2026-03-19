import { useMemo, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import {
  ChevronDown,
  ChevronUp,
  Eye,
  PencilLine,
  Play,
  Plus,
  Save,
  Square,
  Trash2,
} from "lucide-react"

import { api } from "@/lib/api"
import { useRuntimeStore } from "@/app/store"
import type { GoodsRecord, MultiTaskRecord } from "@/lib/types"
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
import {
  PageHero,
  PageSurface,
  PageSurfaceContent,
  SectionHeading,
  minimalFieldClassName,
  minimalSelectTriggerClassName,
} from "@/components/minimal-page"

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
  const [logDrawerOpen, setLogDrawerOpen] = useState(false)
  const [logsClearedAt, setLogsClearedAt] = useState<number | null>(null)

  const goodsMap = useMemo(() => {
    const map = new Map<string, GoodsRecord>()
    bootstrap?.goods.forEach((item) => map.set(item.id, item))
    return map
  }, [bootstrap?.goods])

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

  if (!bootstrap) return null

  const enabledCount = bootstrap.multiTasks.filter((item) => item.enabled).length

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

  const isRunning = runtime.state === "running"
  const runtimeTone =
    runtime.state === "running"
      ? "default"
      : runtime.state === "failed"
        ? "destructive"
        : "outline"

  const startOrStop = async () => {
    if (isRunning) {
      await api.automationStop()
      await queryClient.invalidateQueries({ queryKey: ["bootstrap"] })
      return
    }
    await api.automationStartMulti()
    await queryClient.invalidateQueries({ queryKey: ["bootstrap"] })
  }

  return (
    <div className="grid gap-10">
      <PageHero
        eyebrow="Favorites"
        badges={
          <>
            <Badge variant={runtimeTone}>{runtime.state}</Badge>
            <Badge variant="outline">已启用 {enabledCount}</Badge>
          </>
        }
        title="收藏商品抢购"
        actions={
          <>
            <Button
              size="lg"
              variant={isRunning ? "destructive" : "default"}
              onClick={() => void startOrStop()}
              disabled={!isRunning && enabledCount === 0}
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
            <Button size="lg" variant="secondary" onClick={() => setLogDrawerOpen(true)}>
              <Eye className="mr-2 size-4" />
              查看日志
            </Button>
          </>
        }
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
