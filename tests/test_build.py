import numpy as np
import pytest

from gulliver_hoard.errors import Cancelled, GulliverError
from gulliver_hoard.mesh import solids as S
from gulliver_hoard.mesh.build import build
from gulliver_hoard.outputs import _check
from gulliver_hoard.params import ModelParams, suggest_tiles
from gulliver_hoard.sources.dem import Composite, DemGrid
from gulliver_hoard.sources.fake import FakeSources

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
    assert st["buildings"] > 100 and st["bridges"] == 1 and st["water_pieces"] >= 2
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
