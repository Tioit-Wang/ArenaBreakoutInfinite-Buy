import type { ReactNode } from "react"

import { Card, CardContent } from "@/components/ui/card"
import { cn } from "@/lib/utils"

export const minimalFieldClassName =
  "h-12 rounded-none border-x-0 border-t-0 border-b border-slate-200 bg-transparent px-0 text-lg shadow-none focus-visible:ring-0 focus-visible:ring-offset-0"

export const minimalSelectTriggerClassName =
  "h-12 rounded-none border-x-0 border-t-0 border-b border-slate-200 bg-transparent px-0 text-left text-lg shadow-none focus:ring-0 focus:ring-offset-0 data-[placeholder]:text-slate-400"

const toneClassMap = {
  slate: "text-slate-900",
  emerald: "text-emerald-800",
  amber: "text-amber-800",
  rose: "text-rose-700",
} as const

export function PageHero({
  badges,
  eyebrow,
  title,
  description,
  detail,
  actions,
  className,
}: {
  badges?: ReactNode
  eyebrow?: ReactNode
  title: ReactNode
  description?: ReactNode
  detail?: ReactNode
  actions?: ReactNode
  className?: string
}) {
  return (
    <section className={cn("px-1 pt-4 md:pt-8", className)}>
      <div className="flex flex-col gap-8 lg:flex-row lg:items-end lg:justify-between">
        <div className="max-w-3xl space-y-5">
          {badges ? <div className="flex flex-wrap items-center gap-2">{badges}</div> : null}
          <div className="space-y-3">
            {eyebrow ? (
              <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-400">
                {eyebrow}
              </p>
            ) : null}
            <div className="space-y-3">
              <h1 className="font-display text-4xl leading-tight tracking-tight text-slate-950 md:text-5xl">
                {title}
              </h1>
              {description ? (
                <p className="max-w-2xl text-base leading-7 text-slate-600">{description}</p>
              ) : null}
            </div>
          </div>
          {detail ? <p className="text-sm leading-6 text-slate-500">{detail}</p> : null}
        </div>
        {actions ? (
          <div className="flex shrink-0 flex-wrap items-center gap-3">{actions}</div>
        ) : null}
      </div>
    </section>
  )
}

export function PageSurface({
  className,
  children,
}: {
  className?: string
  children: ReactNode
}) {
  return (
    <Card
      className={cn(
        "overflow-visible rounded-[36px] border-white/60 bg-white/72 shadow-none backdrop-blur-sm",
        className,
      )}
    >
      {children}
    </Card>
  )
}

export function PageSurfaceContent({
  className,
  children,
}: {
  className?: string
  children: ReactNode
}) {
  return <CardContent className={cn("grid gap-10 p-6 md:p-10", className)}>{children}</CardContent>
}

export function SectionHeading({
  eyebrow,
  title,
  description,
  actions,
  className,
}: {
  eyebrow?: ReactNode
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
  className?: string
}) {
  return (
    <div className={cn("flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between", className)}>
      <div className="space-y-2">
        {eyebrow ? (
          <p className="text-xs font-semibold uppercase tracking-[0.28em] text-slate-400">
            {eyebrow}
          </p>
        ) : null}
        <div className="space-y-2">
          <h2 className="font-display text-3xl leading-tight tracking-tight text-slate-950">
            {title}
          </h2>
          {description ? (
            <p className="max-w-2xl text-sm leading-6 text-slate-600">{description}</p>
          ) : null}
        </div>
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-3">{actions}</div> : null}
    </div>
  )
}

export function MetricGrid({
  className,
  children,
}: {
  className?: string
  children: ReactNode
}) {
  return <div className={cn("grid gap-4", className)}>{children}</div>
}

export function MetricCard({
  label,
  value,
  tone = "slate",
  description,
  className,
}: {
  label: ReactNode
  value: ReactNode
  tone?: keyof typeof toneClassMap
  description?: ReactNode
  className?: string
}) {
  return (
    <div className={cn("space-y-2", className)}>
      <p className="text-xs uppercase tracking-[0.22em] text-slate-400">{label}</p>
      <p
        className={cn(
          "break-words text-2xl font-semibold leading-8 tracking-tight",
          toneClassMap[tone],
        )}
      >
        {value}
      </p>
      {description ? <p className="text-sm leading-6 text-slate-500">{description}</p> : null}
    </div>
  )
}

export function InlineNote({
  children,
  tone = "slate",
  className,
}: {
  children: ReactNode
  tone?: "slate" | "emerald" | "rose"
  className?: string
}) {
  const toneClass =
    tone === "emerald"
      ? "border-emerald-200/80 bg-emerald-50/80 text-emerald-800"
      : tone === "rose"
        ? "border-rose-200/80 bg-rose-50/80 text-rose-800"
        : "border-black/5 bg-white/75 text-slate-600"

  return (
    <div className={cn("rounded-[24px] border px-4 py-3 text-sm leading-6", toneClass, className)}>
      {children}
    </div>
  )
}
