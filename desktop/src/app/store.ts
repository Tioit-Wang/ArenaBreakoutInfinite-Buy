import { create } from 'zustand'

import type {
  AppBootstrap,
  AutomationEvent,
  AutomationRunState,
  OcrStatus,
  RuntimeLogEntry,
} from '../lib/types'

type RuntimeStore = {
  bootstrap?: AppBootstrap
  runtime: AutomationRunState
  ocrStatus: OcrStatus
  logs: RuntimeLogEntry[]
  progress: AutomationEvent[]
  setBootstrap: (bootstrap: AppBootstrap) => void
  setRuntime: (runtime: AutomationRunState) => void
  setOcrStatus: (ocrStatus: OcrStatus) => void
  pushLog: (log: RuntimeLogEntry) => void
  pushProgress: (event: AutomationEvent) => void
}

const idleRuntime: AutomationRunState = {
  state: 'idle',
  updatedAt: new Date().toISOString(),
}

const emptyOcrStatus: OcrStatus = {
  managed: false,
  ready: false,
  usingExisting: false,
  started: false,
  baseUrl: 'http://127.0.0.1:1224',
  message: 'not initialized',
}

export const useRuntimeStore = create<RuntimeStore>((set) => ({
  runtime: idleRuntime,
  ocrStatus: emptyOcrStatus,
  logs: [],
  progress: [],
  setBootstrap: (bootstrap) =>
    set({
      bootstrap,
      runtime: bootstrap.runtime,
      ocrStatus: bootstrap.ocrStatus,
      logs: bootstrap.recentLogs,
      progress: [],
    }),
  setRuntime: (runtime) => set({ runtime }),
  setOcrStatus: (ocrStatus) => set({ ocrStatus }),
  pushLog: (log) =>
    set((state) => ({
      logs: [log, ...state.logs].slice(0, 500),
    })),
  pushProgress: (event) =>
    set((state) => ({
      progress: [event, ...state.progress].slice(0, 200),
    })),
}))
