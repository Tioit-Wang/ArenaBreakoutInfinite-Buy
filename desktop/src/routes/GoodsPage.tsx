import { useEffect, useMemo, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import {
  ChevronDown,
  ChevronRight,
  FolderOpen,
  FolderTree,
  PencilLine,
  Plus,
  Save,
  Search,
  Star,
  Trash2,
} from "lucide-react"

import { useRuntimeStore } from "@/app/store"
import { resolveImageSrc } from "@/lib/assets"
import { api } from "@/lib/api"
import {
  buildGoodsCategoryTree,
  defaultSubcategories,
} from "@/lib/goods-categories"
import type { GoodsRecord } from "@/lib/types"
import { cn } from "@/lib/utils"
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
  InlineNote,
  PageHero,
  PageSurface,
  PageSurfaceContent,
  SectionHeading,
  minimalFieldClassName,
  minimalSelectTriggerClassName,
} from "@/components/minimal-page"

const emptyGoods = (): GoodsRecord => ({
  id: crypto.randomUUID(),
  name: "",
  searchName: "",
  bigCategory: "",
  subCategory: "",
  exchangeable: false,
  craftable: false,
  favorite: false,
  imagePath: "images/goods/_default.png",
  price: null,
  createdAt: new Date().toISOString(),
  updatedAt: new Date().toISOString(),
})

type ModalState =
  | { open: false }
  | { open: true; mode: "create" | "edit"; draft: GoodsRecord }

export function GoodsPage() {
  const bootstrap = useRuntimeStore((state) => state.bootstrap)
  const queryClient = useQueryClient()
  const [modal, setModal] = useState<ModalState>({ open: false })
  const [selectedBigCategory, setSelectedBigCategory] = useState("全部")
  const [selectedSubCategory, setSelectedSubCategory] = useState("全部")
  const [keyword, setKeyword] = useState("")
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(
    () => new Set(),
  )

  const categoryTree = useMemo(
    () => buildGoodsCategoryTree(bootstrap?.goods ?? []),
    [bootstrap?.goods],
  )

  const selectedNode = useMemo(
    () => categoryTree.find((item) => item.name === selectedBigCategory),
    [categoryTree, selectedBigCategory],
  )

  useEffect(() => {
    setExpandedCategories((current) => {
      const next = new Set(current)
      for (const category of categoryTree) {
        if (!next.has(category.name)) {
          next.add(category.name)
        }
      }
      return next
    })
  }, [categoryTree])

  useEffect(() => {
    if (selectedBigCategory === "全部") {
      setSelectedSubCategory("全部")
      return
    }
    const available = selectedNode?.subcategories.some(
      (item) => item.name === selectedSubCategory,
    )
    if (!available) {
      setSelectedSubCategory("全部")
    }
  }, [selectedBigCategory, selectedNode, selectedSubCategory])

  const filteredGoods = useMemo(() => {
    const source = bootstrap?.goods ?? []
    const q = keyword.trim().toLowerCase()
    return source.filter((item) => {
      const bigCategory = item.bigCategory || "未分类"
      const subCategory = item.subCategory || "未细分"
      if (selectedBigCategory !== "全部" && bigCategory !== selectedBigCategory) {
        return false
      }
      if (selectedSubCategory !== "全部" && subCategory !== selectedSubCategory) {
        return false
      }
      if (!q) return true
      return (
        item.name.toLowerCase().includes(q) ||
        item.searchName.toLowerCase().includes(q) ||
        subCategory.toLowerCase().includes(q)
      )
    })
  }, [bootstrap?.goods, keyword, selectedBigCategory, selectedSubCategory])

  if (!bootstrap) return null

  const favoriteCount = bootstrap.goods.filter((item) => item.favorite).length
  const currentView =
    selectedBigCategory === "全部"
      ? "全部分类"
      : `${selectedBigCategory}${selectedSubCategory !== "全部" ? ` / ${selectedSubCategory}` : ""}`

  const openCreate = () =>
    setModal({
      open: true,
      mode: "create",
      draft: {
        ...emptyGoods(),
        bigCategory: selectedBigCategory !== "全部" ? selectedBigCategory : "",
        subCategory:
          selectedBigCategory !== "全部" && selectedSubCategory !== "全部"
            ? selectedSubCategory
            : "",
      },
    })

  const saveGoods = async () => {
    if (!modal.open) return
    await api.goodsSave({
      ...modal.draft,
      updatedAt: new Date().toISOString(),
    })
    setModal({ open: false })
    await queryClient.invalidateQueries({ queryKey: ["bootstrap"] })
  }

  const deleteGoods = async (id: string) => {
    await api.goodsDelete(id)
    await queryClient.invalidateQueries({ queryKey: ["bootstrap"] })
  }

  const captureGoodsImage = async () => {
    if (!modal.open) return
    try {
      const path = await api.goodsCaptureCardInteractive(modal.draft.bigCategory || "杂物")
      setModal({
        ...modal,
        draft: { ...modal.draft, imagePath: path },
      })
    } catch (error) {
      if (String(error).includes("capture cancelled")) {
        return
      }
      throw error
    }
  }

  return (
    <div className="grid gap-10">
      <PageHero
        eyebrow="Library"
        badges={
          <>
            <Badge variant="outline">总库 {bootstrap.goods.length}</Badge>
            <Badge variant="secondary">收藏 {favoriteCount}</Badge>
          </>
        }
        title="物品库"
        actions={
          <Button size="lg" onClick={openCreate}>
            <Plus className="mr-2 size-4" />
            新建物品
          </Button>
        }
      />

      <PageSurface>
        <PageSurfaceContent className="gap-8">
          <SectionHeading
            eyebrow="Browse"
            title="分类与卡片"
            description="左侧保留树状导航，右侧保持宽阔留白和整洁卡片。"
            actions={
              <div className="flex min-w-[280px] items-center gap-3 border-b border-slate-200 pb-3 text-slate-500">
                <Search className="size-4" />
                <Input
                  className="h-auto border-0 bg-transparent px-0 py-0 text-base shadow-none focus-visible:ring-0"
                  placeholder="按名称 / 搜索词 / 子类过滤"
                  value={keyword}
                  onChange={(event) => setKeyword(event.target.value)}
                />
              </div>
            }
          />

          <InlineNote>
            当前分类：<span className="font-medium text-slate-900">{currentView}</span>
            <span className="mx-2 text-slate-300">/</span>
            共 {filteredGoods.length} 个物品
          </InlineNote>

          <div className="grid gap-6 xl:grid-cols-[320px_minmax(0,1fr)]">
            <div className="rounded-[32px] border border-black/5 bg-white/58 p-4">
              <div className="mb-4 flex items-center gap-2 px-2 text-xs font-semibold uppercase tracking-[0.24em] text-slate-400">
                <FolderTree className="size-4" />
                分类树
              </div>
              <div className="grid gap-1">
                <TreeLeafButton
                  active={selectedBigCategory === "全部"}
                  label="全部"
                  count={bootstrap.goods.length}
                  onClick={() => {
                    setSelectedBigCategory("全部")
                    setSelectedSubCategory("全部")
                  }}
                />

                {categoryTree.map((category) => {
                  const expanded = expandedCategories.has(category.name)
                  const selectedBig = selectedBigCategory === category.name
                  return (
                    <div key={category.name} className="space-y-1">
                      <div
                        className={cn(
                          "flex items-center rounded-[22px] transition",
                          selectedBig ? "bg-emerald-950/7" : "hover:bg-white/70",
                        )}
                      >
                        <button
                          type="button"
                          onClick={() =>
                            setExpandedCategories((current) => {
                              const next = new Set(current)
                              if (next.has(category.name)) {
                                next.delete(category.name)
                              } else {
                                next.add(category.name)
                              }
                              return next
                            })
                          }
                          className="flex size-10 items-center justify-center rounded-l-[22px] text-slate-500"
                          aria-label={`${expanded ? "收起" : "展开"} ${category.name}`}
                        >
                          {expanded ? (
                            <ChevronDown className="size-4" />
                          ) : (
                            <ChevronRight className="size-4" />
                          )}
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            setSelectedBigCategory(category.name)
                            setSelectedSubCategory("全部")
                          }}
                          className={cn(
                            "flex flex-1 items-center justify-between rounded-r-[22px] px-3 py-2.5 text-left text-sm",
                            selectedBig ? "font-medium text-emerald-950" : "text-slate-700",
                          )}
                        >
                          <span>{category.name}</span>
                          <span className="text-xs opacity-75">{category.count}</span>
                        </button>
                      </div>

                      {expanded ? (
                        <div className="ml-6 grid gap-1 border-l border-black/5 pl-3">
                          <TreeLeafButton
                            active={selectedBig && selectedSubCategory === "全部"}
                            label="全部"
                            count={category.count}
                            onClick={() => {
                              setSelectedBigCategory(category.name)
                              setSelectedSubCategory("全部")
                            }}
                            compact
                          />
                          {category.subcategories.map((subcategory) => (
                            <TreeLeafButton
                              key={`${category.name}-${subcategory.name}`}
                              active={
                                selectedBig &&
                                selectedSubCategory === subcategory.name
                              }
                              label={subcategory.name}
                              count={subcategory.count}
                              onClick={() => {
                                setSelectedBigCategory(category.name)
                                setSelectedSubCategory(subcategory.name)
                              }}
                              compact
                            />
                          ))}
                        </div>
                      ) : null}
                    </div>
                  )
                })}
              </div>
            </div>

            <ScrollArea className="rounded-[32px] border border-black/5 bg-white/52 p-4">
              {filteredGoods.length > 0 ? (
                <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                  {filteredGoods.map((item) => (
                    <article
                      key={item.id}
                      className="overflow-hidden rounded-[28px] border border-white/70 bg-white/86 shadow-sm shadow-emerald-950/5 transition duration-200 hover:-translate-y-0.5 hover:bg-white"
                    >
                      <div className="aspect-[4/3] overflow-hidden bg-slate-100">
                        <img
                          src={resolveImageSrc(bootstrap.paths, item.imagePath)}
                          alt={item.name}
                          className="h-full w-full object-cover transition duration-300 hover:scale-[1.02]"
                        />
                      </div>

                      <div className="grid gap-4 p-5">
                        <div className="space-y-3">
                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                              <h3 className="truncate text-lg font-semibold tracking-tight text-slate-900">
                                {item.name}
                              </h3>
                              <p className="truncate text-sm text-slate-500">{item.searchName}</p>
                            </div>
                            {item.favorite ? (
                              <Badge variant="secondary">
                                <Star className="mr-1 size-3.5 fill-current" />
                                收藏
                              </Badge>
                            ) : null}
                          </div>

                          <div className="flex flex-wrap gap-2">
                            <Badge variant="outline">{item.bigCategory || "未分类"}</Badge>
                            {item.subCategory ? (
                              <Badge variant="outline">{item.subCategory}</Badge>
                            ) : null}
                            {item.exchangeable ? (
                              <Badge variant="secondary">可兑换</Badge>
                            ) : null}
                          </div>
                        </div>

                        <div className="grid gap-2 text-sm text-slate-600">
                          <div className="flex items-center justify-between gap-4">
                            <span>图片路径</span>
                            <span className="max-w-[180px] truncate text-right text-xs">
                              {item.imagePath}
                            </span>
                          </div>
                          <div className="flex items-center justify-between gap-4">
                            <span>参考价格</span>
                            <span className="font-medium text-slate-900">
                              {item.price
                                ? new Intl.NumberFormat("zh-CN").format(item.price)
                                : "--"}
                            </span>
                          </div>
                        </div>

                        <div className="flex gap-2">
                          <Button
                            className="flex-1"
                            size="sm"
                            variant="secondary"
                            onClick={() =>
                              setModal({
                                open: true,
                                mode: "edit",
                                draft: structuredClone(item),
                              })
                            }
                          >
                            <PencilLine className="mr-2 size-4" />
                            编辑
                          </Button>
                          <Button
                            className="flex-1"
                            size="sm"
                            variant="destructive"
                            onClick={() => void deleteGoods(item.id)}
                          >
                            <Trash2 className="mr-2 size-4" />
                            删除
                          </Button>
                        </div>
                      </div>
                    </article>
                  ))}
                </div>
              ) : (
                <div className="flex min-h-[320px] items-center justify-center text-sm text-slate-500">
                  当前筛选条件下没有匹配物品。
                </div>
              )}
              <ScrollBar orientation="vertical" />
            </ScrollArea>
          </div>
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
                  {modal.mode === "create" ? "新建物品" : "编辑物品"}
                </DialogTitle>
                <DialogDescription className="max-w-2xl text-sm leading-6">
                  一级/二级分类对齐 Python 版结构，同时保留现有数据里的扩展分类。
                </DialogDescription>
              </DialogHeader>

              <div className="grid gap-8 py-2 md:grid-cols-2">
                <div className="space-y-3 md:col-span-2">
                  <Label>图片与截图</Label>
                  <div className="flex flex-col gap-4 rounded-[28px] border border-black/5 bg-white/62 p-4 md:flex-row md:items-start md:justify-between">
                    <GoodsCapturePreviewCard
                      imageSrc={resolveImageSrc(bootstrap.paths, modal.draft.imagePath)}
                      title={modal.draft.name}
                      price={modal.draft.price}
                    />
                    <div className="flex flex-col gap-3 md:min-w-[180px]">
                      <Button
                        type="button"
                        variant="secondary"
                        onClick={() => void captureGoodsImage()}
                      >
                        <FolderOpen className="mr-2 size-4" />
                        截图
                      </Button>
                    </div>
                  </div>
                </div>

                <FormField
                  label="物品名"
                  value={modal.draft.name}
                  onChange={(value) =>
                    setModal({
                      ...modal,
                      draft: { ...modal.draft, name: value },
                    })
                  }
                />
                <FormField
                  label="搜索词"
                  value={modal.draft.searchName}
                  onChange={(value) =>
                    setModal({
                      ...modal,
                      draft: { ...modal.draft, searchName: value },
                    })
                  }
                />

                <div className="space-y-3">
                  <Label>一级分类</Label>
                  <Select
                    value={modal.draft.bigCategory || "__none__"}
                    onValueChange={(value) => {
                      const bigCategory = value === "__none__" ? "" : value
                      const defaults = defaultSubcategories(bigCategory)
                      const existing =
                        categoryTree
                          .find((item) => item.name === bigCategory)
                          ?.subcategories.map((item) => item.name) ?? []
                      const merged = Array.from(new Set([...defaults, ...existing]))
                      setModal({
                        ...modal,
                        draft: {
                          ...modal.draft,
                          bigCategory,
                          subCategory: merged.includes(modal.draft.subCategory)
                            ? modal.draft.subCategory
                            : merged[0] ?? "",
                        },
                      })
                    }}
                  >
                    <SelectTrigger className={minimalSelectTriggerClassName}>
                      <SelectValue placeholder="请选择一级分类" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__none__">未分类</SelectItem>
                      {categoryTree.map((category) => (
                        <SelectItem key={category.name} value={category.name}>
                          {category.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-3">
                  <Label>二级分类</Label>
                  <Select
                    value={modal.draft.subCategory || "__none__"}
                    onValueChange={(value) =>
                      setModal({
                        ...modal,
                        draft: {
                          ...modal.draft,
                          subCategory: value === "__none__" ? "" : value,
                        },
                      })
                    }
                  >
                    <SelectTrigger className={minimalSelectTriggerClassName}>
                      <SelectValue placeholder="请选择二级分类" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__none__">未细分</SelectItem>
                      {Array.from(
                        new Set([
                          ...defaultSubcategories(modal.draft.bigCategory),
                          ...(categoryTree
                            .find((item) => item.name === modal.draft.bigCategory)
                            ?.subcategories.map((item) => item.name) ?? []),
                        ]),
                      ).map((subcategory) => (
                        <SelectItem key={subcategory} value={subcategory}>
                          {subcategory}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <FormField
                  label="图片路径"
                  value={modal.draft.imagePath}
                  onChange={(value) =>
                    setModal({
                      ...modal,
                      draft: { ...modal.draft, imagePath: value },
                    })
                  }
                />

                <div className="space-y-3">
                  <Label>参考价格</Label>
                  <Input
                    className={minimalFieldClassName}
                    type="number"
                    value={modal.draft.price ?? 0}
                    onChange={(event) =>
                      setModal({
                        ...modal,
                        draft: { ...modal.draft, price: Number(event.target.value) },
                      })
                    }
                  />
                </div>

                <div className="space-y-3">
                  <Label>可兑换</Label>
                  <Select
                    value={String(modal.draft.exchangeable)}
                    onValueChange={(value) =>
                      setModal({
                        ...modal,
                        draft: { ...modal.draft, exchangeable: value === "true" },
                      })
                    }
                  >
                    <SelectTrigger className={minimalSelectTriggerClassName}>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="true">是</SelectItem>
                      <SelectItem value="false">否</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-3">
                  <Label>收藏</Label>
                  <Select
                    value={String(modal.draft.favorite)}
                    onValueChange={(value) =>
                      setModal({
                        ...modal,
                        draft: { ...modal.draft, favorite: value === "true" },
                      })
                    }
                  >
                    <SelectTrigger className={minimalSelectTriggerClassName}>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="true">是</SelectItem>
                      <SelectItem value="false">否</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <DialogFooter>
                <Button variant="secondary" onClick={() => setModal({ open: false })}>
                  取消
                </Button>
                <Button onClick={() => void saveGoods()}>
                  <Save className="mr-2 size-4" />
                  保存物品
                </Button>
              </DialogFooter>
            </>
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  )
}

function GoodsCapturePreviewCard({
  imageSrc,
  title,
  price,
}: {
  imageSrc: string
  title: string
  price?: number | null
}) {
  const hasPrice = typeof price === "number" && Number.isFinite(price) && price > 0
  return (
    <div className="grid w-full justify-center md:justify-start">
      <div className="grid h-[212px] w-[165px] overflow-hidden rounded-[3px] border-[0.5px] border-[#cccccc] bg-[#ffd84d] shadow-[0_14px_32px_rgba(15,23,42,0.08)]">
        <div className="flex h-[20px] items-center bg-[#2d7cff] px-2 text-[11px] font-semibold text-white">
          <span className="truncate">{title || "标题区"}</span>
        </div>
        <div className="bg-[#ffd84d] px-[30px] py-[20px]">
          <div className="h-full overflow-hidden border border-dashed border-[#333333] bg-white/90">
            <img
              src={imageSrc}
              alt={title || "物品截图预览"}
              className="h-full w-full object-cover"
            />
          </div>
        </div>
        <div className="flex h-[30px] items-center justify-between gap-2 bg-[#2ea043] px-2 text-[11px] text-white">
          <span className="opacity-80">价格区</span>
          <span className="truncate font-semibold">
            {hasPrice ? new Intl.NumberFormat("zh-CN").format(price) : "--"}
          </span>
        </div>
      </div>
    </div>
  )
}

function TreeLeafButton({
  active,
  label,
  count,
  onClick,
  compact = false,
}: {
  active: boolean
  label: string
  count: number
  onClick: () => void
  compact?: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex items-center justify-between rounded-[20px] px-3 text-left text-sm transition",
        compact ? "py-2" : "py-2.5",
        active
          ? compact
            ? "bg-slate-900 text-white"
            : "bg-emerald-900 text-white"
          : "bg-transparent text-slate-700 hover:bg-white/80",
      )}
    >
      <span>{label}</span>
      <span className="text-xs opacity-80">{count}</span>
    </button>
  )
}

function FormField({
  label,
  value,
  onChange,
}: {
  label: string
  value: string
  onChange: (value: string) => void
}) {
  return (
    <div className="space-y-3">
      <Label>{label}</Label>
      <Input
        className={minimalFieldClassName}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  )
}
