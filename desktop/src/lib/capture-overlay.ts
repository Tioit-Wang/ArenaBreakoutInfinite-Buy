import { listen } from "@/lib/tauri"
import { WebviewWindow } from "@tauri-apps/api/webviewWindow"

type CaptureMode = "template" | "goods-card"

type CapturePayload = {
  mode: CaptureMode
  path: string | null
  requestId: string
}

export async function openCaptureOverlay(options: {
  mode: CaptureMode
  slug?: string
  bigCategory?: string
}) {
  const label = `capture-overlay-${Date.now()}`
  const requestId = crypto.randomUUID()
  const params = new URLSearchParams()
  params.set("mode", options.mode)
  params.set("requestId", requestId)
  if (options.slug) params.set("slug", options.slug)
  if (options.bigCategory) params.set("bigCategory", options.bigCategory)

  return new Promise<string | null>((resolve, reject) => {
    let done = false
    let unlistenFn: (() => void) | undefined

    const finish = (value: string | null, error?: unknown) => {
      if (done) return
      done = true
      unlistenFn?.()
      if (error) {
        reject(error)
      } else {
        resolve(value)
      }
    }

    void (async () => {
      try {
        unlistenFn = await listen<CapturePayload>("capture://completed", (payload) => {
          if (payload.mode !== options.mode || payload.requestId !== requestId) return
          finish(payload.path)
        })
      } catch (error) {
        finish(null, error)
        return
      }

      const overlay = new WebviewWindow(label, {
        url: `/#/capture?${params.toString()}`,
        decorations: false,
        transparent: true,
        backgroundColor: { red: 0, green: 0, blue: 0, alpha: 0 },
        alwaysOnTop: true,
        skipTaskbar: true,
        fullscreen: true,
        shadow: false,
        resizable: false,
        focus: true,
        title: "Capture Overlay",
      })

      overlay.once("tauri://error", (event) => {
        finish(null, event.payload)
      })
    })()
  })
}
