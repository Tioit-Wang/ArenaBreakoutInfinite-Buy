# Repository Guidelines

## Project Structure & Module Organization
- `gui_app.py`: Tkinter GUI entry; also exposed as script `wg1-gui`.
- `autobuyer.py`, `auto_clicker.py`, `price_reader.py`: automation, input, and OCR logic.
- `app_config.py`: load/save config and key mappings.
- `images/`: template and debug screenshots; `config.json`, `key_mapping.json`: runtime settings.
- `notebooks/`: exploratory work; keep out of core logic.

## Setup, Build, and Run
- Python: `3.13` (see `.python-version`).
- Install deps: `uv sync` (uses `pyproject.toml`/`uv.lock`).
- Run GUI: `uv run wg1-gui` or `uv run python gui_app.py`.
- Add a dependency: `uv add <pkg>` (dev only: `uv add --dev <pkg>`).

## Coding Style & Naming Conventions
- Follow PEP 8 with 4‑space indents; prefer type hints and docstrings (see existing modules).
- Naming: modules/files `snake_case.py`, functions/vars `snake_case`, classes `PascalCase`.
- Keep UI strings user‑facing; avoid mixing Chinese/English within the same message when possible.
- No formatter is enforced; if used, prefer Black (88 cols) and Ruff. Example: `uv run black . && uv run ruff check .`.

## Testing Guidelines
- No test suite yet. Prefer `pytest` with tests under `tests/` using `test_*.py` naming.
- Install dev tools: `uv add --dev pytest pytest-cov`.
- Run tests: `uv run pytest -q` (coverage: `uv run pytest --cov=.`).

## Commit & Pull Request Guidelines
- Messages: short, imperative, and scoped. Prefer Conventional Commits (`feat:`, `fix:`, `refactor:`). Example: `feat(gui): add ROI selector overlay`.
- Reference issues with `#<id>` when applicable.
- PRs include: clear description, rationale, before/after screenshots for GUI changes, repro steps, and any config/image updates.
- Keep changes minimal; update `README.md` and sample configs if behavior or paths change.

## Security & Configuration Tips
- Do not commit secrets or personal data in `config.json`, screenshots under `images/`, or notebooks.
- Windows: running as Administrator may be required for input automation; DPI scaling affects coordinates—standardize at 100% when capturing templates.

