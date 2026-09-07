"""Tests for shared link/label geometry (netplanner/export/geometry.py).

lift_above_line and label_anchor are exercised indirectly by the export
and canvas rendering tests, but those only ever pass y-down (scene)
coordinates. The y_up=True path -- used by pdf_exporter.py, whose
coordinates have already been flipped into reportlab's y-up space
before reaching here -- needs its own direct tests, since it is easy
for a coincidental link orientation in a rendering test to never
exercise the "wrong way" branch at all.
"""

from __future__ import annotations

from netplanner.export.geometry import label_anchor, lift_above_line


# --------------------------------------------------------------- y_up=False
def test_lift_above_line_default_moves_toward_smaller_y():
    """y-down convention: "toward the top of the page" means a smaller y,
    regardless of which perpendicular direction the raw cross product
    happens to point."""
    # Horizontal line; the naive perpendicular already points up (py<0).
    _, y = lift_above_line(5, 5, 0, 5, 10, 5, 2, y_up=False)
    assert y < 5
    # Horizontal line, endpoints reversed; the naive perpendicular now
    # points down (py>0) and must be flipped.
    _, y = lift_above_line(5, 5, 10, 5, 0, 5, 2, y_up=False)
    assert y < 5


def test_lift_above_line_zero_length_default_subtracts_amount():
    x, y = lift_above_line(3, 3, 3, 3, 3, 3, 4, y_up=False)
    assert (x, y) == (3, -1)


# ---------------------------------------------------------------- y_up=True
def test_lift_above_line_y_up_moves_toward_larger_y():
    """y-up convention (reportlab, after pdf_exporter's fy() flip):
    "toward the top of the page" means a *larger* y."""
    _, y = lift_above_line(5, 5, 0, 5, 10, 5, 2, y_up=True)
    assert y > 5
    _, y = lift_above_line(5, 5, 10, 5, 0, 5, 2, y_up=True)
    assert y > 5


def test_lift_above_line_zero_length_y_up_adds_amount():
    x, y = lift_above_line(3, 3, 3, 3, 3, 3, 4, y_up=True)
    assert (x, y) == (3, 7)


def test_lift_above_line_y_up_and_default_pick_opposite_directions():
    """Regression: pdf_exporter used to call lift_above_line on
    already-flipped (y-up) coordinates without telling it, so it lifted
    labels toward the bottom of the page instead of the top."""
    assert lift_above_line(5, 5, 0, 5, 10, 5, 2, y_up=False) == (5, 3)
    assert lift_above_line(5, 5, 0, 5, 10, 5, 2, y_up=True) == (5, 7)


# ------------------------------------------------------------- label_anchor
def test_label_anchor_forwards_y_up_to_lift_above_line():
    common = {
        "cx": 0, "cy": 0, "tx": 10, "ty": 0,
        "half_w": 2, "half_h": 2, "text_w": 4, "text_h": 2, "lift": 3,
    }
    default = label_anchor(**common, y_up=False)
    flipped = label_anchor(**common, y_up=True)
    assert default[1] != flipped[1]


def test_label_anchor_without_lift_is_unaffected_by_y_up():
    common = {
        "cx": 0, "cy": 0, "tx": 10, "ty": 0,
        "half_w": 2, "half_h": 2, "text_w": 4, "text_h": 2,
    }
    assert label_anchor(**common, y_up=False) == label_anchor(**common, y_up=True)
