import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from "react"

type ShellToolbarContextValue = {
  setActions: Dispatch<SetStateAction<ReactNode>>
}

const ShellToolbarContext = createContext<ShellToolbarContextValue | null>(null)

export function ShellToolbarProvider({
  children,
  setActions,
}: {
  children: ReactNode
  setActions: Dispatch<SetStateAction<ReactNode>>
}) {
  const value = useMemo(() => ({ setActions }), [setActions])
  return <ShellToolbarContext.Provider value={value}>{children}</ShellToolbarContext.Provider>
}

function useShellToolbar() {
  const context = useContext(ShellToolbarContext)
  if (!context) {
    throw new Error("useShellToolbar must be used within ShellToolbarProvider")
  }
  return context
}

export function useRegisterShellToolbar(actions: ReactNode) {
  const { setActions } = useShellToolbar()

  useEffect(() => {
    setActions(actions)
    return () => {
      setActions(null)
    }
  }, [actions, setActions])
}
