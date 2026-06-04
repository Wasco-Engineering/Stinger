#!/usr/bin/env python3
import csv
import sys
from pathlib import Path

p = Path(sys.argv[1]) if len(sys.argv) > 1 else sorted(Path('logs/headless_runs').glob('headless_17029_399_port_b_*.csv'))[-1]
rows = list(csv.DictReader(p.open()))
print('file', p.name, 'rows', len(rows))
for r in rows:
    pressure = float(r['transducer_psi'] or 0)
    if 6.5 <= pressure <= 10.5:
        print(f"P={pressure:.2f} no={r['switch_no']} nc={r['switch_nc']}")
