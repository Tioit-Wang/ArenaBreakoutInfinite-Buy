# ArenaBuyer Desktop

Rust + Tauri v2 rewrite workspace for the Windows desktop version of the ArenaBuyer automation tool.

## Current status

- React + TypeScript + Vite frontend scaffolded
- Tauri v2 shell scaffolded
- Portable `data/` directory resolved from exe sibling
- SQLite-backed storage layer initialized
- Commands wired for bootstrap, config, goods, tasks, history, legacy import, and runtime control
- Umi-OCR sidecar manager scaffolded
- Single / multi automation state-machine skeletons connected to runtime events

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

The frontend, command layer, data layer, and runtime/event plumbing are functional.
Native desktop automation modules are still scaffolded in this first pass:

- `src-tauri/src/automation/window.rs`
- `src-tauri/src/automation/capture.rs`
- `src-tauri/src/automation/input.rs`
- `src-tauri/src/automation/vision.rs`

Those modules currently return placeholders while the rewritten Rust automation flow is being migrated from the Python implementation.
