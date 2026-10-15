import numpy as np
import pytest

from gulliver_hoard.errors import Cancelled, GulliverError
from gulliver_hoard.mesh import solids as S
from gulliver_hoard.mesh.build import build
from gulliver_hoard.outputs import _check
from gulliver_hoard.params import ModelParams, suggest_tiles
from gulliver_hoard.sources.dem import Composite, DemGrid
from gulliver_hoard.sources.fake import FakeSources
from shapely.geometry import box

from conftest import DEMO


def make(**kw):
    base = {**DEMO, "size_mm": 120, "resolution_mm": 0.5}
    base.update(kw)
    return ModelParams(**base)


def closed(m):
    return _check(m)["watertight"]


@pytest.mark.parametrize("kw", [
    {"color": "mono"},
    {"color": "color"},
    {"color": "color", "roads": "raised", "contours_m": -1},
    {"color": "mono", "shape": "circle", "tiles_x": 2, "tiles_y": 2},
    {"color": "color", "shape": "hexagon", "frame_mm": 4, "tiles_x": 2, "tiles_y": 1},
    {"color": "color", "shape": "rounded", "roads": "flat", "label": "Prueba\nsegunda línea"},
])
def test_every_part_and_the_single_piece_are_closed(kw):
    r = build(make(**kw), FakeSources(), name="Lilliput")
    assert r.parts and all(closed(m) for m in r.parts.values()), {n: _check(m) for n, m in r.parts.items()}
    assert closed(r.single) and len(r.single.decompose()) == 1
    for t in r.tiles:
        assert all(closed(m) for m in t["parts"].values())
        assert t["single"] is None or closed(t["single"])
    if kw.get("color") == "color":
        assert {"terrain", "buildings", "water"} <= set(r.parts)


def test_colour_parts_do_not_overlap_and_add_up_to_the_single_piece():
    r = build(make(color="color"), FakeSources(), name="L")
    # the water inlay fills the cut the single piece keeps empty: everything else adds up to it
    total = sum(m.volume() for n, m in r.parts.items() if n != "water")
    assert total == pytest.approx(r.single.volume(), rel=1e-3)
    terrain, water = r.parts["terrain"], r.parts["water"]
    assert S.intersect(terrain, water).volume() < 0.05


def test_scale_size_and_stats():
    p = make()
    r = build(p, FakeSources(), name="L")
    st = r.stats
    assert st["scale"] == "1:20.000" and st["scale_denominator"] == pytest.approx(20000)
    assert st["size_mm"][0] == pytest.approx(120, abs=0.2) and st["size_mm"][1] == pytest.approx(90, abs=0.2)
    assert st["buildings"] > 100 and st["bridges"] == 2 and st["water_pieces"] >= 2
    assert 0 < st["weight_g_pla"] < st["volume_cm3"] * 1.24
    assert r.single.bounding_box()[2] == pytest.approx(0, abs=1e-6)      # sits on z = 0


def test_tiles_pins_and_bed():
    r = build(make(size_mm=300, tiles_x=2, tiles_y=2), FakeSources(), name="L")
    assert [t["id"] for t in r.tiles] == ["A1", "A2", "B1", "B2"]
    assert r.stats["pins"] >= 4
    for t in r.tiles:
        bb = t["single"].bounding_box() if t["single"] is not None else S.union(list(t["parts"].values())).bounding_box()
        assert bb[3] - bb[0] <= 151 and bb[4] - bb[1] <= 113
    # the pin holes really are holes: the tiles add up to less than the whole
    tiles_volume = sum(t["single"].volume() for t in r.tiles)
    assert tiles_volume < r.single.volume() + 1e-3
    assert suggest_tiles(300, 225, 256, 256) == (2, 1)
    assert suggest_tiles(100, 100, 256, 256) == (1, 1)


def test_thin_base_drops_pins_with_a_note():
    r = build(make(size_mm=300, tiles_x=2, base_mm=1.8, water_depth_mm=0.4), FakeSources(), name="L")
    assert r.stats["pins"] == 0 and any("pin" in n.lower() for n in r.notes)


def test_label_is_engraved_under_the_model():
    plain = build(make(label_bottom=False, color="mono", buildings=False), FakeSources(), name="L")
    labelled = build(make(label_bottom=True, color="mono", buildings=False), FakeSources(), name="Lilliput")
    assert labelled.single.volume() < plain.single.volume() - 1.0


def test_sea_from_elevation():
    class Coast(FakeSources):
        def dem(self, bbox, want_res_m, **_):
            lon_min, lat_min, lon_max, lat_max = bbox
            cols, rows = 200, 200
            lons = np.linspace(lon_min, lon_max, cols)
            data = np.tile(np.where(lons < (lon_min + lon_max) / 2, -20.0, 30.0), (rows, 1)).astype(np.float32)
            step = ((lon_max - lon_min) / (cols - 1), (lat_max - lat_min) / (rows - 1))
            return Composite(DemGrid(data, "lonlat", (lon_min, lat_max), step, source="coast", res_m=10), None)

        def osm(self, bbox, **_):
            return {"elements": []}

    r = build(make(color="color", buildings=False), Coast(), name="Costa")
    assert r.stats["sea"] and r.parts["water"].volume() > 100
    assert closed(r.single)


def test_limits_and_cancel():
    with pytest.raises(GulliverError) as err:
        build(make(size_mm=1500, resolution_mm=0.15), FakeSources())
    assert err.value.code == "too_large" and "resolution_mm" in err.value.hint
    with pytest.raises(Cancelled):
        build(make(), FakeSources(), cancelled=lambda: True)


def test_presets_and_road_ranks():
    city = make()
    assert city.effective_road_rank() == 4
    assert ModelParams(**{**DEMO, "size_mm": 100, "width_m": 30000, "height_m": 30000}).effective_road_rank() == 1
    assert make(road_detail="all").effective_road_rank() == 6 and make(roads="none").effective_road_rank() == 0
    assert make(contours_m=-1).contour_interval(230) == 20
    c = ModelParams(**{**DEMO, "shape": "circle"})
    assert c.height_m == c.width_m


def test_command_line_builds_from_a_box(tmp_path, monkeypatch):
    from gulliver_hoard import cli
    monkeypatch.setattr(cli, "Sources", lambda fetcher: FakeSources())
    code = cli.main(["--bbox=-3.715,40.41,-3.685,40.43", "--size", "90", "--tiles", "2x1", "--mono", "--out", str(tmp_path / "out"),
                     "--cache", str(tmp_path / "cache"), "--name", "Lilliput"])
    assert code == 0
    files = {p.name for p in (tmp_path / "out").rglob("*")}
    assert {"lilliput.stl", "lilliput.3mf", "lilliput-tiles.zip", "report.json", "preview.glb"} <= files


def test_roads_and_coast_follow_their_real_outline_not_the_grid():
    """A diagonal road on flat ground and a diagonal coast: the groove and the shore must follow the vector shapes, not
    step along the terrain grid (0.5 mm here)."""
    from shapely.geometry import LineString as L, Polygon as P, box as B
    from gulliver_hoard.geo import Projector

    class Flat(FakeSources):
        def dem(self, bbox, want_res_m, **_):
            lon_min, lat_min, lon_max, lat_max = bbox
            cols = rows = 300
            lons = np.linspace(lon_min, lon_max, cols)
            lats = np.linspace(lat_max, lat_min, rows)
            LON, LAT = np.meshgrid(lons, lats)
            u = (LON - lon_min) / (lon_max - lon_min) + (LAT - lat_min) / (lat_max - lat_min)
            data = np.where(u < 0.35, -10.0, 10.0).astype(np.float32)     # sea in the south-west corner, diagonal coast
            step = ((lon_max - lon_min) / (cols - 1), (lat_max - lat_min) / (rows - 1))
            return Composite(DemGrid(data, "lonlat", (lon_min, lat_max), step, source="flat", res_m=10), None)

        def osm(self, bbox, **_):
            proj = Projector((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
            pts = [proj.inv(x, y) for x, y in [(-200.0, -300.0), (900.0, 700.0)]]
            return {"elements": [{"type": "way", "id": 1, "tags": {"highway": "primary"},
                                  "geometry": [{"lon": lo, "lat": la} for lo, la in pts]}]}

    p = make(color="mono", buildings=False, roads="engraved", smooth=0.0, contours_m=0)
    r = build(p, Flat(), name="Llano")
    assert closed(r.single)
    terrain = r.parts["terrain"]
    W, H = p.model_size_mm
    road = r.footprints["roads"].intersection(B(0, 0, W, H))
    base = float(r.Z.max())
    section = terrain.slice(base - p.road_depth_mm / 2.0)
    rings = [P(ring) for ring in section.to_polygons()]
    solid = rings[0]
    for ring in rings[1:]:
        solid = solid.symmetric_difference(ring)
    shore = r.footprints["water"].buffer(0.9)          # the ground ramps down to the sea over a DEM cell there
    groove = B(0, 0, W, H).difference(solid).difference(shore)
    road_land = road.difference(shore)
    assert groove.symmetric_difference(road_land).area < 0.03 * road_land.area
    assert groove.hausdorff_distance(road_land) < 0.15
    # the coast: a straight diagonal, traced well inside a grid cell
    coast = r.footprints["water"].boundary.difference(B(0, 0, W, H).exterior.buffer(0.01))
    assert coast.length > 10
    line = L([coast.coords[0], coast.coords[-1]]) if coast.geom_type == "LineString" else None
    if line is not None:
        assert coast.hausdorff_distance(line) < 0.15


def test_detail_layers_trees_roofs_spire_rails_and_paths():
    r = build(make(color="color", size_mm=240, resolution_mm=0.5), FakeSources(), name="Detalle")
    st = r.stats
    assert st["trees"] > 100 and "trees" in r.parts and closed(r.parts["trees"])
    assert st["roofs"] >= 2 and st["rails_drawn"] >= 2 and st["paths_drawn"] == 2
    assert {"roads", "rails", "green"} <= set(st["colour_layers"])
    bb = r.single.bounding_box()                      # nothing (a tree, a bridge) sticks out of the model
    W, H = r.params.model_size_mm
    assert bb[0] >= -0.01 and bb[1] >= -0.01 and bb[3] <= W + 0.01 and bb[4] <= H + 0.01
    assert closed(r.single) and all(closed(m) for m in r.parts.values())
    # the church tower carries a spire: it reaches the 44 m mark and narrows to a point
    k = r.params.scale_mm_per_m
    cx, cy = (-609 + 1200) * k, (150 + 900) * k
    around = S.intersect(r.parts["buildings"], S.prism(box(cx - 8 * k, cy - 8 * k, cx + 8 * k, cy + 8 * k), -1.0, 99.0))
    zmin, zmax = around.bounding_box()[2], around.bounding_box()[5]
    assert zmax - zmin > 40 * k
    assert around.slice(zmax - 0.05).area() < 0.05 * (12 * k) ** 2
    # the paths show the ground through the green layer
    green = r.parts["green"]
    path = r.footprints["paths"]
    assert path is not None and path.area > 0.3
    probe = S.prism(path.buffer(-0.05), -1.0, 99.0)
    assert S.intersect(green, probe).volume() < 0.01


def test_flat_roofs_no_trees_and_solid_bridges_switch_off_cleanly():
    r = build(make(color="mono", roofs="flat", trees=False, arches=False, paths=False, rails=False), FakeSources(), name="L")
    assert r.stats["trees"] == 0 and r.stats["roofs"] == 0 and r.stats["rails_drawn"] == 0
    assert closed(r.single)


def test_mountain_gets_snow_and_rock():
    class Alps(FakeSources):
        def dem(self, bbox, want_res_m, **_):
            lon_min, lat_min, lon_max, lat_max = bbox
            cols = rows = 260
            u = np.linspace(-1, 1, cols)
            U, V = np.meshgrid(u, u)
            peak = 2900.0 * np.exp(-(U ** 2 + V ** 2) / 0.18)
            cliff = 600.0 * np.clip((U - 0.35) * 8, 0, 1)                 # a wall of rock
            data = (900.0 + peak + cliff).astype(np.float32)
            step = ((lon_max - lon_min) / (cols - 1), (lat_max - lat_min) / (rows - 1))
            return Composite(DemGrid(data, "lonlat", (lon_min, lat_max), step, source="alps", res_m=25), None)

        def osm(self, bbox, **_):
            return {"elements": []}

    p = ModelParams(lon=7.0, lat=46.0, width_m=8000, height_m=8000, size_mm=120, resolution_mm=0.6,
                    buildings=False, roads="none", color="color", meadows=True, z_exag=3.0)
    r = build(p, Alps(), name="Pico")
    assert {"snow", "rock", "green"} <= set(r.stats["colour_layers"])
    assert r.stats["size_mm"][2] <= 0.3 * 120 + p.base_mm + 0.5          # the exaggeration was capped
    assert any("exaggeration lowered" in n for n in r.notes)
    assert closed(r.parts["snow"]) and closed(r.parts["rock"]) and closed(r.single)
