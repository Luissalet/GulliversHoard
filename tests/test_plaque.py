import pytest
from shapely.geometry import box

from gulliver_hoard.mesh import plaque as P
from gulliver_hoard.mesh import solids as S

MIN_STROKE = 0.4
TITLE = "Valencia, España"
SUB = "39.4699° N  0.3763° W   1:5.000"


def inside_margin(geom, w, h, margin=P.MARGIN_MM):
    return box(margin - 1e-6, margin - 1e-6, w - margin + 1e-6, h - margin + 1e-6).contains(geom)


def test_north_arrow_bounds_and_validity():
    g = P.north_arrow(10.0)
    assert g.is_valid and not g.is_empty
    minx, miny, maxx, maxy = g.bounds
    assert maxy - miny == pytest.approx(10.0, abs=0.6)
    assert (minx + maxx) / 2 == pytest.approx(0.0, abs=0.05) and (miny + maxy) / 2 == pytest.approx(0.0, abs=0.05)
    assert maxx - minx < 0.6 * (maxy - miny)             # slim
    # the arrowhead (the biggest part) points up: it is wider at the bottom than just below the tip
    head = min(S.polygons(g), key=lambda p: p.bounds[1])
    hx0, hy0, hx1, hy1 = head.bounds
    low = head.intersection(box(hx0, hy0, hx1, hy0 + 0.2 * (hy1 - hy0))).bounds
    top = head.intersection(box(hx0, hy1 - 0.2 * (hy1 - hy0), hx1, hy1)).bounds
    assert (low[2] - low[0]) > (top[2] - top[0])
    # the N sits above the arrow head
    letter = max(S.polygons(g), key=lambda p: p.bounds[1])
    assert letter is not head and letter.bounds[1] > head.bounds[3] - 0.5


def test_north_arrow_hollow_half_on_large_sizes():
    assert any(len(p.interiors) for p in S.polygons(P.north_arrow(40.0)))
    assert not any(len(p.interiors) for p in S.polygons(P.north_arrow(8.0)) if p.area > 5)   # tiny arrows stay solid


def test_scale_bar_picks_round_length_and_label():
    g, label = P.scale_bar(0.1, 80.0, 2.4)
    assert label == "500 m"
    minx, miny, maxx, maxy = g.bounds
    assert (minx, miny) == pytest.approx((0.0, 0.0), abs=1e-9)
    assert maxx == pytest.approx(50.0)
    assert maxy == pytest.approx(2.4)
    assert g.is_valid
    # four segments, alternate ones filled: the middle of segment 0 and 2 is solid, 1 and 3 is open
    for i, solid in enumerate([True, False, True, False]):
        assert g.contains(box(12.5 * i + 4, 1.0, 12.5 * i + 8, 1.4)) == solid


@pytest.mark.parametrize("scale,max_len,label", [(0.1, 30.0, "200 m"), (0.02, 80.0, "2 km"), (0.01, 90.0, "5 km"),
                                                  (0.5, 40.0, "50 m"), (1.0, 25.0, "20 m"), (0.001, 90.0, "50 km")])
def test_scale_bar_round_values(scale, max_len, label):
    g, got = P.scale_bar(scale, max_len, 2.0)
    assert got == label
    length = g.bounds[2]
    assert 0.35 * max_len <= length <= max_len + 1e-9


def test_scale_bar_small_ground_lengths():
    g, label = P.scale_bar(100.0, 50.0, 2.0)            # 100 mm per metre
    assert label == "50 cm"
    assert g.bounds[2] == pytest.approx(50.0)


def test_layout_valid_and_inside_strip():
    g = P.plaque_layout(180, 12, title=TITLE, subtitle=SUB, scale_mm_per_m=0.2)
    assert not g.is_empty and g.is_valid
    assert inside_margin(g, 180, 12)
    minx, miny, maxx, maxy = g.bounds
    assert minx == pytest.approx(P.MARGIN_MM, abs=0.4)       # the title starts at the left margin
    assert maxx > 170                                         # the arrow sits at the right end
    # the title takes the upper left and is large; the subtitle sits under it
    left = g.intersection(box(0, 0, 60, 12))
    assert left.bounds[3] - left.bounds[1] > 8.0


def test_layout_has_bar_and_arrow_when_room():
    full = P.plaque_layout(180, 12, title="Madrid", subtitle=SUB, scale_mm_per_m=0.2)
    no_bar = P.plaque_layout(180, 12, title="Madrid", subtitle=SUB, scale_mm_per_m=0.2, bar=False)
    no_arrow = P.plaque_layout(180, 12, title="Madrid", subtitle=SUB, scale_mm_per_m=0.2, arrow=False)
    assert full.area > no_bar.area and full.area > no_arrow.area
    assert no_arrow.bounds[2] <= full.bounds[2] + 1e-6


@pytest.mark.parametrize("w,h", [(40, 6), (30, 5), (60, 4), (20, 3), (100, 8)])
def test_layout_degrades_gracefully(w, h):
    g = P.plaque_layout(w, h, title=TITLE, subtitle=SUB, scale_mm_per_m=0.2)
    assert g.is_valid
    if not g.is_empty:
        assert inside_margin(g, w, h)


def test_layout_small_strip_keeps_title():
    g = P.plaque_layout(40, 6, title="Valencia", subtitle=SUB, scale_mm_per_m=0.2)
    assert not g.is_empty and inside_margin(g, 40, 6)


def test_layout_long_title_is_shortened_not_shrunk():
    g = P.plaque_layout(60, 10, title="A very long title that never fits in a narrow strip of the frame", subtitle="x",
                        scale_mm_per_m=0.2)
    assert inside_margin(g, 60, 10)
    assert g.bounds[3] - g.bounds[1] > 5.0


@pytest.mark.parametrize("w,h", [(180, 12), (100, 8), (40, 6)])
def test_layout_strokes_survive_min_stroke(w, h):
    g = P.plaque_layout(w, h, title="Valencia", subtitle="40.4168° N  3.7038° W  1:12.000", scale_mm_per_m=0.2,
                        min_stroke_mm=MIN_STROKE)
    eroded = g.buffer(-MIN_STROKE / 2 + 0.01)
    parts = S.polygons(g)
    assert parts
    alive = 0
    for p in parts:
        if not p.buffer(-MIN_STROKE / 2 + 0.01).is_empty:
            alive += 1
    assert alive >= 0.8 * len(parts)
    assert not eroded.is_empty
    # and the bulk of the ink is thick enough to print
    assert eroded.area > 0.2 * g.area


def test_layout_empty_when_nothing_fits():
    g = P.plaque_layout(5, 2, title="Valencia", subtitle=SUB, scale_mm_per_m=0.2)
    assert g.is_empty


def test_plaque_strip_rectangle_frame():
    model = box(0, 0, 200, 150)
    outer = model.buffer(10, join_style="mitre")
    rect, angle = P.plaque_strip(outer, model)
    assert angle == 0.0
    ring = outer.difference(model)
    assert ring.buffer(1e-6).contains(rect)
    minx, miny, maxx, maxy = rect.bounds
    assert maxx - minx == pytest.approx(218.8, abs=0.2)
    assert maxy - miny == pytest.approx(8.8, abs=0.2)
    assert miny >= -10 + P.EDGE_MM - 1e-6 and maxy <= -P.EDGE_MM + 1e-6      # in the south band


@pytest.mark.parametrize("side,angle,tall", [("north", 180.0, False), ("east", 90.0, True), ("west", -90.0, True)])
def test_plaque_strip_other_sides(side, angle, tall):
    model = box(0, 0, 200, 150)
    outer = model.buffer(10, join_style="mitre")
    rect, got = P.plaque_strip(outer, model, side)
    assert got == angle
    assert outer.difference(model).buffer(1e-6).contains(rect)
    minx, miny, maxx, maxy = rect.bounds
    long_side, short_side = (maxy - miny, maxx - minx) if tall else (maxx - minx, maxy - miny)
    assert short_side == pytest.approx(8.8, abs=0.2)
    assert long_side > 100


@pytest.mark.parametrize("shape", ["circle", "hexagon", "rounded"])
def test_plaque_strip_other_footprints(shape):
    model = S.outline(shape, 150, 150)
    outer = model.buffer(10, join_style="mitre")
    res = P.plaque_strip(outer, model)
    assert res is not None
    rect, angle = res
    assert angle == 0.0
    assert outer.difference(model).buffer(1e-3).contains(rect)
    assert rect.bounds[3] - rect.bounds[1] >= 2.0


def test_plaque_strip_none_when_frame_too_thin():
    model = box(0, 0, 100, 100)
    assert P.plaque_strip(model.buffer(1.5, join_style="mitre"), model) is None


def test_plaque_strip_rejects_unknown_side():
    model = box(0, 0, 10, 10)
    with pytest.raises(ValueError):
        P.plaque_strip(model.buffer(5), model, "up")


def test_place_moves_layout_into_strip():
    g = P.plaque_layout(180, 12, title="Madrid", subtitle=SUB, scale_mm_per_m=0.2)
    model = box(0, 0, 200, 150)
    outer = model.buffer(10, join_style="mitre")
    rect, angle = P.plaque_strip(outer, model)
    w, h = rect.bounds[2] - rect.bounds[0], rect.bounds[3] - rect.bounds[1]
    g2 = P.plaque_layout(w, h, title="Madrid", subtitle=SUB, scale_mm_per_m=0.2)
    placed = P.place(g2, rect)
    assert rect.contains(placed)
    assert outer.difference(model).contains(placed)
    assert g.is_valid


def test_place_rotated_for_east_side():
    model = box(0, 0, 200, 150)
    outer = model.buffer(10, join_style="mitre")
    rect, angle = P.plaque_strip(outer, model, "east")
    w, h = rect.bounds[3] - rect.bounds[1], rect.bounds[2] - rect.bounds[0]
    g = P.plaque_layout(w, h, title="Madrid", subtitle=SUB, scale_mm_per_m=0.2)
    placed = P.place(g, rect, angle, (w, h))
    assert rect.buffer(1e-6).contains(placed)
    # the text reads bottom to top: the arrow (right end of the layout) ends up at the top
    assert placed.bounds[3] > rect.bounds[3] - 3
