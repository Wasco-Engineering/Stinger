# Stinger — Build, Deploy, and Local Configuration

## Principle

| Location | What lives there | Scope |
|----------|------------------|--------|
| **Z: (or git clone)** | Source code, releases, shared docs, templates | All PCs / stands |
| **Machine-local** | `stinger_config.yaml`, `quality_cal_config.yaml`, logs, Mensor offsets, leak-test state | **One folder per stand PC** |

Never treat repo-root `stinger_config.yaml` as the deploy target on a production PC unless you are actively developing. Copy templates to the local stand directory and set environment variables (or use `deploy_init_stand.ps1`).

---

## Recommended directory layout

### Shared (computer-agnostic)

```
Z:\Engineering\Program Builds\Python Builds\Stinger\
  STINGER_STAND_PARITY_SETUP.md     # Bring-up checklist
  releases\                         # Optional: versioned PyInstaller / zip builds
  source\                           # Optional: git mirror or release tag checkouts
  templates\                        # Optional: copy of deploy\templates from repo
```

Set for documentation and release scripts:

```powershell
$env:STINGER_RELEASE_ROOT = 'Z:\Engineering\Program Builds\Python Builds\Stinger'
```

### Per-stand (computer-specific)

Default without env vars:

```
%LOCALAPPDATA%\Stinger\<STAND_ID>\
  stinger_config.yaml          # COM ports, equipment_id, error models, DB
  quality_cal_config.yaml      # Mensor COM, calibration profiles
  logs\                        # vacuum leak, mensor checks, quality cal sweeps
```

Example Stand 1:

```
%LOCALAPPDATA%\Stinger\STINGER_01\
```

Example Stand 2:

```
%LOCALAPPDATA%\Stinger\STINGER_02\
```

---

## Environment variables

| Variable | Purpose |
|----------|---------|
| `STINGER_STAND_ID` | Subfolder name under `STINGER_HOME` (e.g. `STINGER_01`) |
| `STINGER_HOME` | Override local root (default `%LOCALAPPDATA%\Stinger`) |
| `STINGER_CONFIG_DIR` | Full path to folder containing **both** YAML configs |
| `STINGER_CONFIG` | Full path to `stinger_config.yaml` only |
| `STINGER_QUALITY_CONFIG` | Full path to `quality_cal_config.yaml` only |
| `STINGER_RELEASE_ROOT` | Shared Z: root for docs/releases |

**Config resolution order** (implemented in `app/core/paths.py`):

1. `STINGER_CONFIG` / `STINGER_QUALITY_CONFIG` if set  
2. `STINGER_CONFIG_DIR\stinger_config.yaml` (and quality file) if present  
3. Frozen executable directory (PyInstaller)  
4. Repo root (developer fallback)

---

## One-time stand setup (PowerShell)

From repo root (`C:\Stinger` or a Z: checkout):

```powershell
.\scripts\deploy_init_stand.ps1 -StandId STINGER_02 -EquipmentId STINGER_02
```

Then edit secrets and COM ports in:

```
$env:LOCALAPPDATA\Stinger\STINGER_02\stinger_config.yaml
$env:LOCALAPPDATA\Stinger\STINGER_02\quality_cal_config.yaml
```

Set persistent env for that PC (User or System):

```powershell
[System.Environment]::SetEnvironmentVariable('STINGER_STAND_ID', 'STINGER_02', 'User')
[System.Environment]::SetEnvironmentVariable('STINGER_CONFIG_DIR', "$env:LOCALAPPDATA\Stinger\STINGER_02", 'User')
```

Restart terminal / Cursor after setting user env vars.

---

## Running applications

```powershell
cd C:\Stinger   # or Z:\...\source\Stinger
.\.venv\Scripts\Activate.ps1

# Verify which config is loaded
python -c "from app.core.paths import get_config_dir, get_stinger_config_path, get_quality_cal_config_path; print(get_config_dir()); print(get_stinger_config_path()); print(get_quality_cal_config_path())"

python run.py
python run_quality_cal.py
```

### EXEs on CalibrationUser desktop

```powershell
.\scripts\deploy_build_and_install.ps1 -StandId STINGER_01 -SetMachineEnv
.\scripts\deploy_install_desktop.ps1 -TargetUser CalibrationUser
```

(Run install script **as Administrator** if deploying to another user profile.)

Artifacts: `Stinger.exe`, `QualityCal.exe`, `MensorVacuumCheck.exe` in `Desktop\Stinger\`.

---

## Mensor-relative offsets

Error models in `stinger_config.yaml` (`transducer_error_model`, `alicat_error_model`) are fit vs **Mensor** using Quality Cal or:

```powershell
python scripts/mensor_vacuum_port_check.py --port port_b --discover-mensor
```

Logs: `%LOCALAPPDATA%\Stinger\<STAND_ID>\logs\mensor_vacuum_port_b_*.json`

Vacuum leak (both ports):

```powershell
python scripts/vacuum_leak_test.py hold --duration 3600 --interval 60
```

---

## Parity between stands

Use the same **commands** on Stand 1 and Stand 2; only **local YAML** and `equipment_id` differ. See `Z:\Engineering\Program Builds\Python Builds\Stinger\STINGER_STAND_PARITY_SETUP.md`.

---

## Deployment registry

Every deployed PC is listed in:

- `deploy/DEPLOYMENT_REGISTRY.md` (human-readable)
- `deploy/DEPLOYMENT_REGISTRY.yaml` (machine-readable)

Update these when adding a stand or changing `STINGER_CONFIG_DIR`.

## Related

- `deploy/templates/` — example configs (copy via `deploy_init_stand.ps1`)
- `docs/INITIAL_SETUP.md` — hardware verification
- `AGENTS.md` — dev test commands
