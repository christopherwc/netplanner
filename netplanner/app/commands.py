"""Undo/redo command stack (Command pattern).

Every mutation of the plan should go through a Command so it can be
undone. Concrete commands live here; the controller pushes them onto
the stack.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from netplanner.domain.entities import Device, Link
from netplanner.domain.model import NetworkPlan


class Command(ABC):
    description: str = ""

    @abstractmethod
    def execute(self) -> None: ...

    @abstractmethod
    def undo(self) -> None: ...


class CommandStack:
    def __init__(self) -> None:
        self._undo: list[Command] = []
        self._redo: list[Command] = []

    def push(self, command: Command) -> None:
        command.execute()
        self._undo.append(command)
        self._redo.clear()

    def undo(self) -> None:
        if self._undo:
            cmd = self._undo.pop()
            cmd.undo()
            self._redo.append(cmd)

    def redo(self) -> None:
        if self._redo:
            cmd = self._redo.pop()
            cmd.execute()
            self._undo.append(cmd)

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)


# ---------------------------------------------------------------- concrete
class AddDeviceCommand(Command):
    def __init__(self, plan: NetworkPlan, device: Device):
        self.plan, self.device = plan, device
        self.description = f"Add device '{device.name}'"

    def execute(self) -> None:
        self.plan.add_device(self.device)

    def undo(self) -> None:
        self.plan.remove_device(self.device.id)


class AddLinkCommand(Command):
    def __init__(self, plan: NetworkPlan, link: Link):
        self.plan, self.link = plan, link
        self.description = "Add link"

    def execute(self) -> None:
        self.plan.add_link(self.link)

    def undo(self) -> None:
        self.plan.remove_link(self.link)


class MoveDeviceCommand(Command):
    def __init__(self, plan: NetworkPlan, device_id: str, x: float, y: float):
        self.plan, self.device_id = plan, device_id
        self.new = (x, y)
        device = plan.get_device(device_id)
        self.old = (device.x, device.y) if device else (0.0, 0.0)
        self.description = "Move device"

    def execute(self) -> None:
        d = self.plan.get_device(self.device_id)
        if d:
            d.x, d.y = self.new

    def undo(self) -> None:
        d = self.plan.get_device(self.device_id)
        if d:
            d.x, d.y = self.old


class RenameDeviceCommand(Command):
    def __init__(self, plan: NetworkPlan, device_id: str, new_name: str):
        self.plan, self.device_id, self.new_name = plan, device_id, new_name
        device = plan.get_device(device_id)
        self.old_name = device.name if device else ""
        self.description = f"Rename device to '{new_name}'"

    def execute(self) -> None:
        d = self.plan.get_device(self.device_id)
        if d:
            d.name = self.new_name

    def undo(self) -> None:
        d = self.plan.get_device(self.device_id)
        if d:
            d.name = self.old_name
