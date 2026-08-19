# NetPlanner

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
  *Edit interfaces…* to add/remove any number of ports of any type:
  **Wireless, 1 Gbps, 10 Gbps, 25 Gbps, or 100 Gbps**, with optional IP
  addresses in CIDR notation.
- **Media-typed connections** — pick Copper/Ethernet, Fiber, Wireless,
  Serial, or WAN from the palette, then click two devices. Each click
  pops up that device's free ports so you choose exactly which
  interface the cable lands on; occupied ports don't appear. Each media
  type has a distinct line style (color + dash pattern).
- **No overlapping links** — parallel links between the same two devices
  automatically fan out so every cable stays visible.
- **Editing** — drag to move, double-click to rename, full undo/redo
  (Ctrl+Z / Ctrl+Shift+Z) for every mutation including interface edits.
- **Validation** — Plan → Validate flags duplicate IPs, overlapping
  subnets, and devices with no links.
- **Auto-layout** — spring, circular, or Kamada-Kawai via networkx.
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
4. Right-click `sw1` → *Edit interfaces…* to add a 25 Gbps port.
5. **File → Export PDF…** for a shareable diagram.

Esc always returns to Select/Move mode.

## Test

```bash
pytest
```

## Project files

- Database: `~/.local/share/netplanner/plans.db` (respects `XDG_DATA_HOME`)
- JSON projects: any path you choose, `.netplan` extension by convention
