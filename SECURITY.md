# Security policy

## Reporting a vulnerability

Report privately through GitHub's
[security advisory form](https://github.com/christopherwc/netplanner/security/advisories/new)
rather than opening a public issue.

Please include what you did, what happened, and what you expected. A
`.netplan` file or config that reproduces it is the most useful thing you
can attach — strip any real secrets from it first.

This is a personal project with one maintainer, so there is no response
SLA. Expect an acknowledgement within a week or so.

## What NetPlanner is, for threat-modelling purposes

A single-user desktop application. It has no network listener, no server,
no authentication, and no multi-user model. It runs with the privileges
of the person who started it and can read and write whatever they can.

That shape rules a lot of things out, and it rules two things firmly in.

**It holds secrets.** Configs attached to a device are stored verbatim
inside the plan, and a real running-config contains enable secrets, SNMP
community strings, wireless PSKs and RADIUS keys. The database is
therefore a credential store whether or not it was meant to be one.

**It opens files from other people.** A `.netplan` file is designed to be
mailed to a colleague. Anything that parses one is handling untrusted
input, even though nothing about the workflow feels like it.

## What the code does about that

**Data at rest is owner-only.** The data directory and `plans.db` are
created `0700`/`0600`, as are the log directory and `netplanner.log`. The
default umask would leave both readable by every account on the machine.
The directory mode carries most of the weight: SQLite writes journal and
WAL files alongside the database under its own umask, and a private
directory covers them. A filesystem that cannot honour a mode — a FAT
stick, some network mounts — logs a warning and continues, so the weaker
posture is visible rather than silently assumed.

**Loading a project file is bounded.** A `.netplan` is capped at 64 MiB
and read only after its size is checked, since the whole file is held in
memory before parsing. Nesting deep enough to exhaust the interpreter
stack is reported as a malformed file rather than crashing the app.
Attached configs are capped at 16 MiB on import.

**Exports do not carry your filesystem.** A config remembers where it was
imported from, which is useful locally and wrong in a file you send
someone: `/home/you/clients/acme-bank/core-sw.cfg` describes your
directory layout and your client list, not the network you documented.
The path is kept in the local database and stripped from `.netplan`
exports. The config content itself is preserved.

**Configs are displayed, never interpreted.** The viewer uses
`setPlainText` and a `QSyntaxHighlighter` over fixed patterns, so config
content is never treated as markup or as anything executable.

**No dynamic execution anywhere.** No `eval`, `exec`, `pickle`,
`subprocess`, or shell invocation. Database access is through the
SQLAlchemy ORM, so plan and device names are parameterised rather than
interpolated. Identifiers come from `uuid4`.

## Supply chain

Every dependency is pinned to an exact version, and `uv.lock` records the
SHA-256 of every artifact including transitive ones. CI installs with
`uv sync --locked`, which fails if the lock and `pyproject.toml`
disagree. Dependabot opens a grouped PR weekly so the pins move
deliberately — pinning without that is worse than not pinning, because
stale pins miss security fixes. `pip-audit` runs against the exported
lock on every push and weekly on a schedule, and CodeQL scans the source.

GitHub Actions run with `contents: read`; only the release job is granted
`contents: write`. Checkout runs with `persist-credentials: false`, so
the workflow token is not left in `.git/config` where a later step could
read it. Tag names reach the shell through the environment rather than
`${{ }}` interpolation, which would otherwise let a tag named
`v1.0.0$(...)` execute during a release.

## Known gaps

These are real and unfixed. They are listed because a security document
that only lists wins is not worth reading.

- **Secrets are stored in plaintext.** The database is owner-only, not
  encrypted. Anyone with your account, your backups, or your disk has the
  configs. If that matters for a given config, do not attach it.
- **Actions are pinned by tag, not by digest.** `actions/checkout@v4`
  resolves through a mutable tag, so a compromised or moved tag would be
  picked up silently. Pinning by full commit SHA is the fix; see the
  developer documentation in `README.md`.
- **`.netplan` files are not authenticated.** There is no signature, so
  opening one means trusting whoever sent it to the same degree you trust
  any file they send you. The parser is bounded, which limits what a
  malformed file can cost, but it is not a sandbox.
