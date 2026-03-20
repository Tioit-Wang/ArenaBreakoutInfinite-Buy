import * as React from "react"

import { Input } from "@/components/ui/input"

type SpinnerNumberInputProps = Omit<React.ComponentProps<typeof Input>, "type">

export const SpinnerNumberInput = React.forwardRef<HTMLInputElement, SpinnerNumberInputProps>(
  ({ onKeyDown, onPaste, onDrop, onWheel, readOnly, ...props }, ref) => (
    <Input
      {...props}
      ref={ref}
      type="number"
      readOnly={readOnly ?? true}
      onKeyDown={(event) => {
        onKeyDown?.(event)
        if (event.defaultPrevented || event.key === "Tab") {
          return
        }
        event.preventDefault()
      }}
      onPaste={(event) => {
        onPaste?.(event)
        if (!event.defaultPrevented) {
          event.preventDefault()
        }
      }}
      onDrop={(event) => {
        onDrop?.(event)
        if (!event.defaultPrevented) {
          event.preventDefault()
        }
      }}
      onWheel={(event) => {
        onWheel?.(event)
        if (!event.defaultPrevented) {
          event.preventDefault()
        }
      }}
    />
  ),
)

SpinnerNumberInput.displayName = "SpinnerNumberInput"
