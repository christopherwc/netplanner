# NetPlanner

[![CI](https://github.com/christopherwc/netplanner/actions/workflows/ci.yml/badge.svg)](https://github.com/christopherwc/netplanner/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/downloads/)

A network planning tool for Linux with a PyQt6 GUI, SQLite persistence,
and PDF/PNG export. Design network diagrams Packet Tracer-style: place
equipment, cable it up port-by-port, and export the result.

## Features

- **Equipment palette** — routers, switches, firewalls, servers, access
  points, dish radios (PtP), AP radios (sector), and workstations, each
  with its own color and glyph. Click a type, click the canvas to place;
  devices are auto-named (`rtr1`, `sw1`, `dish1`, ...).
- **Typed interfaces** — every device is created with a realistic set of
  ports (e.g. routers get 4x 1 Gbps, switches get 8x 1 Gbps + 2x 10 Gbps
  uplinks, radios get a wireless port). Right-click a device →
  *Edit properties…* → **Interfaces** tab to add/remove any number of
  ports of any type: **Wireless, 1 Gbps, 10 Gbps, 25 Gbps, or 100 Gbps**,
  with optional IP addresses in CIDR notation.
- **Custom interface types** — the Type column is a dropdown you can also
  type into. Pick one of the five built-in classes, or enter your own
  name for media they don't cover: `SFP28 DAC`, `T1 serial`,
  `DOCSIS 3.1`, `10GBASE-LR`. A custom name is a label — the preset you
  had selected stays underneath as the port's default speed, so a
  renamed port never loses its rate. Typing a preset's name verbatim
  (any casing) selects that preset rather than storing a lookalike
  label, and clearing the field returns the port to its preset.
- **Per-port speed override** — the Type column covers the common cases;
  the **Speed** column next to it covers everything else. Leave it on
  *Default* to follow the type, or pick a preset, or just type the real
  figure: `2.5G`, `850`, `200 Mbps`, `40 Gb/s`. A bare number is read as
  Mbps. This is how you record a 2.5 GbE access port, a licensed radio
  that negotiates 450 Mbps, or a handoff rate-limited below its port
  speed. Wireless ports have no nominal rate, so a manual figure is the
  only way to give one a speed — and link derivation then uses it.
- **MAC addresses** — every interface starts with an all-zeros placeholder
  MAC (`00:00:00:00:00:00`), editable per-port in the Interfaces tab when
  documenting real hardware. Plans saved before this feature load fine;
  missing MACs get the same placeholder on load.
- **Device properties** — right-click a device → *Edit properties…* →
  **General** tab for:
  - **Device model** — free text (e.g. "Cisco ISR 4331"), shown under
    the device name.
  - **Loopback IP** — a device-level IP (CIDR) not tied to any physical
    interface, shown as its own highlighted line on the card.
  - **Native VLAN** — a device-wide VLAN ID (1–4094), defaulting to
    **1**, shown as its own line on the card just like an unconfigured
    switch's default VLAN.
  - **Status** — a deployment tag: **Active**, **Planned**, or
    **Broken** (see below for how each renders).
  - **Notes** — free-form text, word-wrapped and shown at the bottom of
    the card (up to 3 lines, truncated with "…" if longer — the full
    text is still saved).

  All of the above are undoable together as one edit and visible on the
  canvas by default.
- **Sites** — click **▭ Site** under *Annotations*, then click the
  canvas to drop a location box. Sites sit **behind** everything else
  and mark where equipment physically lives — a room, rack, building,
  or closet. Each has a coloured header with its **name** and a
  **notes** section shown right on the box (address, rack numbers,
  access instructions, contacts).

  Drag the body to move a site; drag the **bottom-right corner** to
  resize it. Double-click (or right-click → *Edit site…*) to change the
  name, notes, and colour. Sites render on the canvas and in both
  exports, and the exported page grows to include them.

  Membership is **positional**: a device belongs to whichever site its
  box covers, so dragging equipment in or out just works — there's no
  list to maintain and nothing to fall out of sync. Deleting a site
  removes only the box; the devices drawn on it stay exactly where they
  are.
- **Text boxes** — click **🗨 Text box** under *Annotations* in the
  palette, then click the canvas to place a free-floating label. Set the
  text, font size, bold, color, and wrap width in the dialog; drag to
  reposition, double-click (or right-click → *Edit text…*) to change it,
  and Delete to remove it. Annotations render on the canvas and in both
  PDF and PNG exports, and the exported page grows to include notes that
  sit outside the device cluster.

  Annotations paint their own white panel, so they stay readable on any
  desktop theme and where they overlap a device card. The canvas itself
  keeps your system theme's background.

  Text boxes are **not** part of the topology: they have no ports, take
  no links, never appear in validation, and deleting a device never
  removes them. Use them to label regions ("DMZ", "Rack 3"), record
  caveats, or mark planned changes.
- **VLAN highlighting** — the **VLANs** dock lists every VLAN in the
  plan with a colour swatch, its name (from the VLAN catalog, if set),
  and where it appears — e.g. *"3 device(s) · 6 access, 2 trunk, native
  on 1"*. Every interface on every card shows a small colour chip per
  VLAN it carries, so membership is scannable without reading numbers.

  Tick one or more VLANs to **highlight** them: matching devices and
  interfaces keep full colour, everything else dims to gray. Untick to
  return to the normal view. *Select all* / *Clear* toggle the whole
  list at once. Highlighting only recolours — card sizes and device
  positions never shift, so the diagram stays stable while you explore.

  A VLAN's colour comes from its ID, not from the order VLANs appear,
  so VLAN 20 is the same colour in every plan, every session, and every
  export. Exports honour the active filter, so a filtered PDF or PNG
  shows exactly what was on screen — useful for handing someone a
  "here's just VLAN 30" diagram.
- **Link labels and speed** — double-click a cable (or right-click →
  *Edit link…*) to set a **label**, change its **media type**, and
  record its **bandwidth**. The label is drawn on the cable on the
  canvas and in exports — useful for circuit IDs, carrier references,
  or capacity notes. Labels and port names sit just above the cable so
  the line never strikes through the text.

  **Bandwidth entry accepts Mbps or Gbps**, switchable in the dialog;
  the stored value is always Mbps, so switching units converts the
  value rather than reinterpreting the number. Sub-gigabit links are
  fine — 500 Mbps shows as 0.5 Gbps.

  **Links derive their speed from the ports they connect**, taking the
  *slower* of the two interfaces: patch a 10 Gbps port into a 1 Gbps
  port and the link records 1 Gbps, because that's what it actually
  carries. Wireless interfaces have no fixed line rate, so they're
  skipped in favour of the wired end; a link between two wireless ports
  is left unset for you to fill in.

  **The speed keeps tracking the interfaces**: change a port's type *or
  its manual speed* in the device properties dialog and every attached
  link recomputes automatically — upgrade that 1 Gbps port to 25 Gbps
  and the link becomes 10 Gbps, now capped by the other end; throttle a
  10 Gbps port to `2.5G` and both it and the link follow. It reverts
  with the same single Ctrl+Z that undoes the interface edit.

  Tracking is shown as a **Track interface speeds** checkbox in the link
  dialog. Typing a bandwidth by hand unticks it, and from then on that
  link keeps your figure no matter what happens to the ports — a
  measured or contracted rate is real data and is never overwritten.
  Re-tick the box to resume tracking.
- **Status tags** — every device is tagged **Active**, **Planned**, or
  **Broken**, changing how its whole card is painted (the device-type
  color scheme is always kept; statuses differ only in the stripe
  overlay):
  - **Active** (default): normal device-type colors, no overlay.
  - **Planned**: a diagonal **gray** stripe pattern overlaid across the
    card — useful for equipment that's designed but not yet installed.
  - **Broken**: a diagonal stripe pattern alternating **red and black**
    (hazard-tape style) overlaid across the card — failed equipment
    stands out at a glance.

  Stripes are drawn semi-transparently so the card's text (IPs, MACs,
  VLANs, notes) stays readable underneath. Status is set from the
  **General** tab of *Edit properties…* and renders identically on the
  canvas, in PDF exports, and in PNG exports.
- **Configuration files** — attach real device configs to any device via
  *Edit properties…* → **Configs**. Import one or more files from disk;
  the vendor format is auto-detected (**Cisco IOS**, **MikroTik
  RouterOS**, **Ubiquiti**, or plain text) and files are stored *inside*
  the plan, so a saved plan or exported `.netplan` carries its configs
  and stays readable on another machine. Double-click a file to open a
  read-only viewer with line numbers, find-as-you-type, and vendor-aware
  highlighting (comments, commands, IP addresses, quoted strings). You
  can also rename a file, or save a copy back out to disk. Devices with
  attached configs show an indicator on their card, on the canvas and in
  exports.

  Viewing is deliberately read-only: a stored config documents what the
  hardware actually runs, and silently editing it would let the plan
  drift from reality. Re-import to update one.
- **VLANs per interface** — every interface has its own VLAN
  configuration, set from the **Interfaces** tab of *Edit properties…*:
  - **Access mode** (default): the interface carries a single VLAN,
    untagged. Defaults to VLAN 1.
  - **Trunk mode**: the interface carries multiple VLANs, tagged —
    enter a comma-separated list (e.g. `10,20,30`) in the VLAN(s)
    column.

  Each interface's VLAN membership is shown on the card right under its
  MAC address (`VLAN 10` or `Trunk: 10,20,30`), in blue. Plan → Validate
  flags a trunk port left with no VLANs assigned.
- **Device cards** — devices render as sectioned cards: name header,
  optional model line, a colored device-type band, the native VLAN
  line, an optional loopback line, the interface list (name, IP, MAC,
  and VLAN per port, capped at 6 with a "+N more…" overflow), and
  optional notes at the bottom — all visible by default. *View → Show
  device details* toggles back to compact glyph+name nodes for dense
  diagrams. PDF/PNG exports draw identical cards via the shared
  `export/nodecard.py` layout.
- **Media-typed connections** — pick Copper/Ethernet, Fiber, Wireless,
  Serial, or WAN from the palette, then click two devices. Each click
  pops up that device's free ports so you choose exactly which
  interface the cable lands on; occupied ports don't appear. Each media
  type has a distinct line style (color + dash pattern).
- **No overlapping links** — parallel links between the same two devices
  automatically fan out so every cable stays visible.
- **Editing** — drag to move, double-click to rename, full undo/redo
  (Ctrl+Z / Ctrl+Shift+Z) for every mutation including interface edits.
- **Deleting** — select any device or cable and press **Delete**, or
  right-click it and choose *Delete device* / *Delete link*
  (*Edit → Delete selected* works too). Rubber-band select to remove
  several at once. Deleting a device also removes the cables attached
  to it — you're warned first, and a single undo brings the device and
  all of its cabling back, interface assignments intact. Deleting a
  cable frees the ports at both ends for reuse.
- **Validation** — Plan → Validate flags duplicate IPs, duplicate MACs
  (typically a copy-paste typo), overlapping subnets, and devices with
  no links.
- **Auto-layout** — spring, circular, or Kamada-Kawai via networkx
  (backed by numpy/scipy, which are project dependencies; if they're
  ever missing the layout degrades to a simple circle instead of
  crashing).
- **Persistence** — plans are saved to SQLite
  (`~/.local/share/netplanner/plans.db`); portable `.netplan` JSON
  import/export is also available.
- **Export** — one-click PDF (reportlab) and PNG (Pillow) that match the
  canvas exactly: same colors, line styles, fan-out, and port labels.

## Architecture

Five layers, strictly downward dependencies (GUI never leaks into core):

| Layer | Package | Contents |
|---|---|---|
| Presentation | `netplanner/gui/` | Main window, canvas, palette, panels, dialogs |
| Application | `netplanner/app/` | Controller, undo/redo commands, validation |
| Domain | `netplanner/domain/` | Entities, networkx plan model, interface templates, layout |
| Export | `netplanner/export/` | Shared renderer + geometry, PDF and PNG exporters, style tables |
| Persistence | `netplanner/persistence/` | SQLAlchemy repository, SQLite schema, `.netplan` files |

`export/styles.py` and `export/geometry.py` are shared by the canvas and
both exporters, which is what keeps on-screen and exported output
identical.

## Setup

Arch Linux:

```bash
sudo pacman -S python
```

Debian/Ubuntu:

```bash
sudo apt install python3 python3-venv libgl1 libegl1
```

Then:

```bash
python3 -m venv venv && source venv/bin/activate
pip install -e ".[dev]"
```

## Run

```bash
netplanner
# or: python -m netplanner.main
```

### Quick tour

1. Click **Router** in the palette, click the canvas — `rtr1` appears
   with four gigabit ports.
2. Click **Switch**, place `sw1`.
3. Click **Copper / Ethernet** under Connections, click `rtr1`, pick
   `Gig0/0`, click `sw1`, pick `Gig0/1` — cabled.
4. Right-click `sw1` → *Edit properties…* → **Interfaces** tab to add a
   25 Gbps port, set IPs, paste in real MAC addresses, or switch a port
   to **Trunk** mode and list its VLANs (e.g. `10,20,30`).
5. Switch to the **General** tab to set a device model, a loopback IP,
   a native VLAN (defaults to 1), a status (Active/Planned/Broken), and
   notes — all show up on the card immediately, with Planned adding
   gray stripes and Broken adding red/black hazard stripes.
6. Each device card lists every port with its IP, MAC, and VLAN —
   uncheck **View → Show device details** if you prefer compact nodes.
7. **File → Export PDF…** for a shareable diagram.

Esc always returns to Select/Move mode.

## Test

```bash
pytest
```

The GUI tests need a Qt platform plugin; they set `QT_QPA_PLATFORM=offscreen`
themselves, so no display server is required.

To run what CI runs:

```bash
pip install -e ".[dev]"
ruff check .                                    # lint
pytest --cov=netplanner --cov-fail-under=100    # tests + coverage gate
python .github/scripts/startup_smoke.py         # launches the app, quits itself
```

Coverage is at 100% of lines and the gate enforces it, so new code needs
tests to merge. Warnings are errors (`filterwarnings = ["error"]`), which
is how a deprecation in PyQt6 or SQLAlchemy announces itself on the weekly
scheduled run rather than on launch day.

## CI/CD

`.github/workflows/ci.yml` runs on every push and pull request, weekly on
a schedule, and is called by the release workflow:

| Job | What it guards |
| --- | --- |
| Lint | ruff, reported as inline annotations on the diff |
| Type check | mypy — advisory today, see the note in the workflow |
| Tests | Python 3.10–3.14, gated at 100% line coverage |
| Startup smoke | launches the real entry point; catches import errors a green suite misses |
| Dependency audit | pip-audit against the declared dependencies |
| Package | builds sdist and wheel, installs the wheel clean, checks the console script |

Tagging `vX.Y.Z` runs the same pipeline, verifies the tag matches the
version in `pyproject.toml`, and publishes a GitHub release with the
distributions and their checksums.

## Project files

- Database: `~/.local/share/netplanner/plans.db` (respects `XDG_DATA_HOME`)
- JSON projects: any path you choose, `.netplan` extension by convention
