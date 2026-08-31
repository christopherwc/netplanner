# NetPlanner

[![CI](https://github.com/christopherwc/netplanner/actions/workflows/ci.yml/badge.svg)](https://github.com/christopherwc/netplanner/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/downloads/)
[![uv](https://img.shields.io/badge/packaged%20with-uv-de5fe9)](https://docs.astral.sh/uv/)

A network planning tool for Linux with a PyQt6 GUI, SQLite persistence,
and PDF/PNG export. Design network diagrams Packet Tracer-style: place
equipment, cable it up port-by-port, and export the result.

## Features

- **Equipment palette** — routers, switches, firewalls, servers, access
  points, dish radios (PtP), AP radios (sector), and workstations, each
  with its own color and glyph. Click a type, click the canvas to place;
  devices are auto-named (`rtr1`, `sw1`, `dish1`, ...).
- **Interfaces** — every device is created with a realistic set of ports
  (e.g. routers get 4x 1 Gbps, switches get 8x 1 Gbps + 2x 10 Gbps
  uplinks, radios get a port with no rate yet). Right-click a device →
  *Edit properties…* → **Interfaces** tab to add or remove any number of
  ports, set their speeds, and give them IP addresses in CIDR notation.
- **Maximum Interface Speed** — a port's rate is the whole of its
  physical specification, and it is stated outright rather than picked
  from a list of classes. Type the number into **Maximum Interface
  Speed**; the **Unit** column beside it says what the number means and
  starts at **Gbps**, so `2.5` is 2.5 Gbps. Switch the row to Mbps and
  `850` is 850 Mbps.

  Any rate is expressible, so a 2.5 GbE access port, a licensed radio
  measured at 450 Mbps, and a handoff rate-limited below its port speed
  are all just figures — there is no preset list to fall outside of.
  Switching the unit **re-expresses** the figure rather than rescaling
  it: a 2.5 Gbps port shown in Mbps reads `2500`, and the port still
  runs at the same speed. A rate opens in whichever unit reads better,
  so an 850 Mbps port comes up in Mbps and a 2.5 Gbps one in Gbps.

  **Leave the field blank when the rate is not known.** A radio nobody
  has surveyed yet is carried as unmeasured rather than filled in with a
  plausible number. A link to it then takes whatever the other end
  states; only a link with no rate at either end is left unset for you
  to complete.
- **Negotiated** — the column to the right of Unit, and not an input. It
  shows what the port will actually run at once the far end has had its
  say: the slower of its own maximum and the maximum of the port it is
  patched into. Patch a 40 Gbps port into a 10 Gbps one and it reads
  10 Gbps. It updates as you type, so the row always shows what pressing
  OK would produce. A port with no rate at either end shows `—`.
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
  carries. A port whose rate is not known is skipped in favour of the
  end that has one; a link between two such ports is left unset for you
  to fill in.

  **The speed keeps tracking the interfaces**: change a port's maximum
  speed in the device properties dialog and every attached link
  recomputes automatically — raise that 1 Gbps port to 25 Gbps and the
  link becomes 10 Gbps, now capped by the other end; throttle a 10 Gbps
  port to 2.5 and both it and the link follow. It reverts with the same
  single Ctrl+Z that undoes the interface edit.

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

Dependencies are managed with [uv](https://docs.astral.sh/uv/). Install it
first:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# or: pacman -S uv   (Arch)   |   pipx install uv   (anywhere)
```

Qt links against a handful of system libraries even though PyQt6 bundles
Qt itself. Arch Linux:

```bash
sudo pacman -S glib2 libglvnd libxkbcommon-x11 dbus fontconfig freetype2 zstd
```

Debian/Ubuntu:

```bash
sudo apt install libglib2.0-0 libegl1 libgl1 libx11-6 libxkbcommon-x11-0 \
                 libdbus-1-3 libfontconfig1 libfreetype6 libzstd1
```

A desktop install has all of these already; the full list matters on a
minimal system. It is what `objdump -p` reports as NEEDED for Qt's core
libraries and the offscreen platform plugin, minus what the PyQt6 wheels
bundle themselves (ICU).

Then, from the repository root:

```bash
uv sync --extra dev
```

That creates `.venv/`, installs the exact versions recorded in `uv.lock`,
and installs NetPlanner itself in editable mode. uv fetches its own
Python if you do not have 3.12+; there is no separate venv step.

Commands run through `uv run`, which activates the environment for you:

```bash
uv run netplanner        # launch the app
uv run pytest            # run the tests
```

`source .venv/bin/activate` works too if you prefer an activated shell.

## Dependency pinning

Every runtime and development dependency is pinned to an exact version in
`pyproject.toml` (`numpy==2.5.2`, not `numpy>=1.26`), and `uv.lock`
records the resolved version **and the SHA-256 hash** of every artifact,
including transitive ones.

This is a supply-chain measure, and it buys three specific things:

- **An update is a commit, not an event.** With a floor like `>=1.26`, a
  compromised or simply broken release enters the project the next time
  anyone runs an install, on whatever machine happens to install first.
  With a pin, that release cannot arrive until someone changes a line and
  a reviewer approves it. The dependency set becomes reviewable history.
- **The artifact is verified, not just named.** A version number alone
  does not tell you that the file you downloaded is the file the
  maintainer published. `uv sync` checks each download against the hash
  in `uv.lock` and fails if it differs, so a tampered or substituted
  artifact stops the install rather than executing.
- **What CI tested is what ships.** Developer machines, all three CI
  Python versions, and the release job resolve to identical bytes, so a
  green pipeline is evidence about the artifact users actually get — not
  about a similar one.

**The tradeoff is real and worth stating: pins go stale, and stale pins
miss security fixes.** Pinning only helps if something moves the pins.
That job belongs to Dependabot (`.github/dependabot.yml`), which opens a
grouped PR weekly against the `uv` ecosystem; the pipeline then runs in
full against the proposed versions before anyone merges. Pinning without
that is worse than floors, not better.

CI enforces this with `uv sync --locked` and `UV_LOCKED=1`: if
`pyproject.toml` and `uv.lock` ever disagree, the run fails instead of
quietly installing a set nobody reviewed. (`--frozen` is the weaker
flag — it installs from the lock but accepts one that has fallen behind
`pyproject.toml`, which is exactly the case worth catching.)

To move a pin deliberately:

```bash
uv lock --upgrade-package numpy      # or --upgrade for everything
uv sync --extra dev
uv run pytest --cov=netplanner --cov-fail-under=100
```

Commit `pyproject.toml` and `uv.lock` together — they are one unit, and
`--locked` will reject them if they drift apart.

### Python version support

NetPlanner requires **Python 3.12 or newer**. That floor is a consequence
of pinning rather than a use of any 3.12 feature: the current numpy and
scipy releases require 3.12, and the current networkx requires 3.11, so
covering 3.10 and 3.11 as well would mean three different pinned versions
of each — which is not pinning. Supporting one exact set per dependency
was the more valuable half of the trade.

## Run

```bash
uv run netplanner
# or: uv run python -m netplanner.main
```

### Quick tour

1. Click **Router** in the palette, click the canvas — `rtr1` appears
   with four gigabit ports.
2. Click **Switch**, place `sw1`.
3. Click **Copper / Ethernet** under Connections, click `rtr1`, pick
   `Gig0/0`, click `sw1`, pick `Gig0/1` — cabled.
4. Right-click `sw1` → *Edit properties…* → **Interfaces** tab to add a
   port and type `25` next to Gbps, set IPs, paste in real MAC
   addresses, or switch a port to **Trunk** mode and list its VLANs
   (e.g. `10,20,30`).
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
uv run pytest
```

The GUI tests need a Qt platform plugin; they set `QT_QPA_PLATFORM=offscreen`
themselves, so no display server is required.

To run what CI runs:

```bash
uv sync --locked --extra dev                           # exactly what CI installs
uv run ruff check .                                    # lint (incl. bandit rules)
uv run mypy netplanner                                 # types
uv run pytest --cov=netplanner --cov-fail-under=100    # tests + coverage gate
uv run python .github/scripts/startup_smoke.py         # launches the app, quits itself
```

Coverage is at 100% of lines **and branches**, and the gate enforces
both, so new code needs tests to merge. Branch coverage is the half that
catches an untested guard: a line gate is satisfied the first time an
`if` runs, whichever way it went.

Nothing is excluded — there is no `# pragma: no cover` anywhere in the
package. A pragma is a claim that a line cannot run, and it is worth as
much as the reasoning behind it, which is to say it should be a test or
a deletion instead. Warnings are errors (`filterwarnings = ["error"]`), which
is how a deprecation in PyQt6 or SQLAlchemy announces itself on the weekly
scheduled run rather than on launch day.

## CI/CD

`.github/workflows/ci.yml` runs on every push and pull request, weekly on
a schedule, and is called by the release workflow:

| Job | What it guards |
| --- | --- |
| Lint | ruff, reported as inline annotations on the diff |
| Type check | mypy, blocking — zero errors on `netplanner/` |
| Tests | Python 3.12–3.14, gated at 100% line and branch coverage |
| Startup smoke | launches the real entry point; catches import errors a green suite misses |
| Dependency audit | pip-audit against the dependencies exported from `uv.lock` |
| Package | `uv build`, installs the wheel clean, checks the console script |

Every job installs with `uv sync --locked`, so a lockfile that disagrees
with `pyproject.toml` fails the run rather than resolving around it.

Tagging `vX.Y.Z` runs the same pipeline, verifies the tag matches the
version in `pyproject.toml`, and publishes a GitHub release with the
distributions and their checksums.

## Docker

Two images, for two jobs. Neither replaces installing NetPlanner
normally — a desktop application is better served by a distribution
package than by a container.

### Running the gates

The useful one. Reproduces CI exactly, with no display and nothing
installed on the host:

```bash
docker compose run --build --rm ci
```

That runs `ruff check .`, `mypy netplanner`, and the test suite with the
100% coverage gate — the same three commands the pipeline runs, so a
green container means a green pipeline.

**`--build` is not optional after you change anything.** `docker compose
run` builds only when the image is missing, not when the sources it was
built from have moved, so without it you get a silent rerun of the last
image with no indication that is what happened.

### Running the GUI

Works, with a caveat worth reading first.

```bash
xhost +SI:localuser:$USER          # grant your X server to your own account
docker compose up netplanner
xhost -SI:localuser:$USER          # revoke when finished
```

**The `xhost` line is the whole security question.** X11 has no
meaningful isolation between clients: anything that can reach your X
server can read your keystrokes and screenshot other windows. Granting
it to a local account is much narrower than the `xhost +local:` you will
find in most tutorials, which grants it to everything on the machine —
but it is still a real grant, and it is why this is not the recommended
way to run the application.

On Wayland this goes through XWayland, which works but adds a layer.
There is no native Wayland path here; Qt would need the compositor
socket forwarded and the isolation story is different again.

Plans and logs live in a named volume (`netplanner-data`) mounted at
`/data`, so they survive the container.

The container is hardened as far as the application allows: non-root
(uid 1000), read-only root filesystem, all capabilities dropped,
`no-new-privileges`, and no network at all — NetPlanner has no listener
and makes no outbound request. A startup warning about `XDG_RUNTIME_DIR`
is expected; Qt creates its own scratch directory under the `/tmp` tmpfs.

### Building directly

```bash
docker build --target runtime -t netplanner .
docker build --target ci      -t netplanner-ci .
```

Both base images are pinned by digest, so a moved tag cannot change what
gets built. Dependabot's `docker` ecosystem moves those digests weekly;
a pin nobody updates is a frozen copy of whatever CVEs it shipped with.

The build is multi-stage: `uv sync --locked` installs into `/opt/venv` in
a builder stage, and only that virtualenv crosses into the runtime image.
No uv, no compilers, no test tree, no apt lists. Dependencies install in
their own layer keyed on `uv.lock` alone, so editing application code
does not re-resolve anything.

`.dockerignore` is an allowlist — everything excluded, build inputs named
back in. A denylist silently ships whatever it has not heard of yet.

## Security

Full policy, threat model and known gaps: [SECURITY.md](SECURITY.md).
Report vulnerabilities through the
[security advisory form](https://github.com/christopherwc/netplanner/security/advisories/new),
not a public issue.

The short version of why any of this applies to a diagramming tool: a
plan stores attached device configs verbatim, and a running-config
contains enable secrets, community strings and PSKs — so the database is
a credential store whether or not it was meant to be one. And `.netplan`
files are made to be mailed to colleagues, so the loader is handling
untrusted input even though nothing about the workflow feels like it.

So:

- **Plans and logs are owner-only** (`0700` directories, `0600` files).
  The default umask would leave both readable by every account on the
  machine. A filesystem that cannot honour a mode logs a warning and
  carries on rather than refusing to start.
- **Project files are bounded on load** — 64 MiB, and nesting too deep
  for the parser is reported as a malformed file rather than crashing
  the app. Attached configs are capped at 16 MiB.
- **Exports don't carry your filesystem.** A config remembers where it
  was imported from; that path is kept locally and stripped from
  `.netplan` exports, because `/home/you/clients/acme-bank/core-sw.cfg`
  describes your client list rather than the network you documented.

**Exporting a project asks first.** A `.netplan` carries attached configs
verbatim — that is the point of it — so the export names the devices
holding one and warns before writing. The question comes before the file
dialog, so declining costs nothing.

None of it encrypts anything. NetPlanner holds no secrets of its own; the
exposure is whatever is in the configs you attach, and a running-config
routinely carries enable secrets and community strings. If a config is
too sensitive to sit in plaintext under your home directory, don't attach
it.

### Pinning actions by digest

One gap worth closing when convenient. Workflows reference actions by
tag (`actions/checkout@v4`), and a tag can be moved. Pinning by commit
SHA makes that immutable, and Dependabot still updates SHA pins as long
as the version stays in a trailing comment:

```bash
gh api repos/actions/checkout/git/ref/tags/v4 --jq '.object.sha'
# then: uses: actions/checkout@<sha>  # v4
```

Worth doing for every entry in `.github/workflows/` and
`.github/actions/setup/action.yml`.

## Project files

- Database: `~/.local/share/netplanner/plans.db` (respects `XDG_DATA_HOME`)
- JSON projects: any path you choose, `.netplan` extension by convention
  (File → Export project… / Open project…)
- `uv.lock`: the resolved dependency set with hashes — committed, and
  updated together with `pyproject.toml`
