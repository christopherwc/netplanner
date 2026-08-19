"""Validation rules for network plans.

Each rule returns a list of Issue objects; the controller surfaces them
in the GUI (e.g. a problems panel).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from ipaddress import ip_interface

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
    issues += _check_isolated_devices(plan)
    issues += _check_overlapping_subnets(plan)
    return issues


def _check_duplicate_ips(plan: NetworkPlan) -> list[Issue]:
    seen: dict[str, str] = {}  # ip -> device name
    issues = []
    for device in plan.devices:
        for iface in device.interfaces:
            if not iface.ip_address:
                continue
            ip = str(ip_interface(iface.ip_address).ip)
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
    issues = []
    subnets = list(plan.subnets.values())
    for i, a in enumerate(subnets):
        for b in subnets[i + 1 :]:
            if a.network.overlaps(b.network):
                issues.append(
                    Issue(Severity.ERROR, f"Subnets {a.cidr} and {b.cidr} overlap")
                )
    return issues
