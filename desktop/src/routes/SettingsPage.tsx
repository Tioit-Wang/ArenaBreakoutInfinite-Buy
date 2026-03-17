import { useEffect, useMemo, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { open } from "@tauri-apps/plugin-dialog"
import {
  Eye,
  FolderOpen,
  ImageIcon,
  ImagePlus,
  RefreshCw,
  RotateCw,
  Save,
  ScanSearch,
} from "lucide-react"

import { useRuntimeStore } from "@/app/store"
import { openCaptureOverlay } from "@/lib/capture-overlay"
import { resolveImageSrc } from "@/lib/assets"
import { api } from "@/lib/api"
import { isTauriRuntime } from "@/lib/tauri"
import type { AppConfig, OcrStatus, TemplateConfig } from "@/lib/types"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ScrollArea, ScrollBar } from "@/components/ui/scroll-area"
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
  PageHero,
  PageSurface,
  PageSurfaceContent,
  SectionHeading,
  minimalFieldClassName,
} from "@/components/minimal-page"

const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value))

export function SettingsPage() {
  const bootstrap = useRuntimeStore((state) => state.bootstrap)
  const setOcrStatus = useRuntimeStore((state) => state.setOcrStatus)
  const [config, setConfig] = useState<AppConfig | null>(null)
  const [templates, setTemplates] = useState<TemplateConfig[]>([])
  const [previewTemplate, setPreviewTemplate] = useState<TemplateConfig | null>(null)
  const [templateMessage, setTemplateMessage] = useState<string>("")
  const [ocrMessage, setOcrMessage] = useState<string>("")
  const queryClient = useQueryClient()

  useEffect(() => {
    if (bootstrap) {
      setConfig(clone(bootstrap.config))
      setTemplates(clone(bootstrap.templates))
    }
  }, [bootstrap])

  const ocrTone = useMemo(() => {
    if (bootstrap?.ocrStatus.ready) return "default" as const
    if (bootstrap?.ocrStatus.started) return "secondary" as const
    return "outline" as const
  }, [bootstrap?.ocrStatus.ready, bootstrap?.ocrStatus.started])

  if (!bootstrap || !config) return null

  const chooseExecutable = async (
    currentValue: string,
    onChange: (path: string) => void,
  ) => {
    if (!isTauriRuntime()) return
    const selected = await open({
      title: "选择可执行文件",
      defaultPath: currentValue || bootstrap.paths.rootDir,
      multiple: false,
      directory: false,
      filters: [{ name: "Executable", extensions: ["exe"] }],
    })
    if (typeof selected === "string") {
      onChange(selected)
    }
  }

  const chooseImageFile = async () => {
    if (!isTauriRuntime()) return null
    const selected = await open({
      title: "选择图片文件",
      defaultPath: bootstrap.paths.imagesDir,
      multiple: false,
      directory: false,
      filters: [{ name: "Images", extensions: ["png", "jpg", "jpeg", "webp"] }],
    })
    return typeof selected === "string" ? selected : null
  }

  const updateTemplate = (id: string, patch: Partial<TemplateConfig>) => {
    setTemplates((current) =>
      current.map((item) => (item.id === id ? { ...item, ...patch } : item)),
    )
  }

  const saveConfig = async () => {
    await api.configSave(config)
    await queryClient.invalidateQueries({ queryKey: ["bootstrap"] })
  }

  const runOcrAction = async (action: () => Promise<OcrStatus>) => {
    const status = await action()
    setOcrStatus(status)
    setOcrMessage(status.message)
    await queryClient.invalidateQueries({ queryKey: ["bootstrap"] })
  }

  const saveTemplate = async (template: TemplateConfig) => {
    await api.templatesSave({
      ...template,
      updatedAt: new Date().toISOString(),
    })
    setTemplateMessage(`模板 ${template.name} 已保存`)
    await queryClient.invalidateQueries({ queryKey: ["bootstrap"] })
  }

  const testTemplate = async (template: TemplateConfig) => {
    const result = await api.templatesTest(template.path)
    setTemplateMessage(`${template.name}: ${result.message}`)
  }

  const importTemplateImage = async (template: TemplateConfig) => {
    const sourcePath = await chooseImageFile()
    if (!sourcePath) return
    const path = await api.templatesImportImage(template.slug, sourcePath)
    updateTemplate(template.id, { path })
    setTemplateMessage(`${template.name}: 已导入图片 ${path}`)
  }

  const captureTemplate = async (template: TemplateConfig) => {
    const path = await openCaptureOverlay({ mode: "template", slug: template.slug })
    if (!path) return
    updateTemplate(template.id, { path })
    setTemplateMessage(`${template.name}: 截图已保存到 ${path}`)
  }

  return (
    <div className="grid gap-10">
      <PageHero
        eyebrow="System"
        badges={
          <>
            <Badge variant={ocrTone}>
              {bootstrap.ocrStatus.ready ? "Umi-OCR 已就绪" : "Umi-OCR 未就绪"}
            </Badge>
            <Badge variant="outline">{bootstrap.ocrStatus.baseUrl}</Badge>
          </>
        }
        title="设置"
        description="把 OCR 状态、启动路径和模板资源拉到同一张整洁工作台上。"
        detail={bootstrap.ocrStatus.message}
        actions={
          <Button size="lg" onClick={() => void saveConfig()}>
            <Save className="mr-2 size-4" />
            保存设置
          </Button>
        }
      />

      <PageSurface>
        <PageSurfaceContent className="gap-8">
          <SectionHeading
            eyebrow="Runtime"
            title="Umi-OCR 与启动设置"
            description="在这里检查 OCR 当前状态、重启侧车服务，并维护启动器路径。"
          />

          <div className="grid gap-6 xl:grid-cols-[1.05fr_1.45fr]">
            <div className="grid gap-4 rounded-[32px] border border-black/5 bg-white/60 p-5">
              <div className="flex items-start justify-between gap-4">
                <div className="space-y-2">
                  <p className="text-xs font-semibold uppercase tracking-[0.24em] text-slate-400">
                    OCR Status
                  </p>
                  <p className="text-lg font-semibold tracking-tight text-slate-900">
                    {bootstrap.ocrStatus.ready ? "已准备就绪" : "等待连接"}
                  </p>
                  <p className="text-sm leading-6 text-slate-600">
                    {bootstrap.ocrStatus.message}
                  </p>
                </div>
                <Badge variant={ocrTone}>{bootstrap.ocrStatus.ready ? "Ready" : "Idle"}</Badge>
              </div>

              <div className="flex flex-wrap gap-2">
                <Button size="sm" variant="secondary" onClick={() => void runOcrAction(api.ocrStatus)}>
                  <RefreshCw className="mr-2 size-4" />
                  刷新
                </Button>
                <Button size="sm" variant="secondary" onClick={() => void runOcrAction(api.ocrStart)}>
                  启动
                </Button>
                <Button size="sm" variant="secondary" onClick={() => void runOcrAction(api.ocrStop)}>
                  停止
                </Button>
                <Button size="sm" onClick={() => void runOcrAction(api.ocrRestart)}>
                  <RotateCw className="mr-2 size-4" />
                  重启 Umi-OCR
                </Button>
              </div>

              {(ocrMessage || bootstrap.ocrStatus.message) && (
                <InlineNote tone={bootstrap.ocrStatus.ready ? "emerald" : "slate"}>
                  {ocrMessage || bootstrap.ocrStatus.message}
                </InlineNote>
              )}
            </div>

            <div className="grid gap-8 md:grid-cols-2">
              <PathField
                label="启动器路径"
                value={config.game.exePath}
                placeholder="点击右侧按钮选择 launcher.exe"
                onPick={() =>
                  void chooseExecutable(config.game.exePath, (path) =>
                    setConfig({
                      ...config,
                      game: { ...config.game, exePath: path },
                    }),
                  )
                }
              />
              <PathField
                label="Umi-OCR 路径"
                value={config.umiOcr.exePath}
                placeholder="点击右侧按钮选择 Umi-OCR.exe"
                onPick={() =>
                  void chooseExecutable(config.umiOcr.exePath, (path) =>
                    setConfig({
                      ...config,
                      umiOcr: { ...config.umiOcr, exePath: path },
                    }),
                  )
                }
              />

              <div className="space-y-3">
                <Label htmlFor="launchArgs">启动参数</Label>
                <Input
                  id="launchArgs"
                  className={minimalFieldClassName}
                  value={config.game.launchArgs}
                  onChange={(event) =>
                    setConfig({
                      ...config,
                      game: { ...config.game, launchArgs: event.target.value },
                    })
                  }
                />
              </div>

              <div className="space-y-3">
                <Label htmlFor="hotkey">全局热键</Label>
                <Input
                  id="hotkey"
                  className={minimalFieldClassName}
                  value={config.hotkeys.toggle}
                  onChange={(event) =>
                    setConfig({
                      ...config,
                      hotkeys: { ...config.hotkeys, toggle: event.target.value },
                    })
                  }
                />
              </div>
            </div>
          </div>
        </PageSurfaceContent>
      </PageSurface>

      <PageSurface>
        <PageSurfaceContent className="gap-8">
          <SectionHeading
            eyebrow="Templates"
            title="模板资源"
            description="直接浏览缩略图、调整阈值、测试识别或重录截图。"
          />

          <ScrollArea className="rounded-[32px] border border-black/5 bg-white/55">
            <Table className="min-w-[1180px]">
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[160px]">预览</TableHead>
                  <TableHead>模板名称</TableHead>
                  <TableHead>分类</TableHead>
                  <TableHead className="w-[180px]">阈值</TableHead>
                  <TableHead className="w-[320px] text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {templates.map((template) => (
                  <TableRow key={template.id}>
                    <TableCell className="py-5">
                      <button
                        type="button"
                        className="group relative flex size-24 items-center justify-center overflow-hidden rounded-[24px] border border-black/5 bg-white/75"
                        onClick={() => setPreviewTemplate(template)}
                      >
                        {resolveImageSrc(bootstrap.paths, template.path) ? (
                          <img
                            src={resolveImageSrc(bootstrap.paths, template.path)}
                            alt={template.name}
                            className="h-full w-full object-cover transition duration-200 group-hover:scale-105"
                          />
                        ) : (
                          <ImageIcon className="size-8 text-slate-400" />
                        )}
                      </button>
                    </TableCell>
                    <TableCell className="py-5">
                      <div className="space-y-1">
                        <p className="font-medium text-slate-900">{template.name}</p>
                        <p className="text-xs text-slate-500">{template.slug}</p>
                      </div>
                    </TableCell>
                    <TableCell className="py-5 text-slate-700">{template.kind}</TableCell>
                    <TableCell className="py-5">
                      <Input
                        className="h-10 rounded-full border-white/70 bg-white"
                        type="number"
                        step="0.01"
                        value={template.confidence}
                        onChange={(event) =>
                          updateTemplate(template.id, {
                            confidence: Number(event.target.value),
                          })
                        }
                      />
                    </TableCell>
                    <TableCell className="py-5">
                      <div className="flex justify-end gap-2">
                        <Button
                          size="sm"
                          variant="secondary"
                          onClick={() => setPreviewTemplate(template)}
                        >
                          <Eye className="mr-2 size-4" />
                          大图
                        </Button>
                        <Button
                          size="sm"
                          variant="secondary"
                          onClick={() => void importTemplateImage(template)}
                        >
                          <ImagePlus className="mr-2 size-4" />
                          选图
                        </Button>
                        <Button
                          size="sm"
                          variant="secondary"
                          onClick={() => void testTemplate(template)}
                        >
                          <ScanSearch className="mr-2 size-4" />
                          测试
                        </Button>
                        <Button
                          size="sm"
                          variant="secondary"
                          onClick={() => void captureTemplate(template)}
                        >
                          <FolderOpen className="mr-2 size-4" />
                          自由截图
                        </Button>
                        <Button size="sm" onClick={() => void saveTemplate(template)}>
                          保存
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <ScrollBar orientation="horizontal" />
          </ScrollArea>

          {templateMessage ? <InlineNote tone="emerald">{templateMessage}</InlineNote> : null}
        </PageSurfaceContent>
      </PageSurface>

      <Dialog open={!!previewTemplate} onOpenChange={(open) => !open && setPreviewTemplate(null)}>
        <DialogContent className="max-w-4xl overflow-hidden p-0">
          <DialogHeader className="px-6 pt-6">
            <DialogTitle className="font-display text-3xl tracking-tight">
              {previewTemplate?.name}
            </DialogTitle>
            <DialogDescription className="text-sm leading-6">
              点击表格中的缩略图即可查看大图预览。
            </DialogDescription>
          </DialogHeader>
          <div className="px-6 pb-6">
            <div className="overflow-hidden rounded-[28px] border border-black/5 bg-white/60">
              {previewTemplate ? (
                <img
                  src={resolveImageSrc(bootstrap.paths, previewTemplate.path)}
                  alt={previewTemplate.name}
                  className="max-h-[70vh] w-full object-contain"
                />
              ) : null}
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function PathField({
  label,
  value,
  placeholder,
  onPick,
}: {
  label: string
  value: string
  placeholder: string
  onPick: () => void
}) {
  return (
    <div className="space-y-3">
      <Label>{label}</Label>
      <div className="flex items-center gap-2 border-b border-slate-200 pb-2">
        <Input
          readOnly
          className="h-auto border-0 bg-transparent px-0 py-2 shadow-none focus-visible:ring-0"
          value={value}
          placeholder={placeholder}
          onClick={onPick}
        />
        <Button type="button" size="icon" variant="ghost" onClick={onPick}>
          <FolderOpen className="size-4" />
        </Button>
      </div>
    </div>
  )
}
