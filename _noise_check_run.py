import statistics
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(r"\\tesla\Folder Redirection\Engineer\Documents\Stinger")
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import load_config
from app.hardware.labjack import LabJackController
from app.hardware.alicat import AlicatController

def build_labjack(lj_cfg, port):
    port_cfg = lj_cfg.get(port, {})
    return LabJackController({
        "device_type": lj_cfg.get("device_type", "T7"),
        "connection_type": lj_cfg.get("connection_type", "USB"),
        "identifier": lj_cfg.get("identifier", "ANY"),
        "resolution_index": lj_cfg.get("resolution_index", 9),
        "pressure_filter_alpha": 0.0,
        **port_cfg,
    })

def build_alicat(al_cfg, port):
    port_cfg = al_cfg.get(port, {})
    return AlicatController({
        "com_port": port_cfg.get("com_port"),
        "address": port_cfg.get("address"),
        "baudrate": al_cfg.get("baudrate", 115200),
        "timeout_s": al_cfg.get("timeout_s", 0.05),
        "pressure_index": al_cfg.get("pressure_index"),
        "setpoint_index": al_cfg.get("setpoint_index"),
        "gauge_index": al_cfg.get("gauge_index"),
        "barometric_index": al_cfg.get("barometric_index"),
        "pressure_units_stat": al_cfg.get("pressure_units_stat"),
        "pressure_units_group": al_cfg.get("pressure_units_group"),
        "pressure_units_value": al_cfg.get("pressure_units_value"),
        "pressure_units_override": al_cfg.get("pressure_units_override"),
        "auto_tare_on_connect": False,
    })

def measure(port, lj_cfg, al_cfg):
    print(f"\n=== {port} noise (exhaust, EMA off, pressure_raw) ===")
    labjack = build_labjack(lj_cfg, port)
    alicat = build_alicat(al_cfg, port)
    if not labjack.configure():
        print(f"LabJack configure failed: {labjack._last_status}")
        return
    if not alicat.connect():
        print(f"Alicat connect failed: {alicat._last_status}")
        labjack.cleanup()
        return
    try:
        alicat.exhaust()
        time.sleep(5.0)
        voltages, pressures, alicats = [], [], []
        start = time.perf_counter()
        for _ in range(800):
            trans = labjack.read_transducer()
            status = alicat.read_status()
            if trans is not None:
                voltages.append(trans.voltage)
                pressures.append(trans.pressure_raw)
            if status is not None:
                alicats.append(status.pressure)
        elapsed = time.perf_counter() - start
        if len(voltages) < 2:
            print("Not enough samples")
            return
        v_std = statistics.stdev(voltages)
        p_std = statistics.stdev(pressures)
        a_std = statistics.stdev(alicats) if len(alicats) > 1 else 0.0
        print(f"samples: {len(voltages)} in {elapsed:.2f}s ({len(voltages)/elapsed:.1f} Hz)")
        print(f"trans V: mean={statistics.mean(voltages):.5f} std={v_std*1000:.3f} mV p-p={(max(voltages)-min(voltages))*1000:.3f} mV")
        print(f"trans PSIA: mean={statistics.mean(pressures):.4f} std={p_std:.4f} p-p={(max(pressures)-min(pressures)):.4f}")
        print(f"alicat PSIA: mean={statistics.mean(alicats):.4f} std={a_std:.4f} p-p={(max(alicats)-min(alicats)):.4f}")
    finally:
        try:
            alicat.exhaust()
        except Exception:
            pass
        alicat.disconnect()
        labjack.cleanup()

config = load_config()
lj_cfg = config.get("hardware", {}).get("labjack", {})
al_cfg = config.get("hardware", {}).get("alicat", {})
for p in ("port_a", "port_b"):
    measure(p, lj_cfg, al_cfg)
