"""Tests for config_interfaces: parsing interface config out of an
attached device config, and mirroring it onto a device's interfaces.

No PyQt6 import here — this module is pure domain logic and runs in
the same environment as test_entities.py.
"""

from __future__ import annotations

from netplanner.domain.config_interfaces import (
    ParsedInterface,
    interface_match_key,
    mirror_interfaces,
    parse_interfaces,
)
from netplanner.domain.entities import ConfigFormat, Interface, VlanMode


# ------------------------------------------------------------------ plain text
def test_plain_text_has_nothing_to_parse():
    assert parse_interfaces("interface Gig0/1\nip address 10.0.0.1", ConfigFormat.PLAIN_TEXT) == []


def test_empty_content_parses_to_nothing_for_every_format():
    for fmt in ConfigFormat:
        assert parse_interfaces("", fmt) == []


# ------------------------------------------------------------------- Cisco IOS
CISCO_CONFIG = """
version 15.2
service timestamps debug datetime msec
hostname core-sw
!
interface GigabitEthernet0/1
 description Uplink to core
 switchport mode trunk
 switchport trunk allowed vlan 10,20,30
!
interface GigabitEthernet0/2
 switchport access vlan 10
!
interface TenGigabitEthernet0/1
 ip address 172.16.0.1 255.255.255.252
!
interface Vlan10
 ip address 10.0.10.1 255.255.255.0
!
interface Loopback0
 ip address 10.255.0.1 255.255.255.255
!
end
"""


def test_cisco_parses_every_interface_block():
    parsed = parse_interfaces(CISCO_CONFIG, ConfigFormat.CISCO_IOS)
    names = [p.name for p in parsed]
    assert names == [
        "GigabitEthernet0/1", "GigabitEthernet0/2", "TenGigabitEthernet0/1",
        "Vlan10", "Loopback0",
    ]


def test_cisco_trunk_mode_and_vlan_list():
    parsed = parse_interfaces(CISCO_CONFIG, ConfigFormat.CISCO_IOS)
    gi1 = next(p for p in parsed if p.name == "GigabitEthernet0/1")
    assert gi1.vlan_mode is VlanMode.TRUNK
    assert gi1.trunk_vlans == (10, 20, 30)
    assert gi1.access_vlan is None
    assert gi1.ip_address is None


def test_cisco_access_vlan():
    parsed = parse_interfaces(CISCO_CONFIG, ConfigFormat.CISCO_IOS)
    gi2 = next(p for p in parsed if p.name == "GigabitEthernet0/2")
    assert gi2.vlan_mode is VlanMode.ACCESS
    assert gi2.access_vlan == 10
    assert gi2.trunk_vlans == ()


def test_cisco_ip_address_converts_netmask_to_cidr():
    parsed = parse_interfaces(CISCO_CONFIG, ConfigFormat.CISCO_IOS)
    te1 = next(p for p in parsed if p.name == "TenGigabitEthernet0/1")
    assert te1.ip_address == "172.16.0.1/30"
    lo0 = next(p for p in parsed if p.name == "Loopback0")
    assert lo0.ip_address == "10.255.0.1/32"
    assert lo0.vlan_mode is None


def test_cisco_trunk_vlan_range_syntax():
    config = """
interface Gi0/1
 switchport mode trunk
 switchport trunk allowed vlan 10-12,20
!
"""
    parsed = parse_interfaces(config, ConfigFormat.CISCO_IOS)
    assert parsed[0].trunk_vlans == (10, 11, 12, 20)


def test_cisco_malformed_netmask_is_skipped_not_fatal():
    config = """
interface Gi0/1
 ip address 10.0.0.1 not-a-netmask
!
"""
    parsed = parse_interfaces(config, ConfigFormat.CISCO_IOS)
    assert parsed[0].ip_address is None


def test_cisco_trunk_vlan_list_with_non_numeric_junk_is_skipped():
    config = """
interface Gi0/1
 switchport mode trunk
 switchport trunk allowed vlan 10,abc,20
!
"""
    parsed = parse_interfaces(config, ConfigFormat.CISCO_IOS)
    assert parsed[0].trunk_vlans == (10, 20)


def test_cisco_reversed_vlan_range_is_dropped():
    config = "interface Gi0/1\n switchport trunk allowed vlan 20-10\n!\n"
    parsed = parse_interfaces(config, ConfigFormat.CISCO_IOS)
    assert parsed[0].trunk_vlans == ()


def test_cisco_interface_with_no_recognized_lines_still_appears():
    """An interface block whose lines are all things we don't parse
    (e.g. "no shutdown") still yields a ParsedInterface, with every
    field at its default — the mere existence of the interface in the
    config isn't information we throw away."""
    config = "interface Gi0/3\n no shutdown\n description spare\n!\n"
    parsed = parse_interfaces(config, ConfigFormat.CISCO_IOS)
    assert parsed == [ParsedInterface(name="Gi0/3")]


def test_cisco_lines_before_any_interface_are_ignored():
    config = "hostname sw1\nip address 1.2.3.4 255.255.255.0\ninterface Gi0/1\n!\n"
    parsed = parse_interfaces(config, ConfigFormat.CISCO_IOS)
    assert parsed == [ParsedInterface(name="Gi0/1")]


def test_cisco_indented_lines_mentioning_interface_are_not_new_blocks():
    """A real running-config only ever starts an interface declaration
    at column 0; anything indented — including a route-map's "match
    interface Loopback0" — is a sub-command of whatever block is
    already open, not a new interface."""
    config = """
interface GigabitEthernet0/1
 description uplink interface to core
 ip address 10.0.0.1 255.255.255.0
!
route-map SET-INTERFACE permit 10
 match interface Loopback0
!
"""
    parsed = parse_interfaces(config, ConfigFormat.CISCO_IOS)
    assert parsed == [ParsedInterface(name="GigabitEthernet0/1", ip_address="10.0.0.1/24")]


def test_cisco_last_interface_without_trailing_bang_still_flushes():
    config = "interface Gi0/1\n ip address 10.0.0.1 255.255.255.0"
    parsed = parse_interfaces(config, ConfigFormat.CISCO_IOS)
    assert parsed == [ParsedInterface(name="Gi0/1", ip_address="10.0.0.1/24")]


# -------------------------------------------------------------------- MikroTik
MIKROTIK_CONFIG = """
# 2024-01-01 00:00:00 by RouterOS 7.10
/interface ethernet
set [ find default-name=ether1 ] name=ether1-wan
/interface bridge
add name=bridge1
/ip address
add address=192.168.1.1/24 interface=ether1-wan network=192.168.1.0
add address=10.0.0.1/24 interface=bridge1
/ip firewall filter
add chain=input action=accept
"""


def test_mikrotik_extracts_ip_address_lines_only_from_ip_address_path():
    parsed = parse_interfaces(MIKROTIK_CONFIG, ConfigFormat.MIKROTIK)
    by_name = {p.name: p for p in parsed}
    assert by_name["ether1-wan"].ip_address == "192.168.1.1/24"
    assert by_name["bridge1"].ip_address == "10.0.0.1/24"
    assert len(parsed) == 2  # the firewall filter line contributes nothing


def test_mikrotik_set_form_is_recognized_too():
    config = "/ip address\nset 0 address=10.0.0.5/24 interface=ether2\n"
    parsed = parse_interfaces(config, ConfigFormat.MIKROTIK)
    assert parsed == [ParsedInterface(name="ether2", ip_address="10.0.0.5/24")]


def test_mikrotik_address_line_without_a_current_path_is_ignored():
    config = "add address=10.0.0.1/24 interface=ether1\n"
    assert parse_interfaces(config, ConfigFormat.MIKROTIK) == []


def test_mikrotik_comments_and_blank_lines_are_skipped():
    config = "/ip address\n# a comment\n\nadd address=10.0.0.1/24 interface=ether1\n"
    parsed = parse_interfaces(config, ConfigFormat.MIKROTIK)
    assert parsed == [ParsedInterface(name="ether1", ip_address="10.0.0.1/24")]


def test_mikrotik_missing_interface_field_contributes_nothing():
    config = "/ip address\nadd address=10.0.0.1/24\n"
    assert parse_interfaces(config, ConfigFormat.MIKROTIK) == []


def test_mikrotik_repeated_interface_keeps_the_last_address():
    config = (
        "/ip address\n"
        "add address=10.0.0.1/24 interface=ether1\n"
        "add address=10.0.0.2/24 interface=ether1\n"
    )
    parsed = parse_interfaces(config, ConfigFormat.MIKROTIK)
    assert parsed == [ParsedInterface(name="ether1", ip_address="10.0.0.2/24")]


# ------------------------------------------------------------------- Ubiquiti
def test_ubiquiti_set_style_flat_commands():
    config = (
        "set interfaces ethernet eth0 address 192.168.1.1/24\n"
        "set interfaces ethernet eth1 vif 10 address 10.0.10.1/24\n"
    )
    parsed = parse_interfaces(config, ConfigFormat.UBIQUITI)
    by_name = {p.name: p for p in parsed}
    assert by_name["eth0"].ip_address == "192.168.1.1/24"
    assert by_name["eth1.10"].ip_address == "10.0.10.1/24"


def test_ubiquiti_brace_style_nested_blocks():
    config = """
interfaces {
    ethernet eth0 {
        address 192.168.1.1/24
        description "WAN"
    }
    ethernet eth1 {
        vif 10 {
            address 10.0.10.1/24
        }
    }
    bridge br0 {
        address 10.10.0.1/24
    }
}
"""
    parsed = parse_interfaces(config, ConfigFormat.UBIQUITI)
    by_name = {p.name: p for p in parsed}
    assert by_name["eth0"].ip_address == "192.168.1.1/24"
    assert by_name["eth1.10"].ip_address == "10.0.10.1/24"
    assert by_name["br0"].ip_address == "10.10.0.1/24"


def test_ubiquiti_quoted_address_is_unquoted():
    config = 'interfaces {\n    ethernet eth0 {\n        address "192.168.1.1/24"\n    }\n}\n'
    parsed = parse_interfaces(config, ConfigFormat.UBIQUITI)
    assert parsed == [ParsedInterface(name="eth0", ip_address="192.168.1.1/24")]


def test_ubiquiti_address_line_outside_any_interface_block_is_ignored():
    config = "interfaces {\n    address 10.0.0.1/24\n}\n"
    assert parse_interfaces(config, ConfigFormat.UBIQUITI) == []


def test_ubiquiti_unmatched_closing_brace_does_not_raise():
    config = "}\n}\nethernet eth0 {\n    address 10.0.0.1/24\n}\n"
    parsed = parse_interfaces(config, ConfigFormat.UBIQUITI)
    assert parsed == [ParsedInterface(name="eth0", ip_address="10.0.0.1/24")]


def test_ubiquiti_vif_closes_back_to_parent_interface():
    """After a vif block closes, an address line belongs to the parent
    interface again, not the vif."""
    config = (
        "ethernet eth0 {\n"
        "    vif 10 {\n"
        "        address 10.0.10.1/24\n"
        "    }\n"
        "    address 192.168.1.1/24\n"
        "}\n"
    )
    parsed = parse_interfaces(config, ConfigFormat.UBIQUITI)
    by_name = {p.name: p for p in parsed}
    assert by_name["eth0"].ip_address == "192.168.1.1/24"
    assert by_name["eth0.10"].ip_address == "10.0.10.1/24"


# ------------------------------------------------------------- interface_match_key
def test_interface_match_key_unifies_cisco_name_variants():
    variants = ["GigabitEthernet0/1", "Gig0/1", "gig0/1", "Gi0/1", "gi0/1"]
    keys = {interface_match_key(v) for v in variants}
    assert keys == {"gi0/1"}


def test_interface_match_key_unifies_ten_gig_variants():
    variants = ["TenGigabitEthernet0/1", "Ten0/1", "Te0/1"]
    assert len({interface_match_key(v) for v in variants}) == 1


def test_interface_match_key_distinguishes_different_ports():
    assert interface_match_key("Gig0/1") != interface_match_key("Gig0/2")
    assert interface_match_key("Gig0/1") != interface_match_key("Ten0/1")


def test_interface_match_key_is_case_insensitive_for_non_cisco_names():
    assert interface_match_key("Ether1") == interface_match_key("ether1")
    assert interface_match_key("ETH0") == interface_match_key("eth0")


def test_interface_match_key_strips_surrounding_whitespace():
    assert interface_match_key("  Gig0/1  ") == interface_match_key("Gig0/1")


def test_interface_match_key_falls_through_unchanged_for_unrecognized_prefixes():
    """A name matching none of the known Cisco families (RouterOS's
    "bridge1", say) is just lowercased, not mangled."""
    assert interface_match_key("Bridge1") == "bridge1"
    assert interface_match_key("wlan0") == "wlan0"


# ---------------------------------------------------------- vlan-range parsing
def test_cisco_trunk_vlan_list_tolerates_double_commas():
    config = "interface Gi0/1\n switchport trunk allowed vlan 10,,20\n!\n"
    parsed = parse_interfaces(config, ConfigFormat.CISCO_IOS)
    assert parsed[0].trunk_vlans == (10, 20)


def test_cisco_trunk_vlan_range_with_non_numeric_bound_is_dropped():
    config = "interface Gi0/1\n switchport trunk allowed vlan 10-abc,20\n!\n"
    parsed = parse_interfaces(config, ConfigFormat.CISCO_IOS)
    assert parsed[0].trunk_vlans == (20,)


def test_cisco_invalid_netmask_shape_is_skipped():
    """"255.255.255.3" matches the dotted-quad regex but is not a
    contiguous netmask, so ipaddress rejects it — that must not raise
    out of the parser."""
    config = "interface Gi0/1\n ip address 10.0.0.1 255.255.255.3\n!\n"
    parsed = parse_interfaces(config, ConfigFormat.CISCO_IOS)
    assert parsed[0].ip_address is None


def test_ubiquiti_bare_opening_brace_does_not_raise():
    config = "{\n    address 10.0.0.1/24\n}\n"
    assert parse_interfaces(config, ConfigFormat.UBIQUITI) == []


def test_mirror_access_mode_with_no_vlan_number_leaves_access_vlan_alone():
    """A ParsedInterface can state ACCESS mode without a VLAN number
    (config_interfaces' own contract, even though _parse_cisco never
    produces this combination itself) — the existing access_vlan must
    survive untouched rather than being reset."""
    existing = [Interface(name="Gig0/1", vlan_mode=VlanMode.TRUNK, access_vlan=7)]
    parsed = [ParsedInterface(name="Gig0/1", vlan_mode=VlanMode.ACCESS, access_vlan=None)]
    result = mirror_interfaces(existing, parsed)
    assert result[0].vlan_mode is VlanMode.ACCESS
    assert result[0].access_vlan == 7


# ------------------------------------------------------------------ mirroring
def test_mirror_updates_ip_on_a_matched_interface():
    existing = [Interface(name="Gig0/1")]
    parsed = [ParsedInterface(name="GigabitEthernet0/1", ip_address="10.0.0.1/24")]
    result = mirror_interfaces(existing, parsed)
    assert len(result) == 1
    assert result[0].name == "Gig0/1"  # existing name is not renamed
    assert result[0].ip_address == "10.0.0.1/24"


def test_mirror_preserves_id_mac_and_speed_on_a_matched_interface():
    original = Interface(
        name="Gig0/1", mac_address="aa:bb:cc:dd:ee:ff", max_speed_mbps=2500
    )
    result = mirror_interfaces(
        [original], [ParsedInterface(name="Gig0/1", ip_address="10.0.0.1/24")]
    )
    assert result[0].id == original.id
    assert result[0].mac_address == "aa:bb:cc:dd:ee:ff"
    assert result[0].max_speed_mbps == 2500


def test_mirror_appends_a_new_interface_for_no_match():
    existing = [Interface(name="Gig0/1")]
    parsed = [ParsedInterface(name="Vlan10", ip_address="10.0.10.1/24")]
    result = mirror_interfaces(existing, parsed)
    assert [i.name for i in result] == ["Gig0/1", "Vlan10"]
    assert result[1].ip_address == "10.0.10.1/24"
    assert result[1].id != existing[0].id


def test_mirror_leaves_interfaces_the_config_never_mentions_untouched():
    existing = [Interface(name="Gig0/1", ip_address="1.1.1.1/32"), Interface(name="Gig0/2")]
    result = mirror_interfaces(existing, [ParsedInterface(name="Gig0/1", ip_address="2.2.2.2/32")])
    untouched = next(i for i in result if i.name == "Gig0/2")
    assert untouched.ip_address is None
    assert untouched.id == existing[1].id


def test_mirror_with_no_parsed_interfaces_is_a_pure_copy():
    existing = [Interface(name="Gig0/1", ip_address="1.1.1.1/32")]
    result = mirror_interfaces(existing, [])
    assert len(result) == 1
    assert result[0].id == existing[0].id
    assert result[0].ip_address == "1.1.1.1/32"
    assert result[0] is not existing[0]  # a copy, not the same object


def test_mirror_does_not_mutate_the_original_interfaces():
    original = Interface(name="Gig0/1", ip_address="1.1.1.1/32")
    existing = [original]
    mirror_interfaces(existing, [ParsedInterface(name="Gig0/1", ip_address="9.9.9.9/32")])
    assert original.ip_address == "1.1.1.1/32"  # untouched


def test_mirror_trunk_replaces_rather_than_appends_to_existing_trunk_vlans():
    existing = [Interface(name="Gig0/1", vlan_mode=VlanMode.TRUNK, trunk_vlans=[1, 2, 3])]
    parsed = [ParsedInterface(name="Gig0/1", vlan_mode=VlanMode.TRUNK, trunk_vlans=(10, 20))]
    result = mirror_interfaces(existing, parsed)
    assert result[0].trunk_vlans == [10, 20]


def test_mirror_access_vlan_of_none_in_parsed_leaves_existing_access_vlan():
    """A TRUNK ParsedInterface never carries an access_vlan, so
    switching a previously-access port to trunk must not silently reset
    access_vlan away from whatever it already was — it's simply
    irrelevant once the port is a trunk."""
    existing = [Interface(name="Gig0/1", vlan_mode=VlanMode.ACCESS, access_vlan=99)]
    parsed = [ParsedInterface(name="Gig0/1", vlan_mode=VlanMode.TRUNK, trunk_vlans=(10,))]
    result = mirror_interfaces(existing, parsed)
    assert result[0].vlan_mode is VlanMode.TRUNK
    assert result[0].access_vlan == 99  # untouched, and meaningless now anyway


def test_mirror_a_parsed_interface_with_no_fields_set_is_a_no_op_on_match():
    existing = [Interface(name="Gig0/1", ip_address="1.1.1.1/32", access_vlan=5)]
    result = mirror_interfaces(existing, [ParsedInterface(name="Gig0/1")])
    assert result[0].ip_address == "1.1.1.1/32"
    assert result[0].access_vlan == 5


def test_mirror_matches_multiple_existing_interfaces_independently():
    existing = [Interface(name="Gig0/1"), Interface(name="Gig0/2"), Interface(name="Gig0/3")]
    parsed = [
        ParsedInterface(name="GigabitEthernet0/1", ip_address="10.0.0.1/24"),
        ParsedInterface(name="GigabitEthernet0/3", ip_address="10.0.0.3/24"),
    ]
    result = mirror_interfaces(existing, parsed)
    by_name = {i.name: i for i in result}
    assert by_name["Gig0/1"].ip_address == "10.0.0.1/24"
    assert by_name["Gig0/2"].ip_address is None
    assert by_name["Gig0/3"].ip_address == "10.0.0.3/24"


def test_end_to_end_cisco_config_mirrors_onto_default_router_ports():
    """The scenario the feature exists for: a real running-config
    synced onto the default interfaces a new router starts with."""
    from netplanner.domain.entities import DeviceType
    from netplanner.domain.interfaces import default_interfaces

    existing = default_interfaces(DeviceType.ROUTER)  # Gig0/0..Gig0/3
    parsed = parse_interfaces(CISCO_CONFIG, ConfigFormat.CISCO_IOS)
    result = mirror_interfaces(existing, parsed)

    names = [i.name for i in result]
    # Original router ports kept their own (abbreviated) names...
    assert "Gig0/0" in names and "Gig0/1" in names
    # ...GigabitEthernet0/1 from the config matched Gig0/1 rather than
    # being appended as a duplicate under its full config-stated name.
    assert "GigabitEthernet0/1" not in names
    gi1 = next(i for i in result if i.name == "Gig0/1")
    assert gi1.vlan_mode is VlanMode.TRUNK
    assert gi1.trunk_vlans == [10, 20, 30]
    # Interfaces the config never mentioned (Gig0/2, Gig0/3 - only
    # 0/1 and 0/2 appear in CISCO_CONFIG, and this router template
    # only goes up to Gig0/3) round-trip untouched.
    gi0 = next(i for i in result if i.name == "Gig0/0")
    assert gi0.ip_address is None
    # Interfaces with no router-port counterpart (Vlan10, Loopback0,
    # TenGigabitEthernet0/1) were appended under their config names.
    assert "Vlan10" in names and "Loopback0" in names


# --------------------------------------------------------- undo/redo integration
def test_syncing_through_the_real_controller_is_a_single_undoable_step():
    """The path canvas.py actually drives: DevicePropertiesDialog
    merges parsed config data into its in-memory interfaces, then
    edit_device_properties commits everything (model, notes, VLAN,
    status, interfaces) as one push onto the real CommandStack. This
    exercises that whole chain without going through Qt at all — the
    merge itself needs no GUI, only the dialog wiring in
    test_coverage_dialogs.py does."""
    from unittest.mock import MagicMock

    from netplanner.app.controller import AppController
    from netplanner.domain.entities import DeviceStatus, DeviceType

    controller = AppController(repository=MagicMock())
    device = controller.add_device("sw1", DeviceType.SWITCH, 0, 0)
    before_ids = [i.id for i in device.interfaces]
    before_ips = [i.ip_address for i in device.interfaces]

    parsed = parse_interfaces(CISCO_CONFIG, ConfigFormat.CISCO_IOS)
    mirrored = mirror_interfaces(device.interfaces, parsed)

    controller.edit_device_properties(
        device.id,
        device.device_model,
        device.loopback_ip,
        device.notes,
        device.native_vlan,
        DeviceStatus.ACTIVE,
        mirrored,
    )

    after = controller.plan.get_device(device.id)
    assert after is not None
    gi1 = next(i for i in after.interfaces if i.name == "Gig0/1")
    assert gi1.vlan_mode is VlanMode.TRUNK
    assert gi1.trunk_vlans == [10, 20, 30]
    assert any(i.name == "Vlan10" for i in after.interfaces)  # newly appended

    controller.undo()
    reverted = controller.plan.get_device(device.id)
    assert reverted is not None
    assert [i.id for i in reverted.interfaces] == before_ids
    assert [i.ip_address for i in reverted.interfaces] == before_ips
    assert not any(i.name == "Vlan10" for i in reverted.interfaces)

    controller.redo()
    redone = controller.plan.get_device(device.id)
    assert redone is not None
    assert any(i.name == "Vlan10" for i in redone.interfaces)
