import { invoke as tauriInvoke } from '@tauri-apps/api/core'
import { listen as tauriListen, type UnlistenFn } from '@tauri-apps/api/event'

export const isTauriRuntime = () =>
  typeof window !== 'undefined' &&
  '__TAURI_INTERNALS__' in window

export async function invoke<T>(command: string, args?: Record<string, unknown>): Promise<T> {
  return tauriInvoke<T>(command, args)
}

export async function listen<T>(
  eventName: string,
  handler: (payload: T) => void,
): Promise<UnlistenFn> {
  return tauriListen<T>(eventName, (event) => handler(event.payload))
}
