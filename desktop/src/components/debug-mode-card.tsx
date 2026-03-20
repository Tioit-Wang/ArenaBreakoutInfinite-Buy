import { InlineNote } from "@/components/minimal-page"
import { cn } from "@/lib/utils"

export type DebugModeTone = "slate" | "emerald" | "rose"

type DebugModeCardProps = {
  title: string
  description: string
  enabled: boolean
  saving: boolean
  isRunning: boolean
  onToggle: () => void
  message: string
  messageTone: DebugModeTone
  defaultMessage: string
  ariaLabel: string
}

export function DebugModeCard({
  title,
  description,
  enabled,
  saving,
  isRunning,
  onToggle,
  message,
  messageTone,
  defaultMessage,
  ariaLabel,
}: DebugModeCardProps) {
  return (
    <div className="grid gap-4 rounded-[28px] border border-black/5 bg-white/60 px-5 py-5">
      <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-400">
            Debug Mode
          </p>
          <h3 className="font-display text-2xl leading-tight tracking-tight text-slate-950">
            {title}
          </h3>
          <p className="max-w-2xl text-sm leading-6 text-slate-600">
            {description}
          </p>
        </div>

        <button
          type="button"
          role="switch"
          aria-checked={enabled}
          aria-label={ariaLabel}
          onClick={onToggle}
          disabled={saving || isRunning}
          className={cn(
            "inline-flex min-h-12 items-center gap-3 self-start rounded-full border px-4 py-2 text-sm font-medium transition md:self-center",
            enabled
              ? "border-emerald-200 bg-emerald-50 text-emerald-800"
              : "border-slate-200 bg-white text-slate-600",
            (saving || isRunning) && "cursor-not-allowed opacity-60",
          )}
        >
          <span
            className={cn(
              "relative inline-flex h-7 w-12 shrink-0 rounded-full transition",
              enabled ? "bg-emerald-500" : "bg-slate-300",
            )}
          >
            <span
              className={cn(
                "absolute top-1 size-5 rounded-full bg-white shadow-sm transition",
                enabled ? "left-6" : "left-1",
              )}
            />
          </span>
          <span>
            {saving
              ? "保存中..."
              : enabled
                ? "已开启"
                : "已关闭"}
          </span>
        </button>
      </div>

      <InlineNote tone={message ? messageTone : "slate"}>
        {message || defaultMessage}
      </InlineNote>
    </div>
  )
}
