import { useEffect, useState, type MouseEvent, type ReactNode } from "react"
import { Copy, Minus, Square, X, type LucideIcon } from "lucide-react"

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

const navButtonClassName =
  "flex h-9 items-center gap-2 rounded-lg px-3 text-[13px] font-medium text-slate-600 transition-colors duration-150 hover:bg-slate-900/[0.05] hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700/20"

const controlButtonClassName =
  "grid h-10 w-11 place-items-center border-l border-black/5 text-slate-500 transition-colors duration-150 hover:bg-slate-900/[0.05] hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-emerald-700/20 first:border-l-0"

export type WindowBarNavItem = {
  value: string
  label: string
  icon: LucideIcon
}

type WindowBarProps = {
  title: string
  navItems: readonly WindowBarNavItem[]
  activeValue: string
  onNavigate: (value: string) => void
  actions?: ReactNode
}

export function WindowBar({
  title,
  navItems,
  activeValue,
  onNavigate,
  actions,
}: WindowBarProps) {
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

  const dragRegionProps = {
    "data-tauri-drag-region": "",
    onDoubleClick: hasWindowControls ? handleToggleMaximize : undefined,
    onMouseDown: hasWindowControls ? handleDragStart : undefined,
  }

  return (
    <header className="z-40 border-b border-black/5 bg-[rgba(247,244,238,0.96)] shadow-[0_1px_0_rgba(15,23,42,0.04)] backdrop-blur-sm">
      <div className="mx-auto flex h-14 w-full max-w-[1440px] items-center gap-3 px-4 md:px-6 xl:px-8">
        <div className="min-w-[180px] shrink-0">
          <span className="block truncate text-sm font-semibold tracking-[0.06em] text-slate-800">
            {title}
          </span>
        </div>

        <nav
          className="flex shrink-0 items-center gap-1 rounded-xl border border-black/5 bg-white/72 p-1 shadow-[inset_0_1px_0_rgba(255,255,255,0.65)]"
          aria-label="主导航"
        >
          {navItems.map((item) => {
            const Icon = item.icon
            const isActive = item.value === activeValue
            return (
              <button
                key={item.value}
                type="button"
                aria-current={isActive ? "page" : undefined}
                className={cn(
                  navButtonClassName,
                  isActive && "bg-emerald-950 text-white shadow-sm hover:bg-emerald-950 hover:text-white",
                )}
                onClick={() => onNavigate(item.value)}
              >
                <Icon className="size-4" />
                <span className="whitespace-nowrap">{item.label}</span>
              </button>
            )
          })}
        </nav>

        <div className="h-full min-w-12 flex-1 select-none" {...dragRegionProps} />

        {actions ? (
          <div className="flex shrink-0 items-center gap-2 rounded-xl border border-black/5 bg-white/72 p-1.5 shadow-[inset_0_1px_0_rgba(255,255,255,0.65)]">
            {actions}
          </div>
        ) : null}

        {hasWindowControls ? (
          <div className="ml-1 flex shrink-0 overflow-hidden rounded-lg border border-black/5 bg-white/72">
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
    </header>
  )
}
