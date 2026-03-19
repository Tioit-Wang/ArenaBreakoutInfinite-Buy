import { useEffect, useMemo } from "react"
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
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { WindowBar } from "@/components/window-bar"

const navItems = [
  { value: "single", to: "/single", label: "单商品抢购", icon: Sparkles },
  { value: "favorites", to: "/favorites", label: "收藏商品抢购", icon: Star },
  { value: "goods", to: "/goods", label: "物品库", icon: Boxes },
  { value: "history", to: "/history", label: "历史统计", icon: History },
  { value: "settings", to: "/settings", label: "设置", icon: Settings },
] as const

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
  return (
    <div className="min-h-screen">
      <WindowBar title="ArenaBuyer Desktop" />

      <div className="mx-auto flex min-h-screen w-full max-w-[1440px] flex-col px-4 pb-24 pt-[4.25rem] md:px-6 xl:px-8">
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

      <div className="fixed inset-x-0 bottom-3 z-30 flex justify-center px-4">
        <Tabs
          value={activeTab}
          onValueChange={(value) =>
            navigate(navItems.find((item) => item.value === value)?.to ?? "/single")
          }
          className="w-full max-w-5xl"
        >
          <TabsList className="grid h-auto w-full grid-cols-2 gap-1.5 rounded-[24px] border border-white/55 bg-white/82 p-1.5 shadow-md shadow-slate-900/5 backdrop-blur-md md:grid-cols-5">
            {navItems.map((item) => {
              const Icon = item.icon
              return (
                <TabsTrigger
                  key={item.value}
                  value={item.value}
                  className="flex h-10 items-center gap-2 rounded-full px-3 text-[13px] font-medium text-slate-600 data-[state=active]:bg-white/95 data-[state=active]:text-emerald-900 data-[state=active]:shadow-sm"
                >
                  <Icon className="size-[15px]" />
                  <span>{item.label}</span>
                </TabsTrigger>
              )
            })}
          </TabsList>
        </Tabs>
      </div>
    </div>
  )
}
