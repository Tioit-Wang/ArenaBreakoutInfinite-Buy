import { useEffect, useMemo, useState, type ReactNode } from "react"
import { Outlet, useLocation, useNavigate } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { register, unregister } from "@tauri-apps/plugin-global-shortcut"
import { Boxes, History, Settings, Sparkles, Star } from "lucide-react"

import { api } from "@/lib/api"
import { isTauriRuntime, listen } from "@/lib/tauri"
import { useRuntimeStore } from "@/app/store"
import type {
  AutomationEvent,
  AutomationRunState,
  OcrStatus,
  RuntimeLogEntry,
} from "@/lib/types"
import { Card, CardContent } from "@/components/ui/card"
import { ShellToolbarProvider } from "@/components/shell-toolbar"
import { WindowBar, type WindowBarNavItem } from "@/components/window-bar"

const navItems = [
  { value: "single", to: "/single", label: "单商品抢购", icon: Sparkles },
  { value: "favorites", to: "/favorites", label: "收藏商品抢购", icon: Star },
  { value: "goods", to: "/goods", label: "物品库", icon: Boxes },
  { value: "history", to: "/history", label: "历史统计", icon: History },
  { value: "settings", to: "/settings", label: "设置", icon: Settings },
] satisfies ReadonlyArray<WindowBarNavItem & { to: string }>

const routeToTab = (pathname: string) =>
  navItems.find((item) => pathname.startsWith(item.to))?.value ?? "single"

export function ShellLayout() {
  const location = useLocation()
  const navigate = useNavigate()
  const setBootstrap = useRuntimeStore((state) => state.setBootstrap)
  const setRuntime = useRuntimeStore((state) => state.setRuntime)
  const setOcrStatus = useRuntimeStore((state) => state.setOcrStatus)
  const pushLog = useRuntimeStore((state) => state.pushLog)
  const pushProgress = useRuntimeStore((state) => state.pushProgress)
  const bootstrap = useRuntimeStore((state) => state.bootstrap)
  const toggleShortcut = bootstrap?.config.hotkeys.toggle.trim()
  const [toolbarActions, setToolbarActions] = useState<ReactNode>(null)

  const bootstrapQuery = useQuery({
    queryKey: ["bootstrap"],
    queryFn: api.bootstrap,
  })

  useEffect(() => {
    if (bootstrapQuery.data) {
      setBootstrap(bootstrapQuery.data)
    }
  }, [bootstrapQuery.data, setBootstrap])

  useEffect(() => {
    if (!isTauriRuntime()) {
      return
    }
    let mounted = true
    let cleaners: Array<() => void> = []
    void Promise.all([
      listen<RuntimeLogEntry>("automation://log", (payload) => mounted && pushLog(payload)),
      listen<AutomationRunState>("automation://state", (payload) => mounted && setRuntime(payload)),
      listen<AutomationEvent>("automation://progress", (payload) => mounted && pushProgress(payload)),
      listen<OcrStatus>("ocr://status", (payload) => mounted && setOcrStatus(payload)),
      listen<OcrStatus>("sidecar://status", (payload) => mounted && setOcrStatus(payload)),
    ]).then((unlisteners) => {
      cleaners = unlisteners
    })
    return () => {
      mounted = false
      cleaners.forEach((cleanup) => cleanup())
    }
  }, [pushLog, pushProgress, setOcrStatus, setRuntime])

  useEffect(() => {
    if (!isTauriRuntime() || !toggleShortcut) {
      return
    }
    let disposed = false
    let registered = false
    void (async () => {
      try {
        await register(toggleShortcut, (event) => {
          if (disposed || event.state !== "Pressed") {
            return
          }
          const currentRuntime = useRuntimeStore.getState().runtime
          if (currentRuntime.state === "running") {
            void api
              .automationStop()
              .then((nextRuntime) => {
                if (!disposed) {
                  setRuntime(nextRuntime)
                }
              })
              .catch(console.error)
          }
        })
        registered = true
        if (disposed) {
          await unregister(toggleShortcut)
        }
      } catch (error) {
        console.warn(`failed to register shortcut ${toggleShortcut}`, error)
      }
    })()
    return () => {
      disposed = true
      if (!registered) {
        return
      }
      void unregister(toggleShortcut).catch((error) => {
        console.warn(`failed to unregister shortcut ${toggleShortcut}`, error)
      })
    }
  }, [setRuntime, toggleShortcut])

  const activeTab = useMemo(() => routeToTab(location.pathname), [location.pathname])
  const handleNavigate = (value: string) => {
    navigate(navItems.find((item) => item.value === value)?.to ?? "/single")
  }

  return (
    <ShellToolbarProvider setActions={setToolbarActions}>
      <div className="flex h-screen flex-col overflow-hidden">
        <WindowBar
          title="ArenaBuyer Desktop"
          navItems={navItems}
          activeValue={activeTab}
          onNavigate={handleNavigate}
          actions={toolbarActions}
        />

        <main className="flex-1 overflow-y-auto">
          <div className="mx-auto flex min-h-full w-full max-w-[1440px] flex-col px-4 py-6 md:px-6 md:py-8 xl:px-8">
            <div className="flex-1">
              {bootstrapQuery.isLoading ? (
                <Card className="border-white/70 bg-white/80 shadow-xl shadow-emerald-950/5 backdrop-blur">
                  <CardContent className="p-10 text-center text-sm text-slate-600">
                    正在加载桌面工作台...
                  </CardContent>
                </Card>
              ) : bootstrapQuery.error ? (
                <Card className="border-red-200 bg-red-50 shadow-lg">
                  <CardContent className="p-6 text-sm text-red-700">
                    {String(bootstrapQuery.error)}
                  </CardContent>
                </Card>
              ) : (
                <Outlet />
              )}
            </div>
          </div>
        </main>
      </div>
    </ShellToolbarProvider>
  )
}
