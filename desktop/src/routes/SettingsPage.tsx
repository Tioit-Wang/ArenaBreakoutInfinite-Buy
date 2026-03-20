import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { open } from "@tauri-apps/plugin-dialog"
import { FolderOpen, ImageIcon, RefreshCw, RotateCw, ScanSearch } from "lucide-react"

import { useRuntimeStore } from "@/app/store"
import { resolveImageSrc } from "@/lib/assets"
import { api } from "@/lib/api"
import { getLauncherBlockReason, getUmiBlockReason } from "@/lib/runtime-preflight"
import { isTauriRuntime } from "@/lib/tauri"
import type { AppBootstrap, AppConfig, OcrStatus, TemplateConfig } from "@/lib/types"
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
import { SpinnerNumberInput } from "@/components/ui/spinner-number-input"
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

const AUTOSAVE_DELAY_MS = 400

type NoticeTone = "slate" | "emerald" | "rose"
type TemplateStatusMap = Record<string, boolean>
type ToastState = {
  message: string
  tone: NoticeTone
} | null

const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value))

export function SettingsPage() {
  const bootstrap = useRuntimeStore((state) => state.bootstrap)
  const setOcrStatus = useRuntimeStore((state) => state.setOcrStatus)
  const [config, setConfig] = useState<AppConfig | null>(null)
  const [templates, setTemplates] = useState<TemplateConfig[]>([])
  const [templateStatus, setTemplateStatus] = useState<TemplateStatusMap>({})
  const [previewTemplateId, setPreviewTemplateId] = useState<string | null>(null)
  const [testingTemplateId, setTestingTemplateId] = useState<string | null>(null)
  const [templateMessage, setTemplateMessage] = useState("")
  const [templateMessageTone, setTemplateMessageTone] = useState<NoticeTone>("slate")
  const [toast, setToast] = useState<ToastState>(null)
  const [ocrMessage, setOcrMessage] = useState("")
  const queryClient = useQueryClient()
  const initializedRef = useRef(false)
  const configAutosaveReadyRef = useRef(false)
  const configRef = useRef<AppConfig | null>(null)
  const templateAutosaveReadyRef = useRef(false)
  const templateStatusRequestIdRef = useRef(0)
  const lastSavedTemplatesRef = useRef<Record<string, TemplateConfig>>({})

  useEffect(() => {
    if (!bootstrap || initializedRef.current) {
      return
    }
    initializedRef.current = true
    const initialConfig = clone(bootstrap.config)
    const initialTemplates = clone(bootstrap.templates)
    setConfig(initialConfig)
    setTemplates(initialTemplates)
    setTemplateStatus({})
    lastSavedTemplatesRef.current = Object.fromEntries(
      initialTemplates.map((template) => [template.id, template]),
    )
  }, [bootstrap])

  const ocrTone = useMemo(() => {
    if (bootstrap?.ocrStatus.ready) return "default" as const
    if (bootstrap?.ocrStatus.started) return "secondary" as const
    return "outline" as const
  }, [bootstrap?.ocrStatus.ready, bootstrap?.ocrStatus.started])

  const previewTemplate = useMemo(
    () => templates.find((template) => template.id === previewTemplateId) ?? null,
    [previewTemplateId, templates],
  )

  const previewTemplateSrc =
    previewTemplate && templateStatus[previewTemplate.id]
      ? resolveImageSrc(bootstrap?.paths, previewTemplate.path)
      : ""

  const templatePathSignature = useMemo(
    () => templates.map((template) => `${template.id}:${template.path}`).join("|"),
    [templates],
  )
  const templateConfidenceSignature = useMemo(
    () => templates.map((template) => `${template.id}:${template.confidence}`).join("|"),
    [templates],
  )

  const describeError = (error: unknown) =>
    error instanceof Error ? error.message : String(error)

  useEffect(() => {
    if (!toast) {
      return
    }
    const timeoutId = window.setTimeout(() => {
      setToast(null)
    }, 2200)
    return () => window.clearTimeout(timeoutId)
  }, [toast])

  const showToast = useCallback((message: string, tone: NoticeTone) => {
    setToast({ message, tone })
  }, [])

  const updateBootstrapCache = useCallback((updater: (current: AppBootstrap) => AppBootstrap) => {
    queryClient.setQueryData<AppBootstrap>(["bootstrap"], (current) =>
      current ? updater(current) : current,
    )
  }, [queryClient])

  const syncTemplatesIntoCache = useCallback((savedTemplates: TemplateConfig[]) => {
    if (savedTemplates.length === 0) {
      return
    }
    const savedById = new Map(savedTemplates.map((template) => [template.id, template]))
    updateBootstrapCache((current) => ({
      ...current,
      templates: current.templates.map((template) => savedById.get(template.id) ?? template),
    }))
  }, [updateBootstrapCache])

  const persistConfig = useCallback(async (nextConfig: AppConfig) => {
    try {
      const saved = await api.configSave(nextConfig)
      updateBootstrapCache((current) => ({
        ...current,
        config: clone(saved),
      }))
      await queryClient.invalidateQueries({ queryKey: ["bootstrap"] })
      return saved
    } catch (error) {
      showToast(`设置自动保存失败：${describeError(error)}`, "rose")
      throw error
    }
  }, [queryClient, showToast, updateBootstrapCache])

  useEffect(() => {
    configRef.current = config
  }, [config])

  useEffect(() => {
    if (!configRef.current) {
      return
    }
    if (!configAutosaveReadyRef.current) {
      configAutosaveReadyRef.current = true
      return
    }
    const timeoutId = window.setTimeout(() => {
      const nextConfig = configRef.current
      if (!nextConfig) {
        return
      }
      void persistConfig(nextConfig)
    }, AUTOSAVE_DELAY_MS)
    return () => window.clearTimeout(timeoutId)
  }, [config?.game.exePath, config?.game.launchArgs, config?.umiOcr.exePath, persistConfig])

  useEffect(() => {
    if (templates.length === 0) {
      return
    }
    if (!templateAutosaveReadyRef.current) {
      templateAutosaveReadyRef.current = true
      return
    }
    const changedTemplates = templates.filter((template) => {
      const lastSaved = lastSavedTemplatesRef.current[template.id]
      return (
        !lastSaved
        || lastSaved.confidence !== template.confidence
        || lastSaved.path !== template.path
      )
    })
    if (changedTemplates.length === 0) {
      return
    }
    const timeoutId = window.setTimeout(() => {
      void (async () => {
        try {
          const savedTemplates = await Promise.all(
            changedTemplates.map((template) =>
              api.templatesSave({
                ...template,
                updatedAt: new Date().toISOString(),
              }),
            ),
          )
          for (const savedTemplate of savedTemplates) {
            lastSavedTemplatesRef.current[savedTemplate.id] = savedTemplate
          }
          syncTemplatesIntoCache(savedTemplates)
          setTemplateMessageTone("emerald")
          setTemplateMessage(
            savedTemplates.length === 1
              ? `模板 ${savedTemplates[0].name} 已自动保存`
              : `已自动保存 ${savedTemplates.length} 个模板配置`,
          )
          showToast(
            savedTemplates.length === 1
              ? `模板 ${savedTemplates[0].name} 已自动保存`
              : `已自动保存 ${savedTemplates.length} 个模板配置`,
            "emerald",
          )
        } catch (error) {
          setTemplateMessageTone("rose")
          setTemplateMessage(`模板自动保存失败：${describeError(error)}`)
          showToast(`模板自动保存失败：${describeError(error)}`, "rose")
        }
      })()
    }, AUTOSAVE_DELAY_MS)
    return () => window.clearTimeout(timeoutId)
  }, [showToast, syncTemplatesIntoCache, templateConfidenceSignature, templates])

  useEffect(() => {
    if (templates.length === 0) {
      setTemplateStatus({})
      return
    }
    const requestId = templateStatusRequestIdRef.current + 1
    templateStatusRequestIdRef.current = requestId
    void (async () => {
      const results = await Promise.all(
        templates.map(async (template) => {
          try {
            const validation = await api.templatesValidateFile(template.path)
            return [template.id, Boolean(validation.valid)] as const
          } catch {
            return [template.id, false] as const
          }
        }),
      )
      if (templateStatusRequestIdRef.current !== requestId) {
        return
      }
      setTemplateStatus(Object.fromEntries(results))
    })()
  }, [templatePathSignature, templates])

  if (!bootstrap || !config) return null

  const launcherBlockReason = getLauncherBlockReason(bootstrap)
  const ocrActionBlockReason = getUmiBlockReason(bootstrap)
  const settingsPathIssues = [launcherBlockReason, ocrActionBlockReason].filter(
    (message): message is string => Boolean(message),
  )

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

  const updateTemplate = (id: string, patch: Partial<TemplateConfig>) => {
    setTemplates((current) =>
      current.map((item) => (item.id === id ? { ...item, ...patch } : item)),
    )
  }

  const runOcrAction = async (action: () => Promise<OcrStatus>) => {
    const status = await action()
    setOcrStatus(status)
    setOcrMessage(status.message)
    await queryClient.invalidateQueries({ queryKey: ["bootstrap"] })
  }

  const handlePreviewTemplate = (template: TemplateConfig) => {
    if (!templateStatus[template.id]) {
      setTemplateMessageTone("rose")
      setTemplateMessage(`${template.name}: 模板文件不存在，无法预览`)
      showToast(`${template.name}: 模板文件不存在，无法预览`, "rose")
      return
    }
    setPreviewTemplateId(template.id)
  }

  const testTemplate = async (template: TemplateConfig) => {
    if (testingTemplateId) {
      return
    }
    setTestingTemplateId(template.id)
    try {
      await new Promise<void>((resolve) => {
        window.requestAnimationFrame(() => resolve())
      })
      const result = await api.templatesProbeMatch(template.path)
      const suffix = result.box
        ? ` @ (${result.box.x}, ${result.box.y}, ${result.box.width}x${result.box.height})`
        : ""
      setTemplateMessageTone(result.matched ? "emerald" : "rose")
      const message = `${template.name}: ${result.message}${suffix}`
      setTemplateMessage(message)
      showToast(message, result.matched ? "emerald" : "rose")
    } catch (error) {
      setTemplateMessageTone("rose")
      const message = `${template.name}: ${describeError(error)}`
      setTemplateMessage(message)
      showToast(message, "rose")
    } finally {
      setTestingTemplateId(null)
    }
  }

  const captureTemplate = async (template: TemplateConfig) => {
    try {
      const path = await api.templatesCaptureInteractive(template.slug)
      const savedTemplate = await api.templatesSave({
        ...template,
        path,
        updatedAt: new Date().toISOString(),
      })
      updateTemplate(savedTemplate.id, {
        path: savedTemplate.path,
        updatedAt: savedTemplate.updatedAt,
      })
      lastSavedTemplatesRef.current[savedTemplate.id] = savedTemplate
      syncTemplatesIntoCache([savedTemplate])
      setTemplateStatus((current) => ({ ...current, [savedTemplate.id]: true }))
      setTemplateMessageTone("emerald")
      const message = `${template.name}: 截图已保存并自动应用`
      setTemplateMessage(message)
      showToast(message, "emerald")
    } catch (error) {
      if (String(error).includes("capture cancelled")) {
        return
      }
      setTemplateMessageTone("rose")
      const message = `${template.name}: ${describeError(error)}`
      setTemplateMessage(message)
      showToast(message, "rose")
    }
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
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={Boolean(ocrActionBlockReason)}
                  onClick={() => void runOcrAction(api.ocrStart)}
                  title={ocrActionBlockReason ?? undefined}
                >
                  启动
                </Button>
                <Button size="sm" variant="secondary" onClick={() => void runOcrAction(api.ocrStop)}>
                  停止
                </Button>
                <Button
                  size="sm"
                  disabled={Boolean(ocrActionBlockReason)}
                  onClick={() => void runOcrAction(api.ocrRestart)}
                  title={ocrActionBlockReason ?? undefined}
                >
                  <RotateCw className="mr-2 size-4" />
                  重启 Umi-OCR
                </Button>
              </div>

              {(ocrActionBlockReason || ocrMessage || bootstrap.ocrStatus.message) && (
                <InlineNote
                  tone={
                    ocrActionBlockReason
                      ? "rose"
                      : bootstrap.ocrStatus.ready
                        ? "emerald"
                        : "slate"
                  }
                >
                  {ocrActionBlockReason || ocrMessage || bootstrap.ocrStatus.message}
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
                    setConfig((current) =>
                      current
                        ? {
                            ...current,
                            game: { ...current.game, exePath: path },
                          }
                        : current,
                    ),
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
                    setConfig((current) =>
                      current
                        ? {
                            ...current,
                            game: { ...current.game, launchArgs: event.target.value },
                          }
                        : current,
                    )
                  }
                />
              </div>
              <div className="md:col-span-2">
                <PathField
                  label="Umi-OCR 路径"
                  value={config.umiOcr.exePath}
                  placeholder="点击右侧按钮选择 Umi-OCR.exe"
                  onPick={() =>
                    void chooseExecutable(config.umiOcr.exePath, (path) =>
                      setConfig((current) =>
                        current
                          ? {
                              ...current,
                              umiOcr: { ...current.umiOcr, exePath: path },
                            }
                          : current,
                      ),
                    )
                  }
                />
              </div>
            </div>

            {settingsPathIssues.length > 0 ? (
              <InlineNote tone="rose">{settingsPathIssues.join("；")}</InlineNote>
            ) : null}
          </div>
        </PageSurfaceContent>
      </PageSurface>

      <PageSurface>
        <PageSurfaceContent className="gap-8">
          <SectionHeading
            eyebrow="Templates"
            title="模板资源"
            description="直接浏览完整缩略图、调整阈值、测试识别或重录截图。"
          />

          <ScrollArea className="rounded-[32px] border border-black/5 bg-white/55">
            <Table className="min-w-[1080px]">
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[160px]">预览</TableHead>
                  <TableHead>模板名称</TableHead>
                  <TableHead>分类</TableHead>
                  <TableHead className="w-[140px]">状态</TableHead>
                  <TableHead className="w-[180px]">阈值</TableHead>
                  <TableHead className="w-[220px] text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {templates.map((template) => {
                  const configured = Boolean(templateStatus[template.id])
                  const isTesting = testingTemplateId === template.id
                  const previewSrc = configured
                    ? resolveImageSrc(bootstrap.paths, template.path)
                    : ""
                  return (
                    <TableRow key={template.id}>
                      <TableCell className="py-5">
                        <button
                          type="button"
                          className="group relative flex size-24 items-center justify-center overflow-hidden rounded-[24px] border border-black/5 bg-white/75 p-2"
                          onClick={() => handlePreviewTemplate(template)}
                        >
                          {previewSrc ? (
                            <img
                              src={previewSrc}
                              alt={template.name}
                              className="max-h-full max-w-full object-contain transition duration-200 group-hover:scale-105"
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
                        <Badge
                          variant="outline"
                          className={
                            configured
                              ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                              : "border-slate-200 bg-slate-50 text-slate-500"
                          }
                        >
                          {configured ? "已配置" : "未配置"}
                        </Badge>
                      </TableCell>
                      <TableCell className="py-5">
                        <SpinnerNumberInput
                          className="h-10 rounded-full border-white/70 bg-white"
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
                            disabled={Boolean(testingTemplateId)}
                            onClick={() => void testTemplate(template)}
                          >
                            {isTesting ? (
                              <>
                                <RefreshCw className="mr-2 size-4 animate-spin" />
                                测试中...
                              </>
                            ) : (
                              <>
                                <ScanSearch className="mr-2 size-4" />
                                测试
                              </>
                            )}
                          </Button>
                          <Button
                            size="sm"
                            variant="secondary"
                            onClick={() => void captureTemplate(template)}
                          >
                            <ImageIcon className="mr-2 size-4" />
                            截图
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
            <ScrollBar orientation="horizontal" />
          </ScrollArea>

          {templateMessage ? (
            <InlineNote tone={templateMessageTone}>{templateMessage}</InlineNote>
          ) : null}
        </PageSurfaceContent>
      </PageSurface>

      <Dialog open={!!previewTemplate} onOpenChange={(open) => !open && setPreviewTemplateId(null)}>
        <DialogContent className="max-h-[92vh] max-w-5xl overflow-hidden p-0">
          <DialogHeader className="px-6 pt-6">
            <DialogTitle className="font-display text-3xl tracking-tight">
              {previewTemplate?.name}
            </DialogTitle>
            <DialogDescription className="text-sm leading-6">
              点击缩略图查看完整图片，预览会保持原始比例并按窗口大小等比缩放。
            </DialogDescription>
          </DialogHeader>
          <div className="px-6 pb-6">
            <div className="flex min-h-[240px] items-center justify-center overflow-auto rounded-[28px] border border-black/5 bg-white/60 p-4">
              {previewTemplate && previewTemplateSrc ? (
                <img
                  src={previewTemplateSrc}
                  alt={previewTemplate.name}
                  className="block h-auto max-h-[70vh] w-auto max-w-full object-contain"
                />
              ) : (
                <ImageIcon className="size-10 text-slate-300" />
              )}
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {toast ? <FloatingToast message={toast.message} tone={toast.tone} /> : null}
    </div>
  )
}

function FloatingToast({
  message,
  tone,
}: {
  message: string
  tone: NoticeTone
}) {
  const toneClass =
    tone === "emerald"
      ? "border-emerald-200/90 bg-emerald-50/95 text-emerald-800"
      : tone === "rose"
        ? "border-rose-200/90 bg-rose-50/95 text-rose-800"
        : "border-slate-200/90 bg-white/95 text-slate-700"

  return (
    <div className="pointer-events-none fixed right-6 top-6 z-50 w-full max-w-sm">
      <div className={`rounded-[24px] border px-4 py-3 text-sm leading-6 shadow-lg backdrop-blur ${toneClass}`}>
        {message}
      </div>
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
