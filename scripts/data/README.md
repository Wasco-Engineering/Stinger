# Script output directory

Hardware and calibration scripts write CSV, JSON, plots, and optimizer artifacts here by default. This folder is **not** tracked in git.

## Usage

Run scripts from the repo root, for example:

```powershell
python scripts/quick_static_test.py --port port_a
python scripts/optimize_pressure_calibration.py --input-csv scripts/data/my_run.csv --output-dir scripts/data/my_run_out
```

## Keeping data

Copy important runs to stand-local storage (`%LOCALAPPDATA%\Stinger\<STAND_ID>\logs\` or `C:\Stinger\logs\` on deploy PCs) or to shared engineering storage on Z:. Do not commit fitted `*_error_model` blocks or machine-specific captures to the shared repo.
