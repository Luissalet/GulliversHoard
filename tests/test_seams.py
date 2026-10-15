"""Tests for street-following tile seams (no network)."""
from __future__ import annotations

import itertools
import time

import numpy as np
import pytest
import shapely
from shapely.geometry import LineString, Point, box

from gulliver_hoard.mesh.seams import pin_points, seam_lines, street_tiles
from gulliver_hoard.mesh.solids import tile_boxes

BOUNDS = (0.0, 0.0, 100.0, 60.0)


def _block_scene():
    """Buildings straddling the straight seam at x=50 and a 3 mm street centred at x=56."""
    buildings = shapely.union_all([box(44, y, 52, y + 10) for y in (8, 22, 36)] + [box(44, 50, 52, 54)])
    roads = box(54.5, 0, 57.5, 60)
    return buildings, roads


def _cut_area(poly, buildings):
    """Area of each building lying outside the tile that holds most of it."""
    worst = 0.0
    for b in shapely.get_parts(buildings):
        inside = max(poly_i.intersection(b).area for poly_i in poly)
        worst = max(worst, b.area - inside)
    return worst


def test_without_buildings_or_roads_tiles_are_straight():
    for empty in (None, shapely.Polygon()):
        tiles = street_tiles(BOUNDS, 2, 2, empty, empty)
        ref = tile_boxes(BOUNDS, 2, 2)
        assert [t["box"] for t in tiles] == [r["box"] for r in ref]
        for t, r in zip(tiles, ref):
            assert t["poly"].equals(box(*r["box"]))


def test_seam_follows_street_and_keeps_buildings_whole():
    buildings, roads = _block_scene()
    straight = street_tiles(BOUNDS, 2, 1, None, None)
    assert _cut_area([t["poly"] for t in straight], buildings) > 1.0  # straight cut does split buildings

    tiles = street_tiles(BOUNDS, 2, 1, buildings, roads)
    polys = [t["poly"] for t in tiles]
    assert _cut_area(polys, buildings) < 1e-6
    (seam,) = seam_lines(tiles)
    xs = np.asarray(seam.coords)[:, 0]
    assert abs(seam.coords[0][0] - 50.0) < 1e-9 and abs(seam.coords[-1][0] - 50.0) < 1e-9
    assert seam.coords[0][1] == 0.0 and seam.coords[-1][1] == 60.0
    mid = [x for x, y in seam.coords if 15 < y < 45]
    assert all(54.0 <= x <= 58.0 for x in mid)  # runs along the street
    assert xs.max() - 50.0 <= 15.0  # inside the band (min(15, 0.18 * 50) = 9 -> 59)
    assert tiles[0]["poly"].bounds[0] == 0.0 and tiles[1]["poly"].bounds[2] == 100.0


def test_horizontal_seam_follows_street():
    buildings = shapely.union_all([box(x, 24, x + 10, 33) for x in (10, 30, 50, 70)])
    roads = LineString([(0, 36.5), (100, 36.5)])
    tiles = street_tiles((0, 0, 100, 60), 1, 2, buildings, roads)
    assert _cut_area([t["poly"] for t in tiles], buildings) < 1e-6
    assert tiles[0]["id"] == "A1" and tiles[0]["poly"].centroid.y > tiles[1]["poly"].centroid.y


@pytest.mark.parametrize("nx,ny", [(2, 1), (1, 2), (2, 2), (3, 2)])
def test_partition_property(nx, ny):
    rng = np.random.default_rng(nx * 10 + ny)
    squares = [box(x, y, x + 3, y + 3) for x, y in rng.uniform(5, 90, size=(60, 2))]
    roads = shapely.union_all([LineString([(0, y), (100, y + 2)]) for y in (15, 30, 45)] +
                              [LineString([(x, 0), (x + 2, 60)]) for x in (25, 50, 75)])
    tiles = street_tiles(BOUNDS, nx, ny, shapely.union_all(squares), roads)
    assert len(tiles) == nx * ny
    area = box(*BOUNDS).area
    assert sum(t["poly"].area for t in tiles) == pytest.approx(area, abs=1e-6)
    assert shapely.union_all([t["poly"] for t in tiles]).symmetric_difference(box(*BOUNDS)).area < 1e-6
    for a, b in itertools.combinations(tiles, 2):
        assert a["poly"].intersection(b["poly"]).area < 1e-6
    for t in tiles:
        assert t["poly"].geom_type == "Polygon" and t["box"] == t["poly"].bounds


@pytest.mark.parametrize("nx,ny", [(1, 1), (2, 1), (1, 3), (3, 2)])
def test_ids_rows_cols_match_tile_boxes(nx, ny):
    buildings, roads = _block_scene()
    tiles = street_tiles(BOUNDS, nx, ny, buildings, roads)
    ref = tile_boxes(BOUNDS, nx, ny)
    assert [(t["id"], t["row"], t["col"]) for t in tiles] == [(r["id"], r["row"], r["col"]) for r in ref]
    for t, r in zip(tiles, ref):  # each tile stays near its straight box
        assert t["poly"].centroid.distance(box(*r["box"]).centroid) < 15.0


def test_pin_points_lie_on_seam_and_avoid_buildings():
    seam = LineString([(50, 0), (50, 60), (52, 140)])
    pts = pin_points(seam, spacing_mm=45.0, margin_mm=6.0)
    assert len(pts) >= 3
    for x, y, ang in pts:
        assert seam.distance(Point(x, y)) < 1e-9
        assert 6.0 - 1e-9 <= seam.project(Point(x, y)) <= seam.length - 6.0 + 1e-9
    assert pts[0][2] == pytest.approx(90.0)

    avoid = box(45, 15, 55, 35)
    slid = pin_points(seam, spacing_mm=45.0, avoid=avoid)
    assert slid
    for x, y, _ in slid:
        assert not avoid.contains(Point(x, y)) and seam.distance(Point(x, y)) < 1e-9


def test_pin_points_short_seam_gets_one_pin_and_horizontal_angle():
    pts = pin_points(LineString([(0, 5), (20, 5)]), spacing_mm=45.0, margin_mm=6.0)
    assert len(pts) == 1 and pts[0][0] == pytest.approx(10.0) and pts[0][2] == pytest.approx(0.0)
    assert pin_points(LineString([(0, 5), (4, 5)]), margin_mm=6.0)  # shorter than the margins still gets one


def test_dense_city_runs_fast():
    rng = np.random.default_rng(7)
    pts = rng.uniform(2, 296, size=(5000, 2))
    buildings = shapely.union_all([box(x, y, x + 2.5, y + 2.5) for x, y in pts])
    roads = shapely.union_all([LineString([(0, v), (300, v)]) for v in range(20, 300, 40)] +
                              [LineString([(v, 0), (v, 300)]) for v in range(25, 300, 40)])
    t0 = time.perf_counter()
    tiles = street_tiles((0, 0, 300, 300), 3, 3, buildings, roads)
    assert time.perf_counter() - t0 < 10.0
    assert len(tiles) == 9
    assert sum(t["poly"].area for t in tiles) == pytest.approx(300 * 300, abs=1e-6)
