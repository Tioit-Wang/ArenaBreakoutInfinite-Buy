# ArenaBuyer Desktop

Rust + Tauri v2 rewrite workspace for the Windows desktop version of the ArenaBuyer automation tool.

## Current status

- React + TypeScript + Vite frontend wired
- Tauri v2 shell wired
- Portable `data/` directory resolved from exe sibling
- SQLite-backed storage layer initialized
- Commands wired for bootstrap, config, goods, tasks, history, legacy import, and runtime control
- Umi-OCR sidecar manager implemented
- Native Windows interactive capture selectors implemented for template capture and goods-card capture
- Template tooling split into file validation and live match probing
- Single-item automation flow implemented with runtime events, OCR, template matching, and native input
- Multi-item automation flow implemented for favorites refresh, card scanning, OCR price reads, and detail purchases

## Run

```bash
cd desktop
npm install
npx tauri dev
```

## Build

```bash
cd desktop
npx tauri build --debug --no-bundle
```

## Important note

The desktop rewrite no longer treats the automation layer as placeholder-only.

- Template / goods screenshots use a native Windows selection window instead of a transparent Tauri overlay route.
- `src-tauri/src/automation/vision.rs` performs real template matching with `imageproc::template_matching`.
- `src-tauri/src/automation/single_runner.rs` drives the single-item market workflow end to end.
- `src-tauri/src/automation/multi_runner.rs` drives the favorites-based multi-item workflow end to end.

There are still Windows-specific verification risks around real game UI timing, DPI scaling, and OCR stability, so desktop automation changes should still be validated on a live Windows machine before release.
