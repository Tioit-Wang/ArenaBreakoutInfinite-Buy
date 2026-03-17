import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react"
import { useSearchParams } from "react-router-dom"
import { emitTo } from "@tauri-apps/api/event"
import { getCurrentWebviewWindow } from "@tauri-apps/api/webviewWindow"

import { api } from "@/lib/api"

type Rect = { x: number; y: number; width: number; height: number }
type Point = { x: number; y: number }

const CARD_WIDTH = 165
const CARD_HEIGHT = 212
const CARD_TOP_HEIGHT = 20
const CARD_BOTTOM_HEIGHT = 30
const CARD_MARGIN_LR = 30
const CARD_MARGIN_TB = 20
const MIN_CAPTURE_SIZE = 4

const buildRect = (start: Point, end: Point): Rect => ({
  x: Math.min(start.x, end.x),
  y: Math.min(start.y, end.y),
  width: Math.abs(end.x - start.x),
  height: Math.abs(end.y - start.y),
})

const isUsableRect = (rect: Rect) =>
  rect.width >= MIN_CAPTURE_SIZE && rect.height >= MIN_CAPTURE_SIZE

const buildFixedCardRect = (pointerScreen: Point, screenScale: number): Rect => {
  const width = Math.round(CARD_WIDTH * screenScale)
  const height = Math.round(CARD_HEIGHT * screenScale)
  return {
    x: Math.round(pointerScreen.x - width / 2),
    y: Math.round(pointerScreen.y - height / 2),
    width,
    height,
  }
}

export function CaptureOverlayPage() {
  const [searchParams] = useSearchParams()
  const mode = (searchParams.get("mode") || "template") as "template" | "goods-card"
  const slug = searchParams.get("slug") || ""
  const bigCategory = searchParams.get("bigCategory") || "杂物"
  const requestId = searchParams.get("requestId") || ""
  const rootRef = useRef<HTMLDivElement | null>(null)
  const dragStartClientRef = useRef<Point | null>(null)
  const dragStartScreenRef = useRef<Point | null>(null)
  const [pointerClient, setPointerClient] = useState<Point>({ x: 0, y: 0 })
  const [pointerScreen, setPointerScreen] = useState<Point>({ x: 0, y: 0 })
  const [dragRectClient, setDragRectClient] = useState<Rect | null>(null)
  const [busy, setBusy] = useState(false)
  const screenScale = window.devicePixelRatio || 1

  useEffect(() => {
    const current = getCurrentWebviewWindow()
    document.documentElement.classList.add("capture-overlay-active")
    document.body.classList.add("capture-overlay-active")
    void current.setFocus().catch(() => undefined)
    const timer = window.setTimeout(() => {
      rootRef.current?.focus()
    }, 0)
    return () => {
      window.clearTimeout(timer)
      document.documentElement.classList.remove("capture-overlay-active")
      document.body.classList.remove("capture-overlay-active")
    }
  }, [])

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (busy) return
      if (event.key === "Escape") {
        event.preventDefault()
        resetTemplateSelection()
        void completeCapture(null)
        return
      }
      if (mode === "goods-card" && (event.key === "Enter" || event.key === " ")) {
        event.preventDefault()
        void completeCapture(buildFixedCardRect(pointerScreen, screenScale))
      }
    }

    window.addEventListener("keydown", handleKeyDown, true)
    return () => window.removeEventListener("keydown", handleKeyDown, true)
  }, [busy, mode, pointerScreen, screenScale])

  const fixedCardRectClient = useMemo<Rect>(() => {
    return {
      x: Math.round(pointerClient.x - CARD_WIDTH / 2),
      y: Math.round(pointerClient.y - CARD_HEIGHT / 2),
      width: CARD_WIDTH,
      height: CARD_HEIGHT,
    }
  }, [pointerClient.x, pointerClient.y])

  const activeRect = mode === "goods-card" ? fixedCardRectClient : dragRectClient

  const resetTemplateSelection = () => {
    dragStartClientRef.current = null
    dragStartScreenRef.current = null
    setDragRectClient(null)
  }

  const completeCapture = async (rect: Rect | null) => {
    if (busy) return
    const current = getCurrentWebviewWindow()
    if (!rect) {
      await emitTo("main", "capture://completed", { mode, path: null, requestId })
      await current.close()
      return
    }

    setBusy(true)
    await current.hide()
    await new Promise((resolve) => setTimeout(resolve, 120))

    try {
      const path =
        mode === "goods-card"
          ? await api.goodsCaptureCardImage(bigCategory, rect)
          : await api.templatesCaptureRegion(slug, rect)
      await emitTo("main", "capture://completed", { mode, path, requestId })
    } catch {
      await emitTo("main", "capture://completed", { mode, path: null, requestId })
    } finally {
      await current.close()
    }
  }

  const toPointerScreenPoint = (event: ReactPointerEvent<HTMLDivElement>): Point => ({
    x: Math.round(event.screenX * screenScale),
    y: Math.round(event.screenY * screenScale),
  })

  return (
    <div
      ref={rootRef}
      className="relative h-screen w-screen cursor-crosshair select-none overflow-hidden bg-transparent"
      onPointerMove={(event) => {
        const screenPoint = toPointerScreenPoint(event)
        setPointerClient({ x: event.clientX, y: event.clientY })
        setPointerScreen(screenPoint)
        const dragStartClient = dragStartClientRef.current
        if (mode === "template" && dragStartClient) {
          setDragRectClient(buildRect(dragStartClient, { x: event.clientX, y: event.clientY }))
        }
      }}
      onPointerDown={(event) => {
        if (busy || event.button !== 0) return
        const screenPoint = toPointerScreenPoint(event)
        try {
          event.currentTarget.setPointerCapture(event.pointerId)
        } catch {
          // 某些环境下透明窗口可能不支持捕获，忽略后继续走普通流程。
        }
        if (mode === "template") {
          const startClient = { x: event.clientX, y: event.clientY }
          dragStartClientRef.current = startClient
          dragStartScreenRef.current = screenPoint
          setDragRectClient({ x: startClient.x, y: startClient.y, width: 0, height: 0 })
          return
        }
        void completeCapture(buildFixedCardRect(screenPoint, screenScale))
      }}
      onPointerUp={(event) => {
        if (busy || event.button !== 0) return
        try {
          if (event.currentTarget.hasPointerCapture(event.pointerId)) {
            event.currentTarget.releasePointerCapture(event.pointerId)
          }
        } catch {
          // 释放失败不影响后续收尾。
        }
        if (mode === "template") {
          const dragStartScreen = dragStartScreenRef.current
          if (!dragStartScreen) {
            resetTemplateSelection()
            return
          }
          const screenRect = buildRect(dragStartScreen, toPointerScreenPoint(event))
          if (isUsableRect(screenRect)) {
            void completeCapture(screenRect)
          } else {
            resetTemplateSelection()
          }
        }
      }}
      onPointerCancel={() => {
        if (!busy) {
          resetTemplateSelection()
        }
      }}
      onContextMenu={(event) => {
        event.preventDefault()
        if (!busy) {
          resetTemplateSelection()
          void completeCapture(null)
        }
      }}
      tabIndex={0}
    >
      <div className="pointer-events-none absolute left-1/2 top-8 -translate-x-1/2 rounded-full border border-white/65 bg-white/88 px-5 py-2 text-sm text-slate-900 shadow-xl shadow-slate-900/10 backdrop-blur-xl">
        {mode === "goods-card"
          ? "移动鼠标定位，左键或 Enter 确认，右键/ESC取消"
          : "按下左键开始框选，松开立即截图，右键/ESC取消"}
      </div>

      {mode === "template" && activeRect ? (
        <div
          className="pointer-events-none absolute bg-emerald-300/10"
          style={{
            left: `${activeRect.x}px`,
            top: `${activeRect.y}px`,
            width: `${Math.max(activeRect.width, 1)}px`,
            height: `${Math.max(activeRect.height, 1)}px`,
          }}
        />
      ) : null}

      {mode === "goods-card" && activeRect ? (
        <div
          className="pointer-events-none absolute"
          style={{
            left: `${activeRect.x}px`,
            top: `${activeRect.y}px`,
            width: `${activeRect.width}px`,
            height: `${activeRect.height}px`,
          }}
        >
          <div
            className="absolute inset-x-0 top-0"
            style={{ height: `${CARD_TOP_HEIGHT}px`, backgroundColor: "#2d7cff" }}
          />
          <div
            className="absolute inset-x-0"
            style={{
              top: `${CARD_TOP_HEIGHT}px`,
              height: `${CARD_HEIGHT - CARD_TOP_HEIGHT - CARD_BOTTOM_HEIGHT}px`,
              backgroundColor: "#ffd84d",
            }}
          />
          <div
            className="absolute inset-x-0 bottom-0"
            style={{ height: `${CARD_BOTTOM_HEIGHT}px`, backgroundColor: "#2ea043" }}
          />
          <div
            className="absolute inset-0 border"
            style={{ borderColor: "#cccccc", borderWidth: "1px" }}
          />
          <div
            className="absolute border border-dashed"
            style={{
              left: `${CARD_MARGIN_LR}px`,
              top: `${CARD_TOP_HEIGHT + CARD_MARGIN_TB}px`,
              width: `${CARD_WIDTH - CARD_MARGIN_LR * 2}px`,
              height: `${CARD_HEIGHT - CARD_TOP_HEIGHT - CARD_BOTTOM_HEIGHT - CARD_MARGIN_TB * 2}px`,
              borderColor: "#333333",
            }}
          />
        </div>
      ) : null}
    </div>
  )
}
