import { convertFileSrc } from "@tauri-apps/api/core"

import type { PathsSnapshot } from "@/lib/types"
import { isTauriRuntime } from "@/lib/tauri"

export function resolveImageSrc(paths: PathsSnapshot | undefined, assetPath: string) {
  if (!paths || !assetPath) {
    return ""
  }

  const normalized = assetPath.replaceAll("\\", "/")
  const absolute = /^[A-Za-z]:\//.test(normalized) || normalized.startsWith("//")
    ? normalized
    : `${paths.dataDir.replaceAll("\\", "/")}/${normalized.replace(/^\.?\//, "")}`

  return isTauriRuntime() ? convertFileSrc(absolute) : absolute
}
