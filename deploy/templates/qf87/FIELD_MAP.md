# QF87 Stinger template field map

Template file: `QF87_Stinger_TestStand.docx`

Placeholders use `{{TOKEN}}` syntax in paragraphs (replaced globally in body + tables).

| Token | Source |
|-------|--------|
| `{{TECHNICIAN_ID}}` | Session technician ID |
| `{{ASSET_ID}}` | Session asset ID |
| `{{EQUIPMENT_ID}}` | `test_parameters.equipment_id` from stinger config |
| `{{PROFILE_LABEL}}` | Active calibration profile label |
| `{{STARTED_AT}}` | Session start timestamp |
| `{{COMPLETED_AT}}` | Session end timestamp |
| `{{OVERALL_RESULT}}` | PASS / FAIL |
| `{{PORT_A_RESULT}}` | Left port pass/fail + p99 summary |
| `{{PORT_A_DETAIL}}` | Left port fit / model applied lines |
| `{{PORT_B_RESULT}}` | Right port pass/fail + p99 summary |
| `{{PORT_B_DETAIL}}` | Right port fit / model applied lines |
| `{{CONFIG_CHANGES}}` | Summary of applied error models per port |

When Document Control releases a new QF87 rev, replace `QF87_Stinger_TestStand.docx` and keep token names stable.
