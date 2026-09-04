"""Extracting interface configuration from an attached device config,
and mirroring it onto the device's actual interfaces.

Three things this deliberately does not do:

- Rename or remove existing interfaces the config doesn't mention —
  only interfaces the config actually describes are touched.
- Touch MAC address or maximum speed on a matched interface. Those
  describe the physical port, not what a config says about it, and a
  config rarely states either in a form worth trusting over what's
  already on the diagram.
- Guess at VLAN semantics for Ubiquiti. VyOS/EdgeOS's vif
  sub-interfaces don't map onto a single access/trunk choice per
  port the way Cisco's switchport model or RouterOS's bridge VLAN
  filtering do, so Ubiquiti syncs IP addressing only. Cisco IOS and
  MikroTik both sync VLAN membership; all three vendors sync IP
  addressing, which is unambiguous everywhere.

Nothing here raises on malformed input: a line that doesn't match a
known pattern is skipped, not fatal, so a config that parses
imperfectly still contributes whatever it can.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, replace

from .entities import ConfigFormat, Interface, VlanMode


@dataclass(frozen=True)
class ParsedInterface:
    """What a config says about one interface.

    A subset of Interface's fields: a config rarely states an id, MAC
    address or rate in a form worth trusting, so none of those are
    represented here. `None` means "the config didn't say" and leaves
    the matching field on a synced Interface untouched; `vlan_mode`
    being set is what decides whether access_vlan/trunk_vlans apply.
    """

    name: str
    ip_address: str | None = None
    vlan_mode: VlanMode | None = None
    access_vlan: int | None = None
    trunk_vlans: tuple[int, ...] = ()


def parse_interfaces(content: str, config_format: ConfigFormat) -> list[ParsedInterface]:
    """Extract interface configuration from `content`.

    Plain text has no interface syntax to parse and always returns [].
    """
    if config_format is ConfigFormat.CISCO_IOS:
        return _parse_cisco(content)
    if config_format is ConfigFormat.MIKROTIK:
        return _parse_mikrotik(content)
    if config_format is ConfigFormat.UBIQUITI:
        return _parse_ubiquiti(content)
    return []


def interface_match_key(name: str) -> str:
    """The key two interface names are compared by when syncing.

    Lowercased, and for Cisco-style names, reduced to IOS's short
    form: "GigabitEthernet0/1" (as a running-config states it) and
    "Gig0/1" (as interfaces.py names a new router's ports) both become
    "gi0/1", so a real config matches NetPlanner's abbreviated
    defaults without either side being renamed. Names outside the
    known Cisco prefixes — MikroTik's "ether1", Ubiquiti's "eth0" —
    fall through unchanged but lowercased, which is already how those
    vendors' own tooling treats interface names.
    """
    lowered = name.strip().lower()
    for variant, canonical in _CISCO_PREFIXES:
        if lowered.startswith(variant):
            return canonical + lowered[len(variant) :]
    return lowered


def mirror_interfaces(
    existing: list[Interface], parsed: list[ParsedInterface]
) -> list[Interface]:
    """Apply parsed config values onto a copy of `existing`.

    Matched by interface_match_key(). A parsed interface matching no
    existing one becomes a new Interface, named exactly as the config
    wrote it (not abbreviated) since there's no existing row to defer
    to. Order is preserved: matched interfaces stay where they were,
    unmatched parsed interfaces are appended in the order they were
    parsed.
    """
    result = [replace(iface, trunk_vlans=list(iface.trunk_vlans)) for iface in existing]
    key_to_index = {interface_match_key(iface.name): i for i, iface in enumerate(result)}
    for p in parsed:
        key = interface_match_key(p.name)
        index = key_to_index.get(key)
        if index is None:
            new_iface = Interface(name=p.name)
            _apply_parsed(new_iface, p)
            result.append(new_iface)
            key_to_index[key] = len(result) - 1
        else:
            _apply_parsed(result[index], p)
    return result


def _apply_parsed(interface: Interface, parsed: ParsedInterface) -> None:
    if parsed.ip_address is not None:
        interface.ip_address = parsed.ip_address
    if parsed.vlan_mode is not None:
        interface.vlan_mode = parsed.vlan_mode
        if parsed.vlan_mode is VlanMode.ACCESS and parsed.access_vlan is not None:
            interface.access_vlan = parsed.access_vlan
        elif parsed.vlan_mode is VlanMode.TRUNK:
            interface.trunk_vlans = list(parsed.trunk_vlans)


# ------------------------------------------------------------------- Cisco IOS
# A running-config states the long form ("GigabitEthernet0/1");
# interfaces.py names a new router's default ports with IOS's own
# abbreviation ("Gig0/1"), which is shorter than the long form but
# longer than IOS's minimal short form ("Gi0/1") — so every variant a
# person or a device might actually write has to map to the same
# canonical code, not just the two extremes.
_CISCO_FAMILIES: dict[str, tuple[str, ...]] = {
    "gi": ("gigabitethernet", "gig", "gi"),
    "te": ("tengigabitethernet", "tengig", "ten", "te"),
    "fo": ("fortygigabitethernet", "forty", "fo"),
    "hu": ("hundredgigabitethernet", "hundred", "hu"),
    "fa": ("fastethernet", "fast", "fa"),
    "eth": ("ethernet", "eth"),
    "se": ("serial", "se"),
    "lo": ("loopback", "lo"),
    "po": ("port-channel", "po"),
    "tu": ("tunnel", "tu"),
    "vl": ("vlan", "vl"),
}

# Flattened and sorted longest-variant-first, so e.g. "gigabitethernet"
# is tried (and matches) before the shorter "gig" or "gi" entries would
# otherwise match a prefix of it.
_CISCO_PREFIXES: tuple[tuple[str, str], ...] = tuple(
    sorted(
        (
            (variant, canonical)
            for canonical, variants in _CISCO_FAMILIES.items()
            for variant in variants
        ),
        key=lambda pair: -len(pair[0]),
    )
)

_CISCO_IFACE_RE = re.compile(r"^interface\s+(\S+)", re.IGNORECASE)
_CISCO_IP_RE = re.compile(
    r"^\s*ip address\s+(\d{1,3}(?:\.\d{1,3}){3})\s+(\d{1,3}(?:\.\d{1,3}){3})",
    re.IGNORECASE,
)
_CISCO_TRUNK_MODE_RE = re.compile(r"^\s*switchport mode trunk\b", re.IGNORECASE)
_CISCO_TRUNK_VLANS_RE = re.compile(
    r"^\s*switchport trunk allowed vlan\s+(.+?)\s*$", re.IGNORECASE
)
_CISCO_ACCESS_VLAN_RE = re.compile(r"^\s*switchport access vlan\s+(\d+)", re.IGNORECASE)


def _parse_cisco(content: str) -> list[ParsedInterface]:
    """Cisco IOS blocks each interface between `interface X` and the
    next `interface` line or a bare `!`, so a bare `!` closes the
    current block early — trailing lines describe the next thing in
    the config, not this interface."""
    interfaces: list[ParsedInterface] = []
    name: str | None = None
    ip_address: str | None = None
    vlan_mode: VlanMode | None = None
    access_vlan: int | None = None
    trunk_vlans: list[int] = []

    def flush() -> None:
        if name is not None:
            interfaces.append(
                ParsedInterface(
                    name=name,
                    ip_address=ip_address,
                    vlan_mode=vlan_mode,
                    access_vlan=access_vlan,
                    trunk_vlans=tuple(trunk_vlans),
                )
            )

    for line in content.splitlines():
        iface_match = _CISCO_IFACE_RE.match(line)
        if iface_match:
            flush()
            name = iface_match.group(1)
            ip_address, vlan_mode, access_vlan, trunk_vlans = None, None, None, []
            continue
        if name is None:
            continue
        if line.strip() == "!":
            flush()
            name = None
            continue
        ip_match = _CISCO_IP_RE.match(line)
        if ip_match:
            ip_address = _cidr_from_netmask(ip_match.group(1), ip_match.group(2))
            continue
        if _CISCO_TRUNK_MODE_RE.match(line):
            vlan_mode = VlanMode.TRUNK
            continue
        trunk_match = _CISCO_TRUNK_VLANS_RE.match(line)
        if trunk_match:
            vlan_mode = VlanMode.TRUNK
            trunk_vlans = _parse_vlan_ranges(trunk_match.group(1))
            continue
        access_match = _CISCO_ACCESS_VLAN_RE.match(line)
        if access_match:
            vlan_mode = VlanMode.ACCESS
            access_vlan = int(access_match.group(1))
            continue
    flush()
    return interfaces


def _parse_vlan_ranges(raw: str) -> list[int]:
    """"10,20,30" or "10-12,20" -> [10, 20, 30] / [10, 11, 12, 20]."""
    vlans: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_str, _, end_str = part.partition("-")
            try:
                start, end = int(start_str), int(end_str)
            except ValueError:
                continue
            if start <= end:
                vlans.extend(range(start, end + 1))
        else:
            try:
                vlans.append(int(part))
            except ValueError:
                continue
    return vlans


def _cidr_from_netmask(address: str, netmask: str) -> str | None:
    try:
        network = ipaddress.IPv4Network(f"{address}/{netmask}", strict=False)
    except ValueError:
        return None
    return f"{address}/{network.prefixlen}"


# -------------------------------------------------------------------- MikroTik
# RouterOS export scripts set a "current path" with a `/path` line and
# then list `add`/`set` commands under it until the next `/path` line —
# so tracking the most recent `/...` line is enough context to know
# what an `add`/`set` command is even talking about.
#
# VLAN membership comes from whichever of two independent, commonly
# used RouterOS features the config actually has:
#
# - Bridge VLAN filtering: `/interface bridge port` gives each port a
#   pvid (its untagged/access VLAN); `/interface bridge vlan` lists,
#   per VLAN, which ports carry it `tagged` (trunk membership) and
#   which carry it `untagged` (access membership, overriding pvid for
#   that VLAN specifically). A port tagged for any VLAN is treated as
#   TRUNK — VlanMode has no "trunk with a native VLAN" state distinct
#   from plain trunk, matching how Cisco sync already collapses that
#   same distinction.
# - VLAN sub-interfaces: `/interface vlan add vlan-id=N interface=P`
#   creates a new named interface for tagged traffic on VLAN N over
#   parent P — which also means P itself carries N tagged, i.e. P is
#   a TRUNK member for N, even though the RouterOS config never says
#   "trunk" anywhere.
#
# A port matching neither feature but seen only via a bare pvid is
# still worth recording as ACCESS — simple configs often skip bridge
# VLAN filtering entirely and rely on pvid alone.
_MIKROTIK_PATH_RE = re.compile(r"^/(\S.*)$")
_MIKROTIK_KV_RE = re.compile(r'([\w-]+)=("[^"]*"|\S+)')


def _parse_mikrotik(content: str) -> list[ParsedInterface]:
    ip_by_name: dict[str, str] = {}
    trunk_vlans_by_name: dict[str, set[int]] = {}
    access_vlan_by_name: dict[str, int] = {}
    pvid_by_name: dict[str, int] = {}
    order: dict[str, None] = {}  # insertion-ordered "seen interface names"

    def note(name: str) -> None:
        order.setdefault(name, None)

    current_path = ""
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        path_match = _MIKROTIK_PATH_RE.match(line)
        if path_match:
            current_path = path_match.group(1).strip().lower()
            continue
        if not line.startswith(("add", "set")):
            continue
        fields = {k: v.strip('"') for k, v in _MIKROTIK_KV_RE.findall(line)}

        if current_path == "ip address":
            name, address = fields.get("interface"), fields.get("address")
            if name and address:
                ip_by_name[name] = address
                note(name)
        elif current_path == "interface bridge port":
            name, pvid = fields.get("interface"), fields.get("pvid")
            if name:
                # Bridge membership alone is worth recording — like a
                # Cisco `interface X` stanza with nothing else in it —
                # even when there's no pvid to say anything about VLANs.
                note(name)
                if pvid and pvid.isdigit():
                    pvid_by_name[name] = int(pvid)
        elif current_path == "interface bridge vlan":
            vlan_ids = _parse_vlan_ranges(fields.get("vlan-ids", ""))
            for port in _split_port_list(fields.get("tagged", "")):
                trunk_vlans_by_name.setdefault(port, set()).update(vlan_ids)
                note(port)
            for port in _split_port_list(fields.get("untagged", "")):
                if vlan_ids:
                    access_vlan_by_name[port] = vlan_ids[0]
                note(port)
        elif current_path == "interface vlan":
            parent, vlan_id = fields.get("interface"), fields.get("vlan-id")
            if parent and vlan_id and vlan_id.isdigit():
                trunk_vlans_by_name.setdefault(parent, set()).add(int(vlan_id))
                note(parent)

    result: list[ParsedInterface] = []
    for name in order:
        trunk_vlans = trunk_vlans_by_name.get(name)
        if trunk_vlans:
            vlan_mode, access_vlan = VlanMode.TRUNK, None
        elif name in access_vlan_by_name:
            vlan_mode, access_vlan = VlanMode.ACCESS, access_vlan_by_name[name]
        elif name in pvid_by_name:
            vlan_mode, access_vlan = VlanMode.ACCESS, pvid_by_name[name]
        else:
            vlan_mode, access_vlan = None, None
        result.append(
            ParsedInterface(
                name=name,
                ip_address=ip_by_name.get(name),
                vlan_mode=vlan_mode,
                access_vlan=access_vlan,
                trunk_vlans=tuple(sorted(trunk_vlans)) if trunk_vlans else (),
            )
        )
    return result


def _split_port_list(raw: str) -> list[str]:
    """RouterOS lists ports in tagged=/untagged= as a bare comma list,
    e.g. "ether2,ether3" — no ranges or quoting, unlike vlan-ids."""
    return [p for p in raw.split(",") if p]


# ------------------------------------------------------------------- Ubiquiti
# Two distinct export shapes both get called Ubiquiti by
# detect_config_format(): VyOS/EdgeOS's flat "set" commands, and the
# same configuration shown as nested brace blocks. Both are parsed;
# whichever the file actually uses contributes results, the other
# simply matches nothing.
_UBIQUITI_SET_RE = re.compile(
    r"^set interfaces (?:ethernet|switch|bridge|loopback|wireless) (\S+)"
    r"(?: vif (\d+))? address ([\d./]+)"
)
_UBIQUITI_ADDRESS_RE = re.compile(r'^address\s+"?([\d./]+)"?;?$')
_UBIQUITI_IFACE_KEYWORDS = frozenset({"ethernet", "switch", "bridge", "loopback", "wireless"})


def _parse_ubiquiti(content: str) -> list[ParsedInterface]:
    by_name: dict[str, ParsedInterface] = {}

    for line in content.splitlines():
        match = _UBIQUITI_SET_RE.match(line.strip())
        if match:
            base, vif, address = match.groups()
            name = f"{base}.{vif}" if vif else base
            by_name[name] = ParsedInterface(name=name, ip_address=address)

    stack: list[tuple[str, str | None]] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line == "}":
            if stack:
                stack.pop()
            continue
        if line.endswith("{"):
            parts = line[:-1].split()
            if len(parts) >= 2:
                stack.append((parts[0].lower(), parts[1]))
            elif parts:
                stack.append((parts[0].lower(), None))
            else:
                stack.append(("", None))
            continue
        addr_match = _UBIQUITI_ADDRESS_RE.match(line)
        if addr_match:
            name = _ubiquiti_stack_interface_name(stack)
            if name:
                by_name[name] = ParsedInterface(name=name, ip_address=addr_match.group(1))

    return list(by_name.values())


def _ubiquiti_stack_interface_name(stack: list[tuple[str, str | None]]) -> str | None:
    """Compose an interface name from the open brace-block stack.

    [("interfaces", None), ("ethernet", "eth1"), ("vif", "10")] means
    an `address` line here belongs to "eth1.10" — VyOS/EdgeOS's own
    naming for a tagged VLAN sub-interface.
    """
    base: str | None = None
    vif: str | None = None
    for keyword, ident in stack:
        if keyword in _UBIQUITI_IFACE_KEYWORDS and ident:
            base, vif = ident, None
        elif keyword == "vif" and ident:
            vif = ident
    if base is None:
        return None
    return f"{base}.{vif}" if vif else base
