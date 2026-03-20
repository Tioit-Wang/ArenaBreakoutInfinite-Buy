import type { AppBootstrap } from "@/lib/types"

export function getLauncherBlockReason(bootstrap?: AppBootstrap): string | null {
  if (!bootstrap || bootstrap.runtimePreflight.launcherReady) {
    return null
  }
  return bootstrap.runtimePreflight.launcherMessage
}

export function getUmiBlockReason(bootstrap?: AppBootstrap): string | null {
  if (!bootstrap || bootstrap.runtimePreflight.umiReady) {
    return null
  }
  return bootstrap.runtimePreflight.umiMessage
}

export function getSingleStartBlockReason(
  bootstrap: AppBootstrap | undefined,
  itemId: string | undefined,
): string | null {
  if (!itemId) {
    return "请先选择任务物品"
  }
  return getLauncherBlockReason(bootstrap) ?? getUmiBlockReason(bootstrap)
}

export function getMultiStartBlockReason(
  bootstrap: AppBootstrap | undefined,
  enabledCount: number,
): string | null {
  if (enabledCount <= 0) {
    return "请先启用至少一个收藏商品任务"
  }
  return getLauncherBlockReason(bootstrap) ?? getUmiBlockReason(bootstrap)
}
