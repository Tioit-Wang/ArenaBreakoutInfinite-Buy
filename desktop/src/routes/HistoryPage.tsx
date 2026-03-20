import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"

import { useRuntimeStore } from "@/app/store"
import { api } from "@/lib/api"
import type { ItemPriceTrendPoint } from "@/lib/types"
import { Card, CardContent } from "@/components/ui/card"
import { Label } from "@/components/ui/label"
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
  InlineNote,
  MetricCard,
  MetricGrid,
  PageSurface,
  PageSurfaceContent,
  minimalSelectTriggerClassName,
} from "@/components/minimal-page"

type RangePreset = "7d" | "30d" | "90d"

const RANGE_OPTIONS: Array<{ value: RangePreset; label: string; days: number }> = [
  { value: "7d", label: "7天", days: 7 },
  { value: "30d", label: "30天", days: 30 },
  { value: "90d", label: "90天", days: 90 },
]

function fmt(value: number) {
  return new Intl.NumberFormat("zh-CN").format(value)
}

function fmtNullable(value?: number | null) {
  return typeof value === "number" ? fmt(value) : "—"
}

function buildRange(preset: RangePreset) {
  const option = RANGE_OPTIONS.find((item) => item.value === preset) ?? RANGE_OPTIONS[1]
  const to = new Date()
  const from = new Date(to)
  from.setHours(0, 0, 0, 0)
  from.setDate(from.getDate() - (option.days - 1))
  return {
    from: from.toISOString(),
    to: to.toISOString(),
  }
}

export function HistoryPage() {
  const bootstrap = useRuntimeStore((state) => state.bootstrap)
  const [selectedItemId, setSelectedItemId] = useState("")
  const [rangePreset, setRangePreset] = useState<RangePreset>("30d")

  const range = useMemo(() => buildRange(rangePreset), [rangePreset])
  const timezoneOffsetMin = useMemo(() => new Date().getTimezoneOffset(), [])

  const trendQuery = useQuery({
    queryKey: [
      "history-item-price-trend",
      selectedItemId,
      rangePreset,
      range.from,
      range.to,
      timezoneOffsetMin,
    ],
    queryFn: () =>
      api.historyQueryItemPriceTrend(selectedItemId, range.from, range.to, timezoneOffsetMin),
    enabled: !!bootstrap && !!selectedItemId,
  })

  if (!bootstrap) return null

  const selectedItem = bootstrap.goods.find((item) => item.id === selectedItemId)
  const selectedItemName = trendQuery.data?.itemName || selectedItem?.name || "未选择物品"
  const points = trendQuery.data?.points ?? []
  const hasData = points.length > 0

  return (
    <div className="grid gap-6">
      <PageSurface>
        <PageSurfaceContent className="gap-6">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-black/5 pb-4">
            <h1 className="font-display text-2xl tracking-tight text-slate-950">
              历史统计
            </h1>
            <div className="rounded-full border border-black/5 bg-white/70 px-4 py-2 text-sm text-slate-600">
              当前物品
              <span className="ml-2 font-medium text-slate-900">{selectedItemName}</span>
            </div>
          </div>

          <div className="grid gap-4 rounded-[24px] border border-black/5 bg-white/55 p-4 lg:grid-cols-[minmax(0,360px)_minmax(0,1fr)] lg:items-end">
            <div className="space-y-2">
              <Label className="text-xs uppercase tracking-[0.18em] text-slate-400">物品</Label>
              <Select value={selectedItemId || undefined} onValueChange={setSelectedItemId}>
                <SelectTrigger className={minimalSelectTriggerClassName}>
                  <SelectValue placeholder="请选择一个物品" />
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

            <div className="space-y-2">
              <Label className="text-xs uppercase tracking-[0.18em] text-slate-400">时间范围</Label>
              <div className="flex flex-wrap gap-2">
                {RANGE_OPTIONS.map((option) => {
                  const active = option.value === rangePreset
                  return (
                    <button
                      key={option.value}
                      type="button"
                      onClick={() => setRangePreset(option.value)}
                      className={`rounded-full border px-4 py-2 text-sm transition ${
                        active
                          ? "border-emerald-300 bg-emerald-50 text-emerald-900"
                          : "border-black/10 bg-white/80 text-slate-600 hover:border-slate-300 hover:text-slate-900"
                      }`}
                    >
                      {option.label}
                    </button>
                  )
                })}
              </div>
            </div>
          </div>

          <MetricGrid className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <MetricCard label="最新价格" value={fmtNullable(trendQuery.data?.latestPrice)} />
            <MetricCard label="区间最低" value={fmtNullable(trendQuery.data?.rangeMinPrice)} />
            <MetricCard label="区间最高" value={fmtNullable(trendQuery.data?.rangeMaxPrice)} />
            <MetricCard label="区间均价" value={fmtNullable(trendQuery.data?.rangeAvgPrice)} />
          </MetricGrid>

          <div className="space-y-3 border-t border-black/5 pt-6">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h2 className="text-lg font-semibold text-slate-950">价格趋势图</h2>
                <p className="text-sm text-slate-500">展示每日最高价、最低价和均价。</p>
              </div>
            </div>

            {!selectedItemId ? (
              <InlineNote>请选择一个物品查看价格趋势。</InlineNote>
            ) : trendQuery.isLoading ? (
              <InlineNote>正在加载该物品的价格趋势...</InlineNote>
            ) : trendQuery.error ? (
              <InlineNote tone="rose">{String(trendQuery.error)}</InlineNote>
            ) : hasData ? (
              <Card className="rounded-[28px] border border-black/5 bg-white/55 shadow-none">
                <CardContent className="p-4 md:p-6">
                  <div className="h-[340px] w-full">
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart
                        data={points}
                        margin={{ top: 12, right: 12, left: -16, bottom: 0 }}
                      >
                        <CartesianGrid stroke="rgba(148, 163, 184, 0.20)" strokeDasharray="4 4" />
                        <XAxis dataKey="day" axisLine={false} tickLine={false} dy={8} />
                        <YAxis
                          axisLine={false}
                          tickLine={false}
                          width={84}
                          tickFormatter={(value) => fmt(Number(value))}
                        />
                        <Tooltip content={<PriceTrendTooltip />} />
                        <Legend />
                        <Line
                          type="monotone"
                          dataKey="maxPrice"
                          name="每日最高价"
                          stroke="#166534"
                          strokeWidth={2.5}
                          dot={false}
                          activeDot={{ r: 4 }}
                        />
                        <Line
                          type="monotone"
                          dataKey="minPrice"
                          name="每日最低价"
                          stroke="#be123c"
                          strokeWidth={2.5}
                          dot={false}
                          activeDot={{ r: 4 }}
                        />
                        <Line
                          type="monotone"
                          dataKey="avgPrice"
                          name="每日均价"
                          stroke="#475569"
                          strokeWidth={2}
                          strokeDasharray="6 4"
                          dot={false}
                          activeDot={{ r: 3 }}
                        />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                </CardContent>
              </Card>
            ) : (
              <InlineNote>该物品暂无价格历史。</InlineNote>
            )}
          </div>

          <div className="space-y-3 border-t border-black/5 pt-6">
            <div>
              <h2 className="text-lg font-semibold text-slate-950">每日高低价</h2>
              <p className="text-sm text-slate-500">按本机时区聚合到天，便于回看日内区间。</p>
            </div>

            {!selectedItemId ? (
              <InlineNote>请选择一个物品查看每日最高价与最低价。</InlineNote>
            ) : trendQuery.isLoading ? (
              <InlineNote>正在准备每日高低价表...</InlineNote>
            ) : trendQuery.error ? (
              <InlineNote tone="rose">{String(trendQuery.error)}</InlineNote>
            ) : hasData ? (
              <div className="overflow-hidden rounded-[28px] border border-black/5 bg-white/55">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>日期</TableHead>
                      <TableHead className="text-right">最低价</TableHead>
                      <TableHead className="text-right">最高价</TableHead>
                      <TableHead className="text-right">均价</TableHead>
                      <TableHead className="text-right">最新价</TableHead>
                      <TableHead className="text-right">样本数</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {points.map((point) => (
                      <TableRow key={point.day}>
                        <TableCell className="py-4 text-slate-700">{point.day}</TableCell>
                        <TableCell className="py-4 text-right text-rose-700">
                          {fmt(point.minPrice)}
                        </TableCell>
                        <TableCell className="py-4 text-right text-emerald-800">
                          {fmt(point.maxPrice)}
                        </TableCell>
                        <TableCell className="py-4 text-right text-slate-700">
                          {fmt(point.avgPrice)}
                        </TableCell>
                        <TableCell className="py-4 text-right font-medium text-slate-900">
                          {fmt(point.latestPrice)}
                        </TableCell>
                        <TableCell className="py-4 text-right text-slate-600">
                          {fmt(point.sampleCount)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            ) : (
              <InlineNote>该物品暂无价格历史。</InlineNote>
            )}
          </div>
        </PageSurfaceContent>
      </PageSurface>
    </div>
  )
}

function PriceTrendTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean
  payload?: Array<{ payload?: ItemPriceTrendPoint }>
  label?: string
}) {
  const point = payload?.[0]?.payload
  if (!active || !point) {
    return null
  }

  return (
    <div className="rounded-3xl border border-black/5 bg-white/95 p-4 text-sm text-slate-700 shadow-lg shadow-slate-900/5">
      <p className="text-xs uppercase tracking-[0.24em] text-slate-400">Date</p>
      <p className="mt-1 font-medium text-slate-950">{label}</p>
      <div className="mt-3 grid gap-2">
        <TooltipRow label="最高价" value={point.maxPrice} tone="emerald" />
        <TooltipRow label="最低价" value={point.minPrice} tone="rose" />
        <TooltipRow label="均价" value={point.avgPrice} tone="slate" />
        <TooltipRow label="最新价" value={point.latestPrice} tone="slate" />
        <TooltipRow label="样本数" value={point.sampleCount} tone="slate" />
      </div>
    </div>
  )
}

function TooltipRow({
  label,
  value,
  tone,
}: {
  label: string
  value: number
  tone: "slate" | "emerald" | "rose"
}) {
  const colorClass =
    tone === "emerald"
      ? "text-emerald-800"
      : tone === "rose"
        ? "text-rose-700"
        : "text-slate-700"

  return (
    <div className="flex items-center justify-between gap-4">
      <span className="text-slate-500">{label}</span>
      <span className={`font-medium ${colorClass}`}>{fmt(value)}</span>
    </div>
  )
}
