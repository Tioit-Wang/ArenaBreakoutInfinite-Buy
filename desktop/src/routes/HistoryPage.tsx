import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Calculator, TrendingUp } from "lucide-react"

import { useRuntimeStore } from "@/app/store"
import { api } from "@/lib/api"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
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
  MetricCard,
  MetricGrid,
  PageHero,
  PageSurface,
  PageSurfaceContent,
  SectionHeading,
  minimalFieldClassName,
  minimalSelectTriggerClassName,
} from "@/components/minimal-page"

function fmt(value: number) {
  return new Intl.NumberFormat("zh-CN").format(value || 0)
}

export function HistoryPage() {
  const bootstrap = useRuntimeStore((state) => state.bootstrap)
  const [selectedItemId, setSelectedItemId] = useState<string>("")
  const [buyPrice, setBuyPrice] = useState(0)
  const [sellPrice, setSellPrice] = useState(0)
  const [qty, setQty] = useState(1)

  const summaryQuery = useQuery({
    queryKey: ["history-summary", selectedItemId],
    queryFn: () => api.historyQuerySummary(selectedItemId || undefined),
    enabled: !!bootstrap,
  })

  const priceQuery = useQuery({
    queryKey: ["history-prices", selectedItemId],
    queryFn: () => api.historyQueryPrices(selectedItemId || undefined),
    enabled: !!bootstrap,
  })

  const purchaseQuery = useQuery({
    queryKey: ["history-purchases", selectedItemId],
    queryFn: () => api.historyQueryPurchases(selectedItemId || undefined),
    enabled: !!bootstrap,
  })

  const profit = useMemo(() => {
    const cost = buyPrice * qty
    const gross = sellPrice * qty
    const tax = gross * 0.06
    const net = gross - tax
    return {
      cost,
      gross,
      tax,
      net,
      profit: net - cost,
    }
  }, [buyPrice, qty, sellPrice])

  if (!bootstrap) return null

  const selectedItemName =
    bootstrap.goods.find((item) => item.id === selectedItemId)?.name ?? "全部物品"

  return (
    <div className="grid gap-10">
      <PageHero
        eyebrow="History"
        badges={
          <>
            <Badge variant="secondary">
              <TrendingUp className="mr-2 size-4" />
              SQLite 主库
            </Badge>
            <Badge variant="outline">{selectedItemName}</Badge>
          </>
        }
        title="历史统计"
        description="用更安静的布局查看价格轨迹、购买记录和即时利润估算。"
        detail="按物品筛选后，页面会同步更新聚合指标、价格历史和购买流水。"
      />

      <PageSurface>
        <PageSurfaceContent className="gap-8">
          <SectionHeading
            eyebrow="Summary"
            title="聚合指标"
            description="先确定观察对象，再读指标，不让筛选控件打断数据视线。"
          />

          <div className="grid gap-8 lg:grid-cols-[280px_minmax(0,1fr)]">
            <div className="space-y-3">
              <Label>选择物品</Label>
              <Select
                value={selectedItemId || "__all__"}
                onValueChange={(value) => setSelectedItemId(value === "__all__" ? "" : value)}
              >
                <SelectTrigger className={minimalSelectTriggerClassName}>
                  <SelectValue placeholder="全部物品" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__all__">全部物品</SelectItem>
                  {bootstrap.goods.map((item) => (
                    <SelectItem key={item.id} value={item.id}>
                      {item.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <MetricGrid className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
              <MetricCard label="价格记录" value={fmt(summaryQuery.data?.priceCount ?? 0)} />
              <MetricCard label="最新价格" value={fmt(summaryQuery.data?.latestPrice ?? 0)} />
              <MetricCard label="均价" value={fmt(summaryQuery.data?.priceAvg ?? 0)} />
              <MetricCard label="购买数量" value={fmt(summaryQuery.data?.purchaseQty ?? 0)} />
              <MetricCard label="购买金额" value={fmt(summaryQuery.data?.purchaseAmount ?? 0)} />
            </MetricGrid>
          </div>
        </PageSurfaceContent>
      </PageSurface>

      <PageSurface>
        <PageSurfaceContent className="gap-8">
          <SectionHeading
            eyebrow="Calculator"
            title="利润计算"
            description="沿用 6% 交易税规则，快速估算当前交易是否仍有空间。"
            actions={
              <Badge variant="outline">
                <Calculator className="mr-2 size-4" />
                即时估算
              </Badge>
            }
          />

          <div className="grid gap-8 md:grid-cols-3">
            <NumberField label="买入价" value={buyPrice} onChange={setBuyPrice} />
            <NumberField label="卖出价" value={sellPrice} onChange={setSellPrice} />
            <NumberField label="数量" value={qty} onChange={setQty} />
          </div>

          <MetricGrid className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
            <MetricCard label="总成本" value={fmt(profit.cost)} />
            <MetricCard label="卖出总额" value={fmt(profit.gross)} />
            <MetricCard label="交易税" value={fmt(profit.tax)} />
            <MetricCard label="净收入" value={fmt(profit.net)} />
            <MetricCard
              label="利润"
              tone={profit.profit >= 0 ? "emerald" : "rose"}
              value={fmt(profit.profit)}
            />
          </MetricGrid>
        </PageSurfaceContent>
      </PageSurface>

      <div className="grid gap-6 xl:grid-cols-2">
        <PageSurface>
          <PageSurfaceContent className="gap-8">
            <SectionHeading
              eyebrow="Prices"
              title="价格历史"
              description="最近识别到的价格记录。"
            />

            <div className="overflow-hidden rounded-[32px] border border-black/5 bg-white/55">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>时间</TableHead>
                    <TableHead>物品</TableHead>
                    <TableHead className="text-right">价格</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {priceQuery.data && priceQuery.data.length > 0 ? (
                    priceQuery.data.map((item) => (
                      <TableRow key={item.id}>
                        <TableCell className="py-5 text-slate-600">{item.observedAt}</TableCell>
                        <TableCell className="py-5 text-slate-900">{item.itemName}</TableCell>
                        <TableCell className="py-5 text-right font-medium text-slate-900">
                          {fmt(item.price)}
                        </TableCell>
                      </TableRow>
                    ))
                  ) : (
                    <TableRow>
                      <TableCell colSpan={3} className="py-16 text-center text-sm text-slate-500">
                        当前条件下没有价格记录。
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </div>
          </PageSurfaceContent>
        </PageSurface>

        <PageSurface>
          <PageSurfaceContent className="gap-8">
            <SectionHeading
              eyebrow="Purchases"
              title="购买历史"
              description="最近写入的购买流水。"
            />

            <div className="overflow-hidden rounded-[32px] border border-black/5 bg-white/55">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>时间</TableHead>
                    <TableHead>物品</TableHead>
                    <TableHead className="text-right">单价</TableHead>
                    <TableHead className="text-right">数量</TableHead>
                    <TableHead className="text-right">金额</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {purchaseQuery.data && purchaseQuery.data.length > 0 ? (
                    purchaseQuery.data.map((item) => (
                      <TableRow key={item.id}>
                        <TableCell className="py-5 text-slate-600">{item.purchasedAt}</TableCell>
                        <TableCell className="py-5 text-slate-900">{item.itemName}</TableCell>
                        <TableCell className="py-5 text-right text-slate-700">
                          {fmt(item.price)}
                        </TableCell>
                        <TableCell className="py-5 text-right text-slate-700">
                          {fmt(item.qty)}
                        </TableCell>
                        <TableCell className="py-5 text-right font-medium text-slate-900">
                          {fmt(item.amount)}
                        </TableCell>
                      </TableRow>
                    ))
                  ) : (
                    <TableRow>
                      <TableCell colSpan={5} className="py-16 text-center text-sm text-slate-500">
                        当前条件下没有购买记录。
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </div>
          </PageSurfaceContent>
        </PageSurface>
      </div>
    </div>
  )
}

function NumberField({
  label,
  value,
  onChange,
}: {
  label: string
  value: number
  onChange: (value: number) => void
}) {
  return (
    <div className="space-y-3">
      <Label>{label}</Label>
      <Input
        className={minimalFieldClassName}
        type="number"
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </div>
  )
}
