"""Application controller.

The single entry point the GUI talks to. Owns the current plan, the
command stack, and access to persistence and export subsystems. Keeps
Qt out of the core layers.
"""

from __future__ import annotations

from pathlib import Path

from netplanner.app.commands import (
    AddDeviceCommand,
    AddLinkCommand,
    CommandStack,
    MoveDeviceCommand,
)
from netplanner.app.validation import Issue, validate
from netplanner.domain.entities import Device, DeviceType, Link
from netplanner.domain.layout import auto_layout
from netplanner.domain.model import NetworkPlan
from netplanner.export.pdf_exporter import export_pdf
from netplanner.export.png_exporter import export_png
from netplanner.persistence.repository import PlanRepository


class AppController:
    def __init__(self, repository: PlanRepository | None = None):
        self.repository = repository or PlanRepository()
        self.plan = NetworkPlan()
        self.commands = CommandStack()

    # ------------------------------------------------------------ plan edits
    def add_device(self, name: str, device_type: DeviceType, x: float, y: float) -> Device:
        device = Device(name=name, device_type=device_type, x=x, y=y)
        self.commands.push(AddDeviceCommand(self.plan, device))
        return device

    def add_link(self, a_device_id: str, b_device_id: str) -> Link:
        link = Link(a_device_id=a_device_id, b_device_id=b_device_id)
        self.commands.push(AddLinkCommand(self.plan, link))
        return link

    def move_device(self, device_id: str, x: float, y: float) -> None:
        self.commands.push(MoveDeviceCommand(self.plan, device_id, x, y))

    def undo(self) -> None:
        self.commands.undo()

    def redo(self) -> None:
        self.commands.redo()

    def run_auto_layout(self, algorithm: str = "spring") -> None:
        auto_layout(self.plan, algorithm)

    def validate_plan(self) -> list[Issue]:
        return validate(self.plan)

    # ----------------------------------------------------------- persistence
    def new_plan(self, name: str = "Untitled plan") -> None:
        self.plan = NetworkPlan(name=name)
        self.commands = CommandStack()

    def save(self) -> None:
        self.repository.save(self.plan)

    def load(self, plan_id: str) -> None:
        self.plan = self.repository.load(plan_id)
        self.commands = CommandStack()

    # ---------------------------------------------------------------- export
    def export_to_pdf(self, path: Path) -> None:
        export_pdf(self.plan, path)

    def export_to_png(self, path: Path) -> None:
        export_png(self.plan, path)
