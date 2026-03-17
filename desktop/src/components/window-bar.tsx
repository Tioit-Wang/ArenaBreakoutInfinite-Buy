import { useEffect, useState, type MouseEvent } from "react"
import { Copy, Minus, Square, X } from "lucide-react"

import { cn } from "@/lib/utils"
import {
  closeCurrentWindow,
  isWindowControlsAvailable,
  minimizeCurrentWindow,
  readCurrentWindowMaximized,
  startCurrentWindowDragging,
  subscribeCurrentWindowResized,
  toggleCurrentWindowMaximize,
} from "@/lib/window-controls"

const controlButtonClassName =
  "grid h-8 w-8 place-items-center rounded-full text-slate-500 transition-colors duration-200 hover:bg-white/88 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700/30"

export function WindowBar({ title }: { title: string }) {
  const [isMaximized, setIsMaximized] = useState(false)
  const hasWindowControls = isWindowControlsAvailable()

  useEffect(() => {
    if (!hasWindowControls) {
      return
    }

    let disposed = false
    let cleanup = () => {}

    const syncMaximized = async () => {
      const nextMaximized = await readCurrentWindowMaximized()
      if (!disposed) {
        setIsMaximized(nextMaximized)
      }
    }

    void syncMaximized()
    void subscribeCurrentWindowResized(() => {
      void syncMaximized()
    }).then((unlisten) => {
      if (disposed) {
        unlisten()
        return
      }
      cleanup = unlisten
    })

    return () => {
      disposed = true
      cleanup()
    }
  }, [hasWindowControls])

  const handleDragStart = (event: MouseEvent<HTMLDivElement>) => {
    if (event.button !== 0 || event.detail > 1) {
      return
    }
    event.preventDefault()
    void startCurrentWindowDragging()
  }

  const handleToggleMaximize = () => {
    if (!hasWindowControls) {
      return
    }
    void toggleCurrentWindowMaximize().then((nextMaximized) => {
      setIsMaximized(nextMaximized)
    })
  }

  return (
    <div className="fixed inset-x-0 top-0 z-40 flex justify-center px-4 pt-3 md:px-6 xl:px-8">
      <div className="mx-auto flex h-11 w-full max-w-[1440px] items-center gap-3 rounded-[22px] border border-white/60 bg-white/82 px-3 shadow-md shadow-slate-900/5 backdrop-blur-xl md:px-4">
        <div
          className="flex min-w-0 flex-1 items-center gap-3 overflow-hidden select-none"
          data-tauri-drag-region=""
          onDoubleClick={hasWindowControls ? handleToggleMaximize : undefined}
          onMouseDown={hasWindowControls ? handleDragStart : undefined}
        >
          <span className="truncate pl-1 text-sm font-semibold tracking-[0.02em] text-slate-800">
            {title}
          </span>
          <div className="h-px flex-1 bg-gradient-to-r from-slate-200/85 via-slate-100/70 to-transparent" />
        </div>

        {hasWindowControls ? (
          <div className="flex shrink-0 items-center gap-1">
            <button
              type="button"
              aria-label="最小化窗口"
              title="最小化窗口"
              className={controlButtonClassName}
              onClick={() => void minimizeCurrentWindow()}
            >
              <Minus className="size-4" />
            </button>
            <button
              type="button"
              aria-label={isMaximized ? "还原窗口" : "最大化窗口"}
              title={isMaximized ? "还原窗口" : "最大化窗口"}
              className={controlButtonClassName}
              onClick={handleToggleMaximize}
            >
              {isMaximized ? <Copy className="size-3.5" /> : <Square className="size-3.5" />}
            </button>
            <button
              type="button"
              aria-label="关闭窗口"
              title="关闭窗口"
              className={cn(
                controlButtonClassName,
                "hover:bg-rose-500 hover:text-white focus-visible:ring-rose-500/35",
              )}
              onClick={() => void closeCurrentWindow()}
            >
              <X className="size-4" />
            </button>
          </div>
        ) : null}
      </div>
    </div>
  )
}
