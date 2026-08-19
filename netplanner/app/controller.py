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
    RenameDeviceCommand,
    EditInterfacesCommand,
)
from netplanner.app.validation import Issue, validate
from netplanner.domain.entities import Device, DeviceType, Interface, Link, LinkType
from netplanner.domain.interfaces import default_interfaces
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
        device = Device(
            name=name,
            device_type=device_type,
            x=x,
            y=y,
            interfaces=default_interfaces(device_type),
        )
        self.commands.push(AddDeviceCommand(self.plan, device))
        return device

    def next_device_name(self, device_type: DeviceType) -> str:
        """Generate an auto-incrementing name like rtr1, sw2, fw1."""
        from netplanner.export.styles import style_for

        prefix = style_for(device_type).name_prefix
        existing = {d.name for d in self.plan.devices}
        n = 1
        while f"{prefix}{n}" in existing:
            n += 1
        return f"{prefix}{n}"

    def add_link(
        self,
        a_device_id: str,
        b_device_id: str,
        link_type: LinkType = LinkType.ETHERNET,
        label: str = "",
        a_interface_id: str | None = None,
        b_interface_id: str | None = None,
    ) -> Link:
        link = Link(
            a_device_id=a_device_id,
            b_device_id=b_device_id,
            link_type=link_type,
            label=label,
            a_interface_id=a_interface_id,
            b_interface_id=b_interface_id,
        )
        self.commands.push(AddLinkCommand(self.plan, link))
        return link

    # -------------------------------------------------------------- interfaces
    def used_interface_ids(self) -> set[str]:
        used: set[str] = set()
        for link in self.plan.links:
            if link.a_interface_id:
                used.add(link.a_interface_id)
            if link.b_interface_id:
                used.add(link.b_interface_id)
        return used

    def free_interfaces(self, device_id: str) -> list[Interface]:
        device = self.plan.get_device(device_id)
        if device is None:
            return []
        used = self.used_interface_ids()
        return [i for i in device.interfaces if i.id not in used]

    def edit_interfaces(self, device_id: str, new_interfaces: list[Interface]) -> None:
        self.commands.push(EditInterfacesCommand(self.plan, device_id, new_interfaces))

    def interface_name(self, device_id: str, interface_id: str | None) -> str:
        if not interface_id:
            return ""
        device = self.plan.get_device(device_id)
        if device is None:
            return ""
        iface = next((i for i in device.interfaces if i.id == interface_id), None)
        return iface.name if iface else ""

    def rename_device(self, device_id: str, new_name: str) -> None:
        self.commands.push(RenameDeviceCommand(self.plan, device_id, new_name))

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
