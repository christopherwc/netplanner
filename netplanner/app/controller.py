"""Application controller.

The single entry point the GUI talks to. Owns the current plan, the
command stack, and access to persistence and export subsystems. Keeps
Qt out of the core layers.
"""

from __future__ import annotations

from pathlib import Path

import logging

from netplanner.errors import ConfigImportError
from netplanner.app.commands import (
    AddDeviceCommand,
    AddLinkCommand,
    CommandStack,
    MoveDeviceCommand,
    RenameDeviceCommand,
    EditInterfacesCommand,
    EditDevicePropertiesCommand,
    DeleteDeviceCommand,
    DeleteLinkCommand,
    EditConfigsCommand,
    EditLinkCommand,
    AddSiteCommand,
    SetSiteGeometryCommand,
    EditSiteCommand,
    DeleteSiteCommand,
    AddTextBoxCommand,
    MoveTextBoxCommand,
    EditTextBoxCommand,
    DeleteTextBoxCommand,
)
from netplanner.app.validation import Issue, validate
from netplanner.domain.entities import (
    ConfigFile,
    Device,
    DeviceStatus,
    DeviceType,
    Interface,
    Link,
    LinkType,
    Site,
    TextBox,
    detect_config_format,
    negotiated_speed_mbps,
)
from pathlib import Path as _Path
from netplanner.domain.interfaces import default_interfaces
from netplanner.domain.layout import auto_layout
from netplanner.domain.model import NetworkPlan
from netplanner.export.pdf_exporter import export_pdf
from netplanner.export.png_exporter import export_png
from netplanner.persistence.repository import PlanRepository


logger = logging.getLogger(__name__)


class AppController:
    def __init__(self, repository: PlanRepository | None = None):
        self.repository = repository or PlanRepository()
        self.plan = NetworkPlan()
        self.commands = CommandStack()
        # Active VLAN highlight; exports mirror what the canvas shows.
        self.vlan_filter: set[int] = set()

    # ------------------------------------------------------------ plan edits
    def add_device(self, name: str, device_type: DeviceType, x: float, y: float) -> Device:
        """Create a device at (x, y) with its type's default interfaces (undoable)."""
        logger.info("Adding device '%s' (%s) at (%.0f, %.0f)", name, device_type.value, x, y)
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
        """Generate the next free auto-name for a type (rtr1, sw2, fw1, ...)."""
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
        """Create a link between two devices (undoable).

        Interface ids are optional: links can exist without port
        assignments, e.g. quick sketches or imported plans.

        Bandwidth is derived from the two ports when both are known —
        the slower end wins, since that is what the link actually
        carries. It is only a starting value: the link dialog can
        override it, and nothing recomputes it behind the user's back.
        """
        link = Link(
            a_device_id=a_device_id,
            b_device_id=b_device_id,
            link_type=link_type,
            label=label,
            a_interface_id=a_interface_id,
            b_interface_id=b_interface_id,
            bandwidth_mbps=self.derived_link_speed(
                a_device_id, a_interface_id, b_device_id, b_interface_id
            ),
        )
        self.commands.push(AddLinkCommand(self.plan, link))
        return link

    # -------------------------------------------------------------- interfaces
    def used_interface_ids(self) -> set[str]:
        """Ids of every interface currently occupied by a link endpoint."""
        used: set[str] = set()
        for link in self.plan.links:
            if link.a_interface_id:
                used.add(link.a_interface_id)
            if link.b_interface_id:
                used.add(link.b_interface_id)
        return used

    def free_interfaces(self, device_id: str) -> list[Interface]:
        """Interfaces on a device not yet used by any link (for the port picker)."""
        device = self.plan.get_device(device_id)
        if device is None:
            return []
        used = self.used_interface_ids()
        return [i for i in device.interfaces if i.id not in used]

    def edit_interfaces(self, device_id: str, new_interfaces: list[Interface]) -> None:
        """Replace a device's interface list wholesale (undoable)."""
        self.commands.push(EditInterfacesCommand(self.plan, device_id, new_interfaces))

    def edit_device_properties(
        self,
        device_id: str,
        device_model: str,
        loopback_ip: str | None,
        notes: str,
        native_vlan: int,
        status: DeviceStatus,
        new_interfaces: list[Interface],
    ) -> None:
        """Update model, loopback IP, notes, native VLAN, status, and interfaces as one undo step."""
        self.commands.push(
            EditDevicePropertiesCommand(
                self.plan, device_id, device_model, loopback_ip, notes,
                native_vlan, status, new_interfaces,
            )
        )

    def interface_name(self, device_id: str, interface_id: str | None) -> str:
        """Resolve an interface id to its display name; "" when unset/missing."""
        if not interface_id:
            return ""
        device = self.plan.get_device(device_id)
        if device is None:
            return ""
        iface = next((i for i in device.interfaces if i.id == interface_id), None)
        return iface.name if iface else ""

    def rename_device(self, device_id: str, new_name: str) -> None:
        """Rename a device (undoable)."""
        self.commands.push(RenameDeviceCommand(self.plan, device_id, new_name))

    def set_vlan_filter(self, vlan_ids: set[int]) -> None:
        """Set the VLAN highlight used by the canvas and by exports."""
        self.vlan_filter = set(vlan_ids)

    def vlan_usage(self):
        """Every VLAN in use across the plan, for the legend."""
        from netplanner.export.vlans import plan_vlan_usage

        return plan_vlan_usage(self.plan)

    # ----------------------------------------------------------------- sites
    def add_site(self, name: str, x: float, y: float, **kwargs) -> Site:
        """Place a site box at the given canvas position (undoable)."""
        site = Site(name=name, x=x, y=y, **kwargs)
        logger.info("Adding site %r at (%.0f, %.0f)", name, x, y)
        self.commands.push(AddSiteCommand(self.plan, site))
        return site

    def set_site_geometry(
        self, site_id: str, x: float, y: float, width: float, height: float
    ) -> None:
        """Move or resize a site box (undoable)."""
        self.commands.push(
            SetSiteGeometryCommand(self.plan, site_id, x, y, width, height)
        )

    def edit_site(self, site_id: str, name: str, notes: str, color: str) -> None:
        """Update a site's name, notes and colour (one undo step)."""
        self.commands.push(EditSiteCommand(self.plan, site_id, name, notes, color))

    def delete_site(self, site_id: str) -> None:
        """Remove a site box, leaving the devices inside it in place."""
        logger.info("Deleting site id=%s", site_id)
        self.commands.push(DeleteSiteCommand(self.plan, site_id))

    def devices_in_site(self, site_id: str) -> list[Device]:
        """Devices positioned inside a site's box."""
        return self.plan.devices_in_site(site_id)

    # ------------------------------------------------------------- textboxes
    def add_textbox(self, text: str, x: float, y: float, **kwargs) -> TextBox:
        """Place a text annotation at the given canvas position (undoable)."""
        box = TextBox(text=text, x=x, y=y, **kwargs)
        logger.info("Adding text box at (%.0f, %.0f): %r", x, y, text[:40])
        self.commands.push(AddTextBoxCommand(self.plan, box))
        return box

    def move_textbox(self, textbox_id: str, x: float, y: float) -> None:
        """Reposition a text annotation (undoable)."""
        self.commands.push(MoveTextBoxCommand(self.plan, textbox_id, x, y))

    def edit_textbox(
        self,
        textbox_id: str,
        text: str,
        font_size: float,
        bold: bool,
        color: str,
        width: float,
    ) -> None:
        """Update a text annotation's content and formatting (one undo step)."""
        self.commands.push(
            EditTextBoxCommand(self.plan, textbox_id, text, font_size, bold, color, width)
        )

    def delete_textbox(self, textbox_id: str) -> None:
        """Remove a text annotation (undoable)."""
        logger.info("Deleting text box id=%s", textbox_id)
        self.commands.push(DeleteTextBoxCommand(self.plan, textbox_id))

    # ----------------------------------------------------------- config files
    def edit_configs(self, device_id: str, new_configs: list[ConfigFile]) -> None:
        """Replace a device's attached config files (undoable)."""
        self.commands.push(EditConfigsCommand(self.plan, device_id, new_configs))

    @staticmethod
    def read_config_file(path: _Path) -> ConfigFile:
        """Load a config from disk into an unattached ConfigFile.

        Decoded as UTF-8 with replacement so a stray non-UTF-8 byte in a
        vendor export can't raise; the format is guessed from the
        contents and can be overridden in the dialog.
        """
        try:
            raw = path.read_bytes()
        except OSError as exc:
            logger.exception("Config import failed reading %s", path)
            raise ConfigImportError(
                f"Could not read config file {path}: {type(exc).__name__}: {exc}"
            ) from exc
        text = raw.decode("utf-8", errors="replace")
        detected = detect_config_format(text, path.name.lower())
        logger.info(
            "Imported config %s (%d bytes, detected format: %s)",
            path, len(raw), detected.value,
        )
        return ConfigFile(
            filename=path.name,
            content=text,
            config_format=detected,
            source_path=str(path),
        )

    def delete_device(self, device_id: str) -> None:
        """Delete a device and its attached links (undoable as one step)."""
        device = self.plan.get_device(device_id)
        logger.info(
            "Deleting device '%s' (id=%s) with %d attached link(s)",
            device.name if device else "?", device_id,
            len(self.links_for_device(device_id)),
        )
        self.commands.push(DeleteDeviceCommand(self.plan, device_id))

    def derived_link_speed(
        self,
        a_device_id: str,
        a_interface_id: str | None,
        b_device_id: str,
        b_interface_id: str | None,
    ) -> int | None:
        """Speed a link would run at, from its two interfaces' line rates."""
        return negotiated_speed_mbps(
            self._interface(a_device_id, a_interface_id),
            self._interface(b_device_id, b_interface_id),
        )

    def link_derived_speed(self, link: Link) -> int | None:
        """Speed an existing link would run at, given its current ports."""
        return self.derived_link_speed(
            link.a_device_id, link.a_interface_id,
            link.b_device_id, link.b_interface_id,
        )

    def _interface(self, device_id: str, interface_id: str | None):
        """Resolve a (device, interface) id pair to the Interface object."""
        if not interface_id:
            return None
        device = self.plan.get_device(device_id)
        if device is None:
            return None
        return next((i for i in device.interfaces if i.id == interface_id), None)

    def edit_link(
        self,
        link_id: str,
        label: str,
        link_type: LinkType,
        bandwidth_mbps: int | None,
        bandwidth_auto: bool = False,
    ) -> None:
        """Update a link's label, media type and bandwidth (one undo step).

        `bandwidth_auto` records whether the speed should keep tracking
        the interfaces. Editing the figure by hand clears it, so a
        later port change can't overwrite a real measured rate.
        """
        logger.info(
            "Editing link id=%s (label=%r, type=%s, auto_speed=%s)",
            link_id, label, link_type.value, bandwidth_auto,
        )
        self.commands.push(
            EditLinkCommand(
                self.plan, link_id, label, link_type, bandwidth_mbps, bandwidth_auto
            )
        )

    def delete_link(self, link: Link) -> None:
        """Delete a single link, leaving both devices in place (undoable)."""
        logger.info(
            "Deleting link id=%s (%s <-> %s)", link.id, link.a_device_id, link.b_device_id
        )
        self.commands.push(DeleteLinkCommand(self.plan, link))

    def links_for_device(self, device_id: str) -> list[Link]:
        """Every link with this device at either end (used for delete prompts)."""
        return [
            link
            for link in self.plan.links
            if device_id in (link.a_device_id, link.b_device_id)
        ]

    def move_device(self, device_id: str, x: float, y: float) -> None:
        self.commands.push(MoveDeviceCommand(self.plan, device_id, x, y))

    def undo(self) -> None:
        """Undo the most recent plan mutation."""
        self.commands.undo()

    def redo(self) -> None:
        """Re-apply the most recently undone mutation."""
        self.commands.redo()

    def run_auto_layout(self, algorithm: str = "spring") -> None:
        """Recompute all device positions with the chosen layout algorithm."""
        auto_layout(self.plan, algorithm)

    def validate_plan(self) -> list[Issue]:
        """Run all validation rules; returns found issues (possibly empty)."""
        return validate(self.plan)

    # ----------------------------------------------------------- persistence
    def new_plan(self, name: str = "Untitled plan") -> None:
        """Start a fresh plan, discarding the current one and its history."""
        self.plan = NetworkPlan(name=name)
        self.commands = CommandStack()
        # Active VLAN highlight; exports mirror what the canvas shows.
        self.vlan_filter: set[int] = set()

    def save(self) -> None:
        """Persist the current plan to the SQLite database."""
        self.repository.save(self.plan)

    def load(self, plan_id: str) -> None:
        """Load a stored plan by id; resets undo history."""
        self.plan = self.repository.load(plan_id)
        self.commands = CommandStack()
        # Active VLAN highlight; exports mirror what the canvas shows.
        self.vlan_filter: set[int] = set()

    # ---------------------------------------------------------------- export
    def export_to_pdf(self, path: Path) -> None:
        """Render the plan to a single-page PDF, honouring the VLAN filter."""
        export_pdf(self.plan, path, self.vlan_filter)

    def export_to_png(self, path: Path) -> None:
        """Render the plan to an antialiased PNG, honouring the VLAN filter."""
        export_png(self.plan, path, self.vlan_filter)
