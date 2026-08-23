"""Validation rules for network plans.

Each rule returns a list of Issue objects; the controller surfaces them
in the GUI (e.g. a problems panel).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from ipaddress import ip_interface

from netplanner.domain.entities import VlanMode
from netplanner.domain.model import NetworkPlan


class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class Issue:
    severity: Severity
    message: str
    device_id: str | None = None


def validate(plan: NetworkPlan) -> list[Issue]:
    issues: list[Issue] = []
    issues += _check_duplicate_ips(plan)
    issues += _check_duplicate_macs(plan)
    issues += _check_isolated_devices(plan)
    issues += _check_overlapping_subnets(plan)
    issues += _check_empty_trunks(plan)
    return issues


def _check_duplicate_ips(plan: NetworkPlan) -> list[Issue]:
    """Flag repeated addresses, and addresses that are not addresses.

    The IP field is free text, so a typo like 10.0.0.256/24 reaches here
    intact. Parsing it raised ValueError out of the whole validation
    run, which meant the one tool built to report bad data instead
    failed on it. A malformed address is now simply an issue.
    """
    seen: dict[str, str] = {}  # ip -> device name
    issues = []
    for device in plan.devices:
        for iface in device.interfaces:
            if not iface.ip_address:
                continue
            try:
                ip = str(ip_interface(iface.ip_address).ip)
            except ValueError:
                issues.append(
                    Issue(
                        Severity.ERROR,
                        f"Interface '{iface.name}' on '{device.name}' has an "
                        f"unreadable IP address: {iface.ip_address!r}",
                        device_id=device.id,
                    )
                )
                continue
            if ip in seen:
                issues.append(
                    Issue(
                        Severity.ERROR,
                        f"Duplicate IP {ip} on '{device.name}' (also on '{seen[ip]}')",
                        device_id=device.id,
                    )
                )
            else:
                seen[ip] = device.name
    return issues


def _check_isolated_devices(plan: NetworkPlan) -> list[Issue]:
    return [
        Issue(Severity.WARNING, f"Device '{d.name}' has no links", device_id=d.id)
        for d in plan.isolated_devices()
    ]


def _check_overlapping_subnets(plan: NetworkPlan) -> list[Issue]:
    """Flag overlapping subnets, and CIDRs that will not parse.

    Same reasoning as the IP rule: a subnet is stored as typed, so the
    parse has to happen inside the check and a failure has to become an
    issue rather than an exception.
    """
    issues = []
    parsed = []
    for subnet in plan.subnets.values():
        try:
            parsed.append((subnet, subnet.network))
        except ValueError as exc:
            issues.append(
                Issue(Severity.ERROR, f"Subnet {subnet.cidr!r} is not a valid network: {exc}")
            )
    for i, (a, a_net) in enumerate(parsed):
        for b, b_net in parsed[i + 1 :]:
            if a_net.overlaps(b_net):
                issues.append(
                    Issue(Severity.ERROR, f"Subnets {a.cidr} and {b.cidr} overlap")
                )
    return issues


def _check_duplicate_macs(plan: NetworkPlan) -> list[Issue]:
    """Flag MAC addresses appearing on more than one interface.

    Auto-generated MACs are effectively unique; duplicates typically mean
    a user typo while editing, so they are worth surfacing. The default
    placeholder MAC (all zeros, see domain.entities.blank_mac) is
    excluded: it means "not yet assigned," not a real collision, and
    every freshly created interface starts with it.
    """
    from netplanner.domain.entities import blank_mac

    placeholder = blank_mac().upper()
    seen: dict[str, str] = {}  # normalized mac -> device name
    issues = []
    for device in plan.devices:
        for iface in device.interfaces:
            mac = iface.mac_address.strip().upper()
            if not mac or mac == placeholder:
                continue
            if mac in seen:
                issues.append(
                    Issue(
                        Severity.WARNING,
                        f"Duplicate MAC {mac} on '{device.name}' (also on '{seen[mac]}')",
                        device_id=device.id,
                    )
                )
            else:
                seen[mac] = device.name
    return issues


def _check_empty_trunks(plan: NetworkPlan) -> list[Issue]:
    """Flag trunk interfaces carrying no VLANs at all.

    A trunk with zero allowed VLANs passes no traffic and almost
    always means the trunk was configured but never assigned VLANs.
    """
    issues = []
    for device in plan.devices:
        for iface in device.interfaces:
            if iface.vlan_mode is VlanMode.TRUNK and not iface.trunk_vlans:
                issues.append(
                    Issue(
                        Severity.WARNING,
                        f"Interface '{iface.name}' on '{device.name}' is a trunk "
                        "with no VLANs assigned",
                        device_id=device.id,
                    )
                )
    return issues
