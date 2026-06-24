# Database Contract (Stinger)

This document describes the **database contract** Stinger relies on: what we read, what we write, and how values are interpreted. It avoids speculative new tables.

## Key principles (important)

- **Treat fixed-width fields as padded**:
  - In this database, many columns are `nchar` (fixed width). Always `strip()` whitespace when reading.
- **Normalize `SequenceID` for joins**:
  - `ProductTestParameters.SequenceID` is `nchar(4)` but is commonly stored as non-zero-padded values (e.g. `'300 '` → `RTRIM` = `300`).
  - `OrderCalibrationDetail.SequenceID` is `nchar(4)` and is commonly stored zero-padded (e.g. `'0300'`).
  - To join PTP ↔ results, normalize by integer value: `seq_int = int(RTRIM(SequenceID))`, then format as needed (`f\"{seq_int:04d}\"`).

## Tables Stinger reads

### `ProductTestParameters` (PTP)

PTP is a **key-value table**:

- **Key**: `(PartID, SequenceID, ParameterName)`
- **Value**: `ParameterValue` (string)

Stinger uses PTP to load:

- **Targets + windows**
  - `ActivationTarget`
  - `IncreasingLowerLimit`, `IncreasingUpperLimit`
  - `DecreasingLowerLimit`, `DecreasingUpperLimit`
  - `ResetBandLowerLimit`, `ResetBandUpperLimit`
  - `TargetActivationDirection` (e.g., `Increasing` or `Decreasing`)
- **Control shaping**
  - `ControlPressure1..N` (legacy; **IGNORE - not needed, never used by Stinger**)
  - `RateTarget1..N` (legacy; not used by Stinger)
- **Units**
  - `UnitsOfMeasure` (numeric code; see mapping below)
- **Electrical mapping (if present / used)**
  - `CommonTerminal`, `NormallyOpenTerminal`, `NormallyClosedTerminal`

#### Units of Measure (PTP value → DB string)

In this database:

- `ProductTestParameters.ParameterValue` stores `UnitsOfMeasure` as a **string value** (often numeric-looking).
- `OrderCalibrationDetail.UnitsOfMeasure` is `nchar(20)` and stores a **unit string** (e.g., `PSI`, `INHG`, `Torr`, `mmHg @ 0° C`).

Observed dominant mappings (derived by joining PTP ↔ saved results on PartID+Sequence):

- PTP `UnitsOfMeasure = 1` → `PSI`
- PTP `UnitsOfMeasure = 15` → `INHG`
- PTP `UnitsOfMeasure = 19` → `mmHg @ 0° C`
- PTP `UnitsOfMeasure = 21` → `Torr`

**Write rule (practical)**:

- When writing a result row, set `OrderCalibrationDetail.UnitsOfMeasure` to the same string already used for that PartID+Sequence in recent history when available; otherwise fall back to the mapping above.

### `OrderCalibrationMaster` (work order context)

Used to auto-fill work-order context after operator enters **Shop Order**:

- Part ID: `PartID` (`nchar(30)`)
- Sequence ID (operation/step): `LastSequenceCalibrated` (`nchar(4)`)
- Order quantity: `OrderQTY` (`int`)

Other useful columns include `OperatorID`, `EquipmentID`, `StartTime`, `FinishTime`, and `CalibrationDate`.

## Tables Stinger writes

### `OrderCalibrationDetail` (per-unit results)

Stinger records per-unit results for a Shop Order. In this schema, a “unit” is identified by:

`(ShopOrder, PartID, SequenceID, SerialNumber)`

Known fields used/observed:

- Identifiers: `ShopOrder`, `SequenceID`, `PartID`, `SerialNumber`, `InspectionDate`, `OperatorID`, `EquipmentID`
- Identifiers (DB key nuance): `ActivationID` (see below)
- Measurements:
  - `IncreasingActivation` (**increasing-direction switching point**)
  - `DecreasingDeactivation` (**decreasing-direction switching point**)
  - `IncreasingGap` (meaning TBD)
  - `DecreasingGap` (meaning TBD)
  - `MaxPressureAchieved` (maximum test/display-reference pressure observed during the run)
  - `GageReferenceDiff` (barometric/reference pressure captured for report parity)
- Evaluation:
  - `InSpec` (bit) — expected to represent overall PASS/FAIL
- Units:
  - `UnitsOfMeasure` (string)

`SerialNumber` is stored as an `int` (1, 2, 3, ...).

#### `ActivationID` (attempt index)

`OrderCalibrationDetail` has a composite primary key:

`(ShopOrder, SequenceID, PartID, SerialNumber, ActivationID)`

Observed behavior in production history:

- `ActivationID` is **almost always 1**
- A small number of units have `ActivationID = 2`, and those rows appear to represent a second recorded attempt for the same unit.

**Stinger policy (aligned with “up to 3 attempts”)**:

- Treat `ActivationID` as an **attempt index** for a given unit.
- First recorded attempt for a unit uses `ActivationID = 1`
- Each subsequent attempt inserts a new row with `ActivationID = previous_max + 1` (up to 3 for normal policy; higher values only if allowed as an override).
- “Latest result” for the unit is the row with the **highest ActivationID**.

#### Retest behavior (overwrite)

In operator terms, retesting "overwrites" the prior result.

In database terms, Stinger should **UPDATE the existing row** for that unit (same `ActivationID`), replacing the measured values with the new attempt's results.

Practical unit identifier:

- Key a unit by `(ShopOrder, PartID, SequenceID, SerialNumber, ActivationID)`
- For most units, `ActivationID = 1`

#### “Write on every attempt” (policy)

- PASS requires an explicit **Record Success** tap (operator intent).
- FAIL attempts should still be **written** (even if the operator immediately chooses `Retest`), so attempt history is preserved and the 3-attempt policy can be enforced/remembered.

## Evaluation rules (bands)

Stinger evaluates measured switching points against acceptance windows **by pressure direction**:

- **Increasing band**: `IncreasingLowerLimit` to `IncreasingUpperLimit`
  - Acceptable range for the switching point observed while pressure is **increasing**
  - Lower bound may be “no minimum” (conceptually \(-\infty\))
- **Decreasing band**: `DecreasingLowerLimit` to `DecreasingUpperLimit`
  - Acceptable range for the switching point observed while pressure is **decreasing**
  - Upper bound may be “no maximum” (conceptually \(+\infty\))
- **Reset band**: `ResetBandLowerLimit` to `ResetBandUpperLimit`
  - Reset behavior constraint (often unconstrained)

**PASS**: the “increasing-direction” switching point is in the increasing band **AND** the “decreasing-direction” switching point is in the decreasing band.

Confirmed behavior from correlating SPS parts in PTP vs saved results:

- Evaluation is **direction-based**:
  - `IncreasingActivation` is checked against the **Increasing** band
  - `DecreasingDeactivation` is checked against the **Decreasing** band
- `TargetActivationDirection` determines which direction is considered “activation” vs “deactivation” for operator wording, but **the stored columns remain direction-based**.

## Progress + serial allocation (contract intent)

Operators expect serial numbering to be mostly automatic. The system should be able to:

- Compute “done / total” for a Shop Order by counting **distinct SerialNumber** values in `OrderCalibrationDetail` for that Shop Order/Part/Sequence (do not count attempts as separate units).
- Determine the **next available serial number** that is not already tested (and not currently in-progress on the other port)

Serial numbers are simple integers (`1`, `2`, `3`, …). The product’s label serial is separate and is not the “SerialNumber” field here.

## Offline mode

Stinger is **online-first** (SQL Server is the source of truth for work orders and parameters), with a machine-local SQLite cache for stand continuity when SQL is unavailable.

- Maintain a local SQLite cache at `<config_dir>/stinger_local.sqlite3` by default:
  - `OrderCalibrationMaster` rows used by the stand
  - `ProductTestParameters` rows used by the stand
  - `OrderCalibrationDetail` writes performed by the stand, with sync status metadata
- When the DB is unavailable:
  - Read work-order context and PTP from the local cache if present
  - Record results to the local queue using the same result fields as SQL Server
- When connectivity is restored:
  - Upload queued result rows to SQL Server after ensuring the matching master row exists
  - Current implementation marks a row as conflict instead of overwriting when the same unit/attempt already exists remotely under another equipment ID or sequence representation
