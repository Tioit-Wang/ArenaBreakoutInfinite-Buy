import type {
  AppBootstrap,
  AppConfig,
  AutomationRunState,
  GoodsRecord,
  HistorySummary,
  ImportReport,
  LegacyCandidate,
  MultiTaskRecord,
  OcrStatus,
  PriceHistoryRecord,
  PurchaseHistoryRecord,
  SingleTaskRecord,
  TemplateConfig,
} from './types'
import { invoke } from './tauri'

export const api = {
  bootstrap: () => invoke<AppBootstrap>('bootstrap'),
  configGet: () => invoke<AppConfig>('config_get'),
  configSave: (config: AppConfig) => invoke<AppConfig>('config_save', { config }),
  goodsList: () => invoke<GoodsRecord[]>('goods_list'),
  goodsSave: (goods: GoodsRecord) => invoke<GoodsRecord>('goods_save', { goods }),
  goodsDelete: (id: string) => invoke<void>('goods_delete', { id }),
  singleTasksList: () => invoke<SingleTaskRecord[]>('single_tasks_list'),
  singleTasksSave: (task: SingleTaskRecord) =>
    invoke<SingleTaskRecord>('single_tasks_save', { task }),
  singleTasksReorder: (taskIds: string[]) =>
    invoke<SingleTaskRecord[]>('single_tasks_reorder', { taskIds }),
  singleTasksDelete: (id: string) => invoke<void>('single_tasks_delete', { id }),
  multiTasksList: () => invoke<MultiTaskRecord[]>('multi_tasks_list'),
  multiTasksSave: (task: MultiTaskRecord) =>
    invoke<MultiTaskRecord>('multi_tasks_save', { task }),
  multiTasksReorder: (taskIds: string[]) =>
    invoke<MultiTaskRecord[]>('multi_tasks_reorder', { taskIds }),
  multiTasksDelete: (id: string) => invoke<void>('multi_tasks_delete', { id }),
  templatesList: () => invoke<TemplateConfig[]>('templates_list'),
  templatesSave: (template: TemplateConfig) =>
    invoke<TemplateConfig>('templates_save', { template }),
  templatesTest: (path: string) =>
    invoke<{ matched: boolean; confidence: number; message: string }>('templates_test', { path }),
  templatesImportImage: (slug: string, sourcePath: string) =>
    invoke<string>('templates_import_image', { slug, sourcePath }),
  templatesCaptureRegion: (
    slug: string,
    region: { x: number; y: number; width: number; height: number },
  ) => invoke<string>('templates_capture_region', { slug, region }),
  goodsImportImage: (sourcePath: string, bigCategory: string) =>
    invoke<string>('goods_import_image', { sourcePath, bigCategory }),
  goodsCaptureCardImage: (
    bigCategory: string,
    region: { x: number; y: number; width: number; height: number },
  ) => invoke<string>('goods_capture_card_image', { bigCategory, region }),
  historyQueryPrices: (itemId?: string, limit = 200) =>
    invoke<PriceHistoryRecord[]>('history_query_prices', { itemId, limit }),
  historyQueryPurchases: (itemId?: string, limit = 200) =>
    invoke<PurchaseHistoryRecord[]>('history_query_purchases', { itemId, limit }),
  historyQuerySummary: (itemId?: string) =>
    invoke<HistorySummary>('history_query_summary', { itemId }),
  legacyScan: () => invoke<LegacyCandidate[]>('legacy_scan'),
  legacyImport: (sourceRoot: string) => invoke<ImportReport>('legacy_import', { sourceRoot }),
  automationStartSingle: () => invoke<AutomationRunState>('automation_start_single'),
  automationStartMulti: () => invoke<AutomationRunState>('automation_start_multi'),
  automationPause: () => invoke<AutomationRunState>('automation_pause'),
  automationResume: () => invoke<AutomationRunState>('automation_resume'),
  automationStop: () => invoke<AutomationRunState>('automation_stop'),
  ocrStatus: () => invoke<OcrStatus>('ocr_status'),
  ocrStart: () => invoke<OcrStatus>('ocr_start'),
  ocrStop: () => invoke<OcrStatus>('ocr_stop'),
  ocrRestart: () => invoke<OcrStatus>('ocr_restart'),
}

export type RuntimeStatusPayload = OcrStatus | AutomationRunState
