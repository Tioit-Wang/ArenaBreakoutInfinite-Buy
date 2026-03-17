export const __assign = Object.assign || function __assign(target: object, ...sources: object[]) {
  const output = target
  for (const source of sources) {
    for (const key in source) {
      if (Object.prototype.hasOwnProperty.call(source, key)) {
        ;(output as Record<string, unknown>)[key] = (source as Record<string, unknown>)[key]
      }
    }
  }
  return output
}

export function __rest(
  source: Record<PropertyKey, unknown>,
  excluded: Array<string | symbol>,
) {
  const target: Record<PropertyKey, unknown> = {}
  for (const key in source) {
    if (
      Object.prototype.hasOwnProperty.call(source, key) &&
      !excluded.includes(key)
    ) {
      target[key] = source[key]
    }
  }
  if (source != null && typeof Object.getOwnPropertySymbols === "function") {
    for (const symbol of Object.getOwnPropertySymbols(source)) {
      if (
        !excluded.includes(symbol as unknown as string) &&
        Object.prototype.propertyIsEnumerable.call(source, symbol)
      ) {
        target[symbol] = source[symbol]
      }
    }
  }
  return target
}

export function __spreadArray<T>(to: T[], from: T[], pack: boolean) {
  let ar: T[] | undefined
  if (pack || arguments.length === 2) {
    for (let i = 0, l = from.length; i < l; i += 1) {
      if (ar || !(i in from)) {
        if (!ar) ar = Array.prototype.slice.call(from, 0, i)
        ar[i] = from[i]
      }
    }
    return to.concat(ar || Array.prototype.slice.call(from))
  }
  return to.concat(from)
}
