import type { UnlistenFn } from "@tauri-apps/api/event"
import type { Window } from "@tauri-apps/api/window"

import { isTauriRuntime } from "@/lib/tauri"

type WindowControlsHandle = Pick<
  Window,
  "close" | "isMaximized" | "minimize" | "onResized" | "startDragging" | "toggleMaximize"
>

const noopUnlisten: UnlistenFn = () => {}

let currentWindowPromise: Promise<WindowControlsHandle | null> | undefined

async function getCurrentAppWindow(): Promise<WindowControlsHandle | null> {
  if (!isWindowControlsAvailable()) {
    return null
  }

  if (!currentWindowPromise) {
    currentWindowPromise = import("@tauri-apps/api/window")
      .then(({ getCurrentWindow }) => getCurrentWindow())
      .catch((error) => {
        console.warn("failed to load Tauri window api", error)
        return null
      })
  }

  return currentWindowPromise
}

async function runWithWindow<T>(
  actionName: string,
  action: (appWindow: WindowControlsHandle) => Promise<T>,
  fallback: T,
): Promise<T> {
  const appWindow = await getCurrentAppWindow()
  if (!appWindow) {
    return fallback
  }

  try {
    return await action(appWindow)
  } catch (error) {
    console.warn(`window control action failed: ${actionName}`, error)
    return fallback
  }
}

export function isWindowControlsAvailable() {
  return isTauriRuntime()
}

export async function startCurrentWindowDragging(): Promise<void> {
  await runWithWindow(
    "startDragging",
    async (appWindow) => {
      await appWindow.startDragging()
    },
    undefined,
  )
}

export async function minimizeCurrentWindow(): Promise<void> {
  await runWithWindow(
    "minimize",
    async (appWindow) => {
      await appWindow.minimize()
    },
    undefined,
  )
}

export async function closeCurrentWindow(): Promise<void> {
  await runWithWindow(
    "close",
    async (appWindow) => {
      await appWindow.close()
    },
    undefined,
  )
}

export async function readCurrentWindowMaximized(): Promise<boolean> {
  return runWithWindow("isMaximized", (appWindow) => appWindow.isMaximized(), false)
}

export async function toggleCurrentWindowMaximize(): Promise<boolean> {
  return runWithWindow(
    "toggleMaximize",
    async (appWindow) => {
      await appWindow.toggleMaximize()
      return appWindow.isMaximized()
    },
    false,
  )
}

export async function subscribeCurrentWindowResized(
  handler: () => void,
): Promise<UnlistenFn> {
  return runWithWindow(
    "onResized",
    (appWindow) => appWindow.onResized(() => handler()),
    noopUnlisten,
  )
}
