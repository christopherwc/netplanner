# NetPlanner

A network planning tool for Linux with a PyQt6 GUI, SQLite persistence,
and PDF/PNG export.

## Architecture

- `netplanner/gui/` — PyQt6 presentation layer (main window, canvas, panels, dialogs)
- `netplanner/app/` — application layer (controller, undo/redo commands, validation)
- `netplanner/domain/` — core model (networkx graph, entities, layout engine)
- `netplanner/export/` — shared renderer plus PDF (reportlab) and PNG (Pillow/Qt) exporters
- `netplanner/persistence/` — SQLAlchemy repository, SQLite database, .netplan JSON files

## Setup

```bash
sudo apt install python3 python3-venv libgl1 libegl1
python3 -m venv venv && source venv/bin/activate
pip install -e ".[dev]"
```

## Run

```bash
netplanner
# or: python -m netplanner.main
```

## Test

```bash
pytest
```
