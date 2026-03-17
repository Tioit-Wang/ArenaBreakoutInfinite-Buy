import { QueryClientProvider } from "@tanstack/react-query"
import { HashRouter, Navigate, Route, Routes } from "react-router-dom"

import { queryClient } from "@/app/queryClient"
import { ShellLayout } from "@/routes/ShellLayout"
import { SingleTasksPage } from "@/routes/SingleTasksPage"
import { MultiTasksPage } from "@/routes/MultiTasksPage"
import { GoodsPage } from "@/routes/GoodsPage"
import { HistoryPage } from "@/routes/HistoryPage"
import { SettingsPage } from "@/routes/SettingsPage"
import { CaptureOverlayPage } from "@/routes/CaptureOverlayPage"

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <HashRouter>
        <Routes>
          <Route path="/capture" element={<CaptureOverlayPage />} />
          <Route element={<ShellLayout />}>
            <Route index element={<Navigate to="/single" replace />} />
            <Route path="/single" element={<SingleTasksPage />} />
            <Route path="/favorites" element={<MultiTasksPage />} />
            <Route path="/goods" element={<GoodsPage />} />
            <Route path="/history" element={<HistoryPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/single" replace />} />
          </Route>
        </Routes>
      </HashRouter>
    </QueryClientProvider>
  )
}
