"""Undo/redo command stack (Command pattern).

Every mutation of the plan should go through a Command so it can be
undone. Concrete commands live here; the controller pushes them onto
the stack.
"""

from __future__ import annotations

import logging

from abc import ABC, abstractmethod

from netplanner.domain.entities import (
    ConfigFile,
    Device,
    DeviceStatus,
    Interface,
    Link,
    LinkType,
    TextBox,
)
from netplanner.domain.model import NetworkPlan


logger = logging.getLogger(__name__)


class Command(ABC):
    description: str = ""

    @abstractmethod
    def execute(self) -> None: ...

    @abstractmethod
    def undo(self) -> None: ...


class CommandStack:
    """Classic undo/redo stack: executing a new command clears the redo side."""

    def __init__(self) -> None:
        self._undo: list[Command] = []
        self._redo: list[Command] = []

    def push(self, command: Command) -> None:
        logger.debug("Command: %s", command.description)
        command.execute()
        self._undo.append(command)
        self._redo.clear()

    def undo(self) -> None:
        if self._undo:
            logger.debug("Undo: %s", self._undo[-1].description)
            cmd = self._undo.pop()
            cmd.undo()
            self._redo.append(cmd)

    def redo(self) -> None:
        if self._redo:
            logger.debug("Redo: %s", self._redo[-1].description)
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
    """Add a device; undo removes it (and any links made to it since)."""

    def __init__(self, plan: NetworkPlan, device: Device):
        self.plan, self.device = plan, device
        self.description = f"Add device '{device.name}'"

    def execute(self) -> None:
        self.plan.add_device(self.device)

    def undo(self) -> None:
        self.plan.remove_device(self.device.id)


class AddLinkCommand(Command):
    """Add a link; undo detaches it and frees its interfaces."""

    def __init__(self, plan: NetworkPlan, link: Link):
        self.plan, self.link = plan, link
        self.description = "Add link"

    def execute(self) -> None:
        self.plan.add_link(self.link)

    def undo(self) -> None:
        self.plan.remove_link(self.link)


class MoveDeviceCommand(Command):
    """Record a device move so drags are undoable."""

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
    """Rename a device, remembering the old name for undo."""

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


class EditInterfacesCommand(Command):
    """Swap a device's whole interface list; undo restores the old list.

    Links referencing removed interfaces keep their (now dangling) ids;
    they simply lose their port label rather than breaking.
    """

    def __init__(self, plan: NetworkPlan, device_id: str, new_interfaces: list[Interface]):
        self.plan, self.device_id = plan, device_id
        self.new_interfaces = list(new_interfaces)
        device = plan.get_device(device_id)
        self.old_interfaces = list(device.interfaces) if device else []
        self.description = "Edit interfaces"

    def execute(self) -> None:
        d = self.plan.get_device(self.device_id)
        if d:
            d.interfaces = list(self.new_interfaces)

    def undo(self) -> None:
        d = self.plan.get_device(self.device_id)
        if d:
            d.interfaces = list(self.old_interfaces)


class EditDevicePropertiesCommand(Command):
    """Update model/loopback/notes/interfaces together as one undo step.

    Bundling these avoids a separate undo entry for each field when the
    user edits several tabs of the properties dialog and clicks OK once.
    """

    def __init__(
        self,
        plan: NetworkPlan,
        device_id: str,
        device_model: str,
        loopback_ip: str | None,
        notes: str,
        native_vlan: int,
        status: DeviceStatus,
        new_interfaces: list[Interface],
    ):
        self.plan, self.device_id = plan, device_id
        self.new = (device_model, loopback_ip, notes, native_vlan, status, list(new_interfaces))
        device = plan.get_device(device_id)
        self.old = (
            (
                device.device_model,
                device.loopback_ip,
                device.notes,
                device.native_vlan,
                device.status,
                list(device.interfaces),
            )
            if device
            else ("", None, "", 1, DeviceStatus.ACTIVE, [])
        )
        self.description = "Edit device properties"

    def _apply(self, values) -> None:
        model, loopback_ip, notes, native_vlan, status, interfaces = values
        d = self.plan.get_device(self.device_id)
        if d:
            d.device_model = model
            d.loopback_ip = loopback_ip
            d.notes = notes
            d.native_vlan = native_vlan
            d.status = status
            d.interfaces = list(interfaces)

    def execute(self) -> None:
        self._apply(self.new)

    def undo(self) -> None:
        self._apply(self.old)


class DeleteDeviceCommand(Command):
    """Delete a device and every link attached to it, as one undo step.

    networkx drops incident edges when a node is removed, so those links
    are captured up front and re-added on undo — otherwise undoing a
    device deletion would restore the device but silently lose its
    cabling.
    """

    def __init__(self, plan: NetworkPlan, device_id: str):
        self.plan = plan
        self.device = plan.get_device(device_id)
        # Snapshot incident links before removal so undo can restore them.
        self.removed_links: list[Link] = [
            link
            for link in plan.links
            if device_id in (link.a_device_id, link.b_device_id)
        ]
        name = self.device.name if self.device else "device"
        self.description = f"Delete device '{name}'"

    def execute(self) -> None:
        if self.device:
            self.plan.remove_device(self.device.id)

    def undo(self) -> None:
        if not self.device:
            return
        self.plan.add_device(self.device)
        for link in self.removed_links:
            # Both endpoints must exist; the far end was never removed.
            if self.plan.get_device(link.a_device_id) and self.plan.get_device(
                link.b_device_id
            ):
                self.plan.add_link(link)


class DeleteLinkCommand(Command):
    """Delete a single link, keeping both devices intact."""

    def __init__(self, plan: NetworkPlan, link: Link):
        self.plan, self.link = plan, link
        self.description = "Delete link"

    def execute(self) -> None:
        self.plan.remove_link(self.link)

    def undo(self) -> None:
        self.plan.add_link(self.link)


class EditConfigsCommand(Command):
    """Replace a device's attached config files (undoable).

    Configs are swapped wholesale like interfaces are, so importing,
    renaming and removing several files in one dialog session collapses
    into a single undo step.
    """

    def __init__(self, plan: NetworkPlan, device_id: str, new_configs: list[ConfigFile]):
        self.plan, self.device_id = plan, device_id
        self.new_configs = list(new_configs)
        device = plan.get_device(device_id)
        self.old_configs = list(device.configs) if device else []
        self.description = "Edit device configs"

    def execute(self) -> None:
        d = self.plan.get_device(self.device_id)
        if d:
            d.configs = list(self.new_configs)

    def undo(self) -> None:
        d = self.plan.get_device(self.device_id)
        if d:
            d.configs = list(self.old_configs)


class AddTextBoxCommand(Command):
    """Place a new text annotation on the canvas."""

    def __init__(self, plan: NetworkPlan, textbox: TextBox):
        self.plan, self.textbox = plan, textbox
        preview = textbox.text.splitlines()[0][:24] if textbox.text else "empty"
        self.description = f"Add text box '{preview}'"

    def execute(self) -> None:
        self.plan.add_textbox(self.textbox)

    def undo(self) -> None:
        self.plan.remove_textbox(self.textbox.id)


class MoveTextBoxCommand(Command):
    """Reposition a text annotation (captures the old spot for undo)."""

    def __init__(self, plan: NetworkPlan, textbox_id: str, x: float, y: float):
        self.plan, self.textbox_id = plan, textbox_id
        self.new = (x, y)
        box = plan.get_textbox(textbox_id)
        self.old = (box.x, box.y) if box else (x, y)
        self.description = "Move text box"

    def _apply(self, pos: tuple[float, float]) -> None:
        box = self.plan.get_textbox(self.textbox_id)
        if box:
            box.x, box.y = pos

    def execute(self) -> None:
        self._apply(self.new)

    def undo(self) -> None:
        self._apply(self.old)


class EditTextBoxCommand(Command):
    """Change a text annotation's content and formatting as one step.

    Text, size, weight and color are bundled because they are all edited
    in a single dialog; separate commands would make one OK click take
    four undos to reverse.
    """

    def __init__(
        self,
        plan: NetworkPlan,
        textbox_id: str,
        text: str,
        font_size: float,
        bold: bool,
        color: str,
        width: float,
    ):
        self.plan, self.textbox_id = plan, textbox_id
        self.new = (text, font_size, bold, color, width)
        box = plan.get_textbox(textbox_id)
        self.old = (
            (box.text, box.font_size, box.bold, box.color, box.width)
            if box
            else self.new
        )
        self.description = "Edit text box"

    def _apply(self, values) -> None:
        box = self.plan.get_textbox(self.textbox_id)
        if box:
            box.text, box.font_size, box.bold, box.color, box.width = values

    def execute(self) -> None:
        self._apply(self.new)

    def undo(self) -> None:
        self._apply(self.old)


class DeleteTextBoxCommand(Command):
    """Remove a text annotation, keeping it for undo."""

    def __init__(self, plan: NetworkPlan, textbox_id: str):
        self.plan = plan
        self.textbox = plan.get_textbox(textbox_id)
        self.description = "Delete text box"

    def execute(self) -> None:
        if self.textbox:
            self.plan.remove_textbox(self.textbox.id)

    def undo(self) -> None:
        if self.textbox:
            self.plan.add_textbox(self.textbox)


class EditLinkCommand(Command):
    """Edit a link's label, media type and bandwidth as one undo step.

    Bundled like device properties: they are edited in a single dialog,
    so one OK should be one Ctrl+Z.
    """

    def __init__(
        self,
        plan: NetworkPlan,
        link_id: str,
        label: str,
        link_type: LinkType,
        bandwidth_mbps: int | None,
    ):
        self.plan, self.link_id = plan, link_id
        self.new = (label, link_type, bandwidth_mbps)
        link = plan.get_link(link_id)
        self.old = (
            (link.label, link.link_type, link.bandwidth_mbps) if link else self.new
        )
        self.description = "Edit link"

    def _apply(self, values) -> None:
        link = self.plan.get_link(self.link_id)
        if link:
            link.label, link.link_type, link.bandwidth_mbps = values

    def execute(self) -> None:
        self._apply(self.new)

    def undo(self) -> None:
        self._apply(self.old)
