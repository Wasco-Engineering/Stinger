# AGENTS.md — Stinger

This file provides repo-specific guidance for agentic coding assistants.

Repo summary: Python + PyQt6 UI, state machine logic (`transitions`), optional hardware
integration (NI-DAQmx, serial), SQL Server access via SQLAlchemy/pyodbc.

Note on editor/agent rules:
- No `.cursorrules` found.
- Workspace rules may live under `.cursor/rules/` (not committed; see `.gitignore`).
- No `.github/copilot-instructions.md` found.

---

## Environment / Setup

This repo is typically run from a virtualenv located at `.venv/`.

- Create/activate venv (Windows PowerShell):
  - `python -m venv .venv`
  - `.\.venv\Scripts\Activate.ps1`
- Install dependencies:
  - `python -m pip install -r requirements.txt`

Configuration:
- **Production / stand PCs:** `C:\Stinger\` with `stinger_config.yaml` and `quality_cal_config.yaml`
  (set machine `STINGER_CONFIG_DIR=C:\Stinger`). See `docs/DEPLOYMENT.md`.
- **Legacy:** `%LOCALAPPDATA%\Stinger\<STAND_ID>\` still resolved if present.
- **Development fallback:** repo-root YAML if no install copy exists.
- Logs default to `<config_dir>/logs/`.
- Shared builds/docs on `Z:\Engineering\Program Builds\Python Builds\Stinger\` (set `STINGER_RELEASE_ROOT`).

---

## Run / Build Commands

There is no separate “build” step; running is `python run.py`.

- Run the application:
  - `python run.py`

---

## Test Commands (pytest)

The repo uses `pytest` (and has `pytest-qt` available for Qt/UI tests).

- Run all tests:
  - `python -m pytest`
- Run tests quickly/quietly:
  - `python -m pytest -q`
- Run a single file:
  - `python -m pytest tests/test_state_machine.py`
- Run a single test (node id):
  - `python -m pytest tests/test_state_machine.py::TestPortStateMachine::test_initial_state -q`
- Run tests matching a substring:
  - `python -m pytest -k state_machine -q`
- Stop on first failure:
  - `python -m pytest -x`

Qt-specific testing tips:
- Prefer `pytest-qt` fixtures (e.g. `qtbot`) for widgets/signals.
- Avoid tests that require real hardware/DB by default.

---

## Lint / Format / Typecheck

No formatter/linter/typechecker is currently configured in the repo (no `pyproject.toml`,
`setup.cfg`, `tox.ini`, `pytest.ini`). If you add one, keep it lightweight and consistent.

Recommended (optional) tooling if/when adopted:

- Ruff (lint + import sorting):
  - Install: `python -m pip install ruff`
  - Run: `python -m ruff check .`
  - Fix: `python -m ruff check . --fix`

- Black (format):
  - Install: `python -m pip install black`
  - Run: `python -m black .`

- Mypy (types):
  - Install: `python -m pip install mypy`
  - Run: `python -m mypy app`

If you introduce these tools, document exact versions and configuration.

---

## Code Style Guidelines

### Python version / typing
- Target Python 3.10+ (see `requirements.txt`).
- Use type hints on public functions/methods.
- Prefer concrete types (`dict[str, Any]` / `list[str]`) when reasonable.
- Use `Optional[T]` (or `T | None` if the project standardizes on 3.10+ union syntax).
- Avoid `Any` where a small TypedDict/dataclass/Enum would clarify intent.

### Imports
- Group imports in this order with a blank line between groups:
  1) standard library
  2) third-party (PyQt6, SQLAlchemy, transitions, etc.)
  3) local (`from app...`)
- Prefer absolute imports from `app.*` (consistent with `run.py`).
- Avoid wildcard imports.

### Formatting
- Follow PEP 8.
- Indentation: 4 spaces.
- Strings: prefer single quotes inside Python when not user-facing; use f-strings for
  interpolation.
- Keep lines reasonably short (aim ~100 chars) unless breaking harms readability.
- Keep docstrings for modules/classes/public methods; match existing style (triple quotes,
  short summary, optional Args/Returns/Raises).

### Naming
- Modules/files: `snake_case.py`.
- Classes: `CamelCase`.
- Functions/methods: `snake_case`.
- Constants: `UPPER_SNAKE_CASE`.
- Qt signals: `snake_case` (matches existing e.g. `button_state_changed`).
- Enums:
  - Enum type: `CamelCase` (e.g. `PortState`).
  - Members: `UPPER_SNAKE_CASE`.

### Logging
- Prefer `logging.getLogger(__name__)` per module.
- Use `logger.debug/info/warning/error` instead of `print`.
- Exceptions:
  - At boundaries (e.g. `run.py`), it’s acceptable to `print` a concise fatal error before
    exit.
  - Inside libraries/services, log and raise exceptions to preserve stack traces.

### Error handling
- Don’t catch broad `Exception` unless you’re at an application boundary or you can add
  actionable context.
- Preserve the original exception context when re-raising (use `raise ... from e`).
- Validate external inputs early:
  - YAML config (`app/core/config.py`) should raise `ValueError` for missing sections.
  - UI inputs should be parsed/validated before driving hardware/state transitions.

### PyQt / UI architecture
- Keep UI code in `app/ui/` focused on presentation.
- Keep business logic/state transitions in services (`app/services/...`).
- Avoid blocking the Qt event loop:
  - Long-running hardware/DB work should be async/off-thread or chunked via timers.
- Prefer Qt signals/slots for cross-layer communication rather than direct widget mutation.

### State machines
- State machine triggers are string-based; treat them as part of the public API.
- Prefer adding new triggers/states in one place and updating tests accordingly.
- Use `PortState` / `PortSubstate` Enums rather than raw strings except where the
  `transitions` library requires string values.

### Database
- Keep DB I/O behind `app/database/`.
- Do not embed SQL in UI code.
- Make offline mode possible when DB init fails (current behavior logs a warning).
- **IGNORE ControlPressure1-5**: These legacy fields from `ProductTestParameters` are not needed and should never be used by Stinger. They are operational Alicat setpoints that are deliberately excluded from calculations.

### Hardware
- Hardware access belongs under `app/hardware/`.
- Never require physical hardware for unit tests.
- When adding hardware integrations, provide a fake/mock path when feasible for testing.

---

## Testing Guidelines

- Tests live in `tests/` and use `pytest`.
- Prefer small, deterministic tests.
- Avoid time-based flakiness; if you must use timing, keep margins generous.
- For Qt-related tests, use `pytest-qt` and avoid opening real dialogs/windows unless
  required.

---

## Safe Changes / Common Pitfalls

- Do not commit or log secrets:
  - DB credentials might be referenced by `stinger_config.yaml` or environment.
- Be careful editing `stinger_config.yaml`: it is treated as authoritative.
- Keep changes minimal and localized; avoid large refactors unless requested.
