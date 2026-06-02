# Initial Setup (New Stand / New PC)

This guide captures first-time bring-up for the Stinger dual-port stand: software install,
hardware verification, measurement policy, and repeat-test commands. It reflects validation
performed on this stand (LabJack T7, dual Alicats on COM3, Druck 0–30 PSIA transducers).

For authoritative channel wiring see **`HARDWARE_SPEC.md`**. For day-to-day test commands see
**`TESTING.md`**.

---

## 1. Prerequisites

| Component | Notes |
|-----------|--------|
| **Windows 10/11** | Stand PC |
| **Git** | Install from [git-scm.com](https://git-scm.com); add `C:\Program Files\Git\bin` to PATH |
| **Python 3.10+** | 3.12 recommended; disable Microsoft Store `python.exe` aliases |
| **LabJack LJM** | Native driver (`LabJackM.dll`) — [LabJack LJM installer](https://labjack.com/pages/support?doc=/software-driver/ljm-software-installer-t7-t4-t8-digit/) |
| **ODBC Driver 18 for SQL Server** | Required for `PASCAL` / `WASCO_Calibration` and MAX ShopOrder |
| **Network** | Reachability to SQL Server `PASCAL` and MAX `ExactMAXWasco` (VPN/LAN as site requires) |

Hardware on the stand:

- LabJack **T7** (USB), shared across both ports
- **Two Alicat** controllers on **COM3** (FTDI USB serial), addresses **B** (Port A / left) and **A** (Port B / right)
- **Druck UNIK** transducers 0–10 V → 0–30 PSIA (AIN0/1 Port A, AIN2/3 Port B)
- Exhaust **solenoids** DIO19 (Port A), DIO18 (Port B)
- Vacuum pump on exhaust path (verify solenoid routing after install)

---

## 2. Clone and Python environment

```powershell
cd C:\Stinger
git pull

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Run unit tests (no hardware required):

```powershell
python -m pytest -q
```

Launch the application:

```powershell
python run.py
```

---

## 3. Configuration (`stinger_config.yaml`)

Machine-specific settings live in repo-root **`stinger_config.yaml`**. Key sections:

### Alicat (verified on this stand)

```yaml
hardware:
  alicat:
    baudrate: 115200
    timeout_s: 0.2
    port_a:
      com_port: "COM3"
      address: "B"    # left port
    port_b:
      com_port: "COM3"
      address: "A"    # right port
```

Both units share one COM port; the app serializes commands by address.

**Note:** After power-up, Alicats may be in **EXH/HLD** (exhaust/hold). The app calls
`cancel_hold()` during tests; if setpoints appear ignored, send `C` via Alicat terminal or
run a discovery script.

### LabJack transducers (verified — not swapped)

| Port | Alicat | Transducer AIN | Solenoid DIO |
|------|--------|----------------|--------------|
| Port A (left) | B | AIN0 / AIN1 | DIO19 |
| Port B (right) | A | AIN2 / AIN3 | DIO18 |

Set `transducer_installed: true` on both ports when physical sensors are present.

### Measurement source (auto blend)

Stinger uses **automatic** source selection for UI display and test recordings:

| Pressure (PSIA) | Source |
|-----------------|--------|
| **< 26** | Transducer only |
| **26 – 31** | Smooth linear blend (transducer → Alicat) |
| **> 31** | Alicat only (transducer saturates ~30.3 PSIA) |

If the pressure switch **activates** at **≥ 24 PSIA**, display/recording snaps to **Alicat**
to avoid a discontinuity at cutover.

```yaml
hardware:
  measurement:
    preferred_source: "auto"
    fallback_on_unavailable: true
    transducer_only_below_psi: 26.0
    alicat_only_above_psi: 31.0
    switch_pivot_min_psi: 24.0
```

Admin PIN (**2245**) can force `auto`, `transducer`, or `alicat` from the admin panel.

### Low-pressure transducer lockout

Parts with activation target **below 50 Torr** require `transducer_installed: true` on that
port. Above 50 Torr, Alicat-only operation is allowed.

### Transducer calibration (future)

Correlation testing shows a small **static bias** vs Alicat (Port A ~0.3–0.45 PSI low,
Port B ~0.15–0.25 PSI low below 30 PSIA). **Dynamic** sweeps add transient lag (acceptable).

**Planned:** compare against a third-party **Mensor** reference, then apply per-port static
offset in software. No offset is applied in the current build.

---

## 4. Transducer range and correlation (May 2026)

### Maximum transducer reading

Both transducers **saturate at ~30.3 PSIA** (~10.106 V). Above that, only Alicat is valid.

| Alicat setpoint | Port A transducer | Port B transducer |
|-----------------|-------------------|-------------------|
| 30 PSI | 29.56 | 29.87 |
| 32 PSI | 30.32 (flat) | 30.32 (flat) |

### Static offset (transducer − Alicat), below saturation

| Target | Port A | Port B |
|--------|--------|--------|
| 14 PSI | −0.29 | −0.07 |
| 22 PSI | −0.38 | −0.24 |
| 30 PSI | −0.45 | −0.17 |

### Dynamic ramp (14 → 35 PSI @ 1.5 PSI/s)

| Port | Mean offset | Std |
|------|-------------|-----|
| A | −0.33 PSI | 0.67 PSI |
| B | −0.24 PSI | 0.54 PSI |

Raw CSV: `scripts/data/transducer_alicat_battery_*.csv`

Re-run correlation battery:

```powershell
python scripts/transducer_alicat_correlation_battery.py
python scripts/transducer_alicat_correlation_battery.py --port port_a
```

---

## 4b. Vacuum pull test (May 2026)

With the vacuum pump installed, low-setpoint pulls were attempted with solenoid
routed to vacuum and Alicat setpoints 5 → 2 → 0.2 PSIA.

```powershell
python scripts/vacuum_pull_test.py
python scripts/vacuum_pull_test.py --targets 5 2 0.2
```

### Observed behavior

| Check | Result |
|-------|--------|
| Setpoint commands | **OK** — Alicat SP reached 5.00 / 2.00 on the active port |
| Alicat measured pressure | **Stuck ~13.3 PSIA** — did not track setpoint down during test |
| Transducers | **Small dips only** (~1 PSI), not full pull to setpoint |
| Cross-port movement | **Both transducers often move** when only one solenoid toggles — check for **swapped solenoid DIO** (DIO19 ↔ DIO18) or shared manifold |

### If vacuum does not pull

1. Confirm pump is running and plumbed to the solenoid **vacuum** port.
2. Listen when script prints `solenoid DIOxx VACUUM` — click should be on the **active** port only.
3. If the **other** port’s transducer moves more, swap `solenoid_dio` between `port_a` and `port_b` in config and re-test.
4. Verify `vacuum_state` / `atmosphere_state` match wiring (currently vacuum=1, atmosphere=0).
5. Ensure Alicat is out of hold/exhaust (`cancel_hold`) before low setpoints.
6. App pump protection blocks vacuum when pressure > baro + 2 PSI — vent to atmosphere first.

Positive pressurization (14–35 PSI) was verified earlier; vacuum path needs plumbing/solenoid confirmation on the floor.

## 5. Hardware verification commands

From repo root with venv activated:

```powershell
# LabJack smoke (read transducer + switch)
python tests/labjack_smoke_check.py --port port_a
python tests/labjack_smoke_check.py --port port_b

# Solenoid toggle (listen for clicks)
python tests/labjack_smoke_check.py --port port_a --toggle-solenoid

# Broad hardware scan
python scripts/hardware_test.py

# Opt-in hardware pytest
$env:STINGER_RUN_HARDWARE_TESTS = "1"
python -m pytest tests/test_hardware_integration.py -o addopts="" -q

# Vacuum / low-setpoint pull (requires vacuum pump)
python scripts/vacuum_pull_test.py

# Manual solenoid toggle UI (bench wiring)
python scripts/solenoid_test_gui.py
```

### Database connectivity

```powershell
python -c "from app.core.config import load_config; from app.database.session import initialize_database; from app.database import operations as db; c=load_config(); print('WASCO:', initialize_database(c['database'])); print('MAX:', db.is_shop_order_database_available()); print(db.validate_shop_order('stinger228'))"
```

Expected: WASCO OK, MAX OK, custom work order `SPS00000/300` resolves.

---

## 6. Solenoid and vacuum routing

Config:

```yaml
hardware:
  solenoid:
    vacuum_state: 1
    atmosphere_state: 0
    safe_vacuum_switch_threshold_psi: 2.0
```

- **Atmosphere:** DIO low (0) — exhaust to atmosphere
- **Vacuum:** DIO high (1) — exhaust to vacuum pump

The app **refuses vacuum** unless Alicat absolute pressure ≤ barometric + 2 PSI (pump
protection). Vent to atmosphere before applying high positive setpoints.

If vacuum pulls on the **wrong port**, swap `solenoid_dio` between `port_a` and `port_b` in
config (or fix wiring).

---

## 7. Bring-up checklist

Use this before calling the stand production-ready:

- [ ] Python venv + `pytest -q` passes
- [ ] LabJack discovery / transducer reads on both ports
- [ ] Both Alicats respond on COM3 @ 115200 (addresses B and A)
- [ ] Transducer ↔ Alicat mapping verified (each port’s transducer tracks its Alicat)
- [ ] Solenoid clicks on DIO19 (A) and DIO18 (B)
- [ ] Vacuum pull reaches low setpoints on **both** ports (correct solenoid routing)
- [ ] SQL Server read (PTP + optional MAX shop order)
- [ ] Full UI path: login → pressurize → one test cycle on a port
- [ ] Switch edge detection during precision sweep (real switch installed)
- [ ] Mensor reference correlation (future static offset)

---

## 8. Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| `git` not found | Add Git to PATH or use full path to `git.exe` |
| `LabJackM.dll` missing | Install LabJack LJM driver |
| Alicat no response | Wrong baud (use 115200); device in EXH — send `cancel_hold` |
| Only one Alicat on COM3 | Check RS-485 address / power |
| `LJME_DEVICE_CURRENTLY_CLAIMED` | Close other process using LabJack |
| DB offline | VPN/network; app runs in offline mode for UI |
| Setpoint ignored | Alicat in hold/exhaust — `cancel_hold()` |
| Transducer stuck ~30.3 PSI | Sensor at full scale — use Alicat above 31 PSI |
| Vacuum switch refused | Pressure too high — vent to atmosphere first |

---

## 9. Related docs

- **`HARDWARE_SPEC.md`** — DB9 / DIO pinout, Alicat plumbing
- **`LABJACK_T7_PRO.md`** — LJM discovery, driver install
- **`TESTING.md`** — pytest and hardware test markers
- **`DATABASE_CONTRACT.md`** — tables and write contract
