"""Session models for the quality calibration workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


PORT_LABELS = {
    "port_a": "Left Port",
    "port_b": "Right Port",
}


@dataclass(slots=True)
class LeakCheckResult:
    port_id: str
    target_psia: float
    duration_s: float
    initial_alicat_psia: float
    final_alicat_psia: float
    initial_transducer_psia: Optional[float]
    final_transducer_psia: Optional[float]
    alicat_leak_rate_psi_per_min: float
    transducer_leak_rate_psi_per_min: Optional[float]
    passed: Optional[bool]
    measured_at: datetime = field(default_factory=datetime.now)

    @property
    def port_label(self) -> str:
        return PORT_LABELS.get(self.port_id, self.port_id)


@dataclass(slots=True)
class CalibrationPointResult:
    port_id: str
    point_index: int
    point_total: int
    target_psia: float
    route: str
    mensor_psia: Optional[float]
    alicat_psia: Optional[float]
    transducer_psia: Optional[float]
    deviation_psia: Optional[float]
    passed: bool
    settle_duration_s: float
    hold_duration_s: float
    sample_count: int
    corrected_deviation_psia: Optional[float] = None
    mensor_used: bool = True
    measured_at: datetime = field(default_factory=datetime.now)


@dataclass(slots=True)
class PortFitSummary:
    port_id: str
    sweep_csv_path: Optional[Path] = None
    transducer_p99_abs_torr: Optional[float] = None
    alicat_p99_abs_torr: Optional[float] = None
    transducer_passed: bool = False
    alicat_passed: bool = False
    applied_to_stinger_config: bool = False
    transducer_error_model: Optional[Dict[str, Any]] = None
    alicat_error_model: Optional[Dict[str, Any]] = None


@dataclass(slots=True)
class PortCalibrationResult:
    port_id: str
    points: list[CalibrationPointResult] = field(default_factory=list)
    leak_check: Optional[LeakCheckResult] = None
    fit_summary: Optional[PortFitSummary] = None

    @property
    def port_label(self) -> str:
        return PORT_LABELS.get(self.port_id, self.port_id)

    @property
    def overall_passed(self) -> bool:
        if not self.points:
            return False
        return all(point.passed for point in self.points)


@dataclass(slots=True)
class QualityCalibrationSession:
    technician_name: str = ""
    asset_id: str = "222"
    include_leak_check: bool = False
    profile_id: str = ""
    profile_label: str = ""
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    left_port: PortCalibrationResult = field(
        default_factory=lambda: PortCalibrationResult(port_id="port_a")
    )
    right_port: PortCalibrationResult = field(
        default_factory=lambda: PortCalibrationResult(port_id="port_b")
    )
    last_report_path: Optional[Path] = None
    last_certificate_docx: Optional[Path] = None
    last_certificate_pdf: Optional[Path] = None

    def begin(self) -> None:
        if self.started_at is None:
            self.started_at = datetime.now()

    def complete(self) -> None:
        self.completed_at = datetime.now()

    def port_result(self, port_id: str) -> PortCalibrationResult:
        if port_id == "port_a":
            return self.left_port
        if port_id == "port_b":
            return self.right_port
        raise KeyError(f"Unsupported port id: {port_id}")

    @property
    def overall_passed(self) -> bool:
        ports_with_points = [port for port in (self.left_port, self.right_port) if port.points]
        if not ports_with_points:
            return False
        return all(port.overall_passed for port in ports_with_points)
