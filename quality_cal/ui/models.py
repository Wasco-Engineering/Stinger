"""Typed UI models for the quality calibration shell."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


StageKind = Literal['setup', 'leak', 'calibration', 'confirm', 'move', 'report']


@dataclass(frozen=True, slots=True)
class HardwareStatusEntry:
    """Single hardware status line displayed in the setup view."""

    name: str
    label: str
    ok: bool
    detail: str


@dataclass(frozen=True, slots=True)
class HardwareSnapshot:
    """Snapshot of hardware readiness for the setup screen."""

    overall_ok: bool
    summary: str
    discovery_note: str
    entries: tuple[HardwareStatusEntry, ...]


@dataclass(frozen=True, slots=True)
class WorkflowStage:
    """Ordered stage definition for the custom workflow shell."""

    key: str
    title: str
    description: str
    kind: StageKind
    port_id: str | None = None
