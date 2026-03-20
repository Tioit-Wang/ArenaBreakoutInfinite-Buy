export type PathsSnapshot = {
  rootDir: string
  dataDir: string
  imagesDir: string
  assetsDir: string
  debugDir: string
  logsDir: string
  cacheDir: string
  dbPath: string
  bundledUmiDir?: string | null
  bundledResourcesDir?: string | null
}

export type GameConfig = {
  exePath: string
  launchArgs: string
  startupTimeoutSec: number
  launcherTimeoutSec: number
  launchClickDelaySec: number
}

export type UmiOcrConfig = {
  baseUrl: string
  timeoutSec: number
  autoStart: boolean
  startupWaitSec: number
  exePath: string
}

export type HotkeyConfig = {
  toggle: string
}

export type DebugConfig = {
  enabled: boolean
  saveRoiOnFail: boolean
  overlaySec: number
  stepSleep: number
  saveOverlayImages: boolean
  saveSingleCaptureImages: boolean
  saveMultiCaptureImages: boolean
}

export type AvgPriceAreaConfig = {
  distanceFromBuyTop: number
  height: number
  scale: number
}

export type MultiSnipeTuning = {
  buyResultTimeoutSec: number
  buyResultPollStepSec: number
  buyClickSettleSec: number
  pollStepSec: number
  probeStepSec: number
  postClickWaitSec: number
  roiPreCaptureWaitSec: number
  ocrMaxWorkers: number
  ocrRoundWindowSec: number
  ocrRoundStepSec: number
  ocrRoundFailLimit: number
  postCloseDetailSec: number
  postSuccessClickSec: number
  postNavSec: number
  detailOpenSettleSec: number
  detailCacheVerifyTimeoutSec: number
  anchorStabilizeSec: number
  ocrMissPenaltyThreshold: number
  penaltyConfirmDelaySec: number
  penaltyWaitSec: number
  fastChainMode: boolean
  fastChainMax: number
  fastChainIntervalMs: number
  relocateAfterFail: number
  roundCooldownEveryNRounds: number
  roundCooldownMinutes: number
  restockRetriggerWindowMinutes: number
  restockMissCooldownMinutes: number
}

export type AppConfig = {
  game: GameConfig
  umiOcr: UmiOcrConfig
  hotkeys: HotkeyConfig
  debug: DebugConfig
  avgPriceArea: AvgPriceAreaConfig
  multiSnipeTuning: MultiSnipeTuning
}

export type TemplateConfig = {
  id: string
  slug: string
  name: string
  kind: string
  path: string
  confidence: number
  notes?: string | null
  createdAt: string
  updatedAt: string
}

export type TemplateFileValidationResult = {
  valid: boolean
  width?: number | null
  height?: number | null
  message: string
}

export type MatchBoxSnapshot = {
  x: number
  y: number
  width: number
  height: number
}

export type TemplateProbeResult = {
  matched: boolean
  confidence: number
  box?: MatchBoxSnapshot | null
  message: string
}

export type GoodsRecord = {
  id: string
  name: string
  searchName: string
  bigCategory: string
  subCategory: string
  exchangeable: boolean
  craftable: boolean
  favorite: boolean
  imagePath: string
  price?: number | null
  createdAt: string
  updatedAt: string
}

export type SingleTaskRecord = {
  id: string
  itemId: string
  itemName: string
  enabled: boolean
  priceThreshold: number
  pricePremiumPct: number
  restockPrice: number
  restockPremiumPct: number
  targetTotal: number
  purchased: number
  durationMin: number
  timeStart?: string | null
  timeEnd?: string | null
  orderIndex: number
  createdAt: string
  updatedAt: string
}

export type MultiTaskRecord = {
  id: string
  itemId: string
  name: string
  enabled: boolean
  price: number
  premiumPct: number
  purchaseMode: string
  targetTotal: number
  purchased: number
  orderIndex: number
  imagePath: string
  bigCategory: string
  createdAt: string
  updatedAt: string
}

export type PriceHistoryRecord = {
  id: string
  itemId: string
  itemName: string
  category?: string | null
  price: number
  observedAt: string
  observedAtEpoch?: number
}

export type PurchaseHistoryRecord = {
  id: string
  itemId: string
  itemName: string
  category?: string | null
  price: number
  qty: number
  amount: number
  taskId?: string | null
  taskName?: string | null
  usedMax?: boolean | null
  purchasedAt: string
}

export type HistorySummary = {
  priceCount: number
  priceMin: number
  priceMax: number
  priceAvg: number
  latestPrice: number
  purchaseCount: number
  purchaseQty: number
  purchaseAmount: number
  purchaseAvg: number
}

export type ItemPriceTrendPoint = {
  day: string
  minPrice: number
  maxPrice: number
  avgPrice: number
  latestPrice: number
  sampleCount: number
}

export type ItemPriceTrendResponse = {
  itemId: string
  itemName: string
  from: string
  to: string
  points: ItemPriceTrendPoint[]
  latestPrice?: number | null
  rangeMinPrice?: number | null
  rangeMaxPrice?: number | null
  rangeAvgPrice?: number | null
}

export type LegacyCandidate = {
  root: string
  displayName: string
  files: string[]
  outputDir?: string | null
}

export type ImportReport = {
  id: string
  sourceRoot: string
  status: string
  goodsImported: number
  singleTasksImported: number
  multiTasksImported: number
  priceRowsImported: number
  purchaseRowsImported: number
  finishedAt: string
}

export type OcrStatus = {
  managed: boolean
  ready: boolean
  usingExisting: boolean
  started: boolean
  baseUrl: string
  exePath?: string | null
  message: string
}

export type AutomationRunState = {
  sessionId?: string | null
  mode?: string | null
  state: string
  detail?: string | null
  startedAt?: string | null
  updatedAt: string
}

export type AutomationEvent = {
  sessionId: string
  mode: string
  kind: string
  level: string
  message: string
  step?: string | null
  progress?: number | null
  payload: Record<string, unknown>
  createdAt: string
}

export type RuntimeLogEntry = {
  id?: number | null
  sessionId?: string | null
  level: string
  scope: string
  message: string
  createdAt: string
  payload: Record<string, unknown>
}

export type RuntimePreflightStatus = {
  launcherReady: boolean
  launcherMessage: string
  umiReady: boolean
  umiMessage: string
}

export type AppBootstrap = {
  paths: PathsSnapshot
  config: AppConfig
  templates: TemplateConfig[]
  goods: GoodsRecord[]
  singleTasks: SingleTaskRecord[]
  multiTasks: MultiTaskRecord[]
  runtime: AutomationRunState
  ocrStatus: OcrStatus
  runtimePreflight: RuntimePreflightStatus
  legacyCandidates: LegacyCandidate[]
  recentLogs: RuntimeLogEntry[]
}
