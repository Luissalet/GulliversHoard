import time
from types import SimpleNamespace

import pytest
from shapely.geometry import box

from gulliver_hoard import printability as P
from gulliver_hoard.mesh import solids as S
from gulliver_hoard.mesh.build import build
from gulliver_hoard.params import ModelParams
from gulliver_hoard.sources.fake import FakeSources


def dumbbell(neck=0.3):
    return box(0, 0, 4, 4).union(box(4, 2 - neck / 2, 7, 2 + neck / 2)).union(box(7, 0, 11, 4))


def test_thin_regions_finds_the_neck():
    thin = P.thin_regions(dumbbell(), 0.8)
    assert not thin.is_empty
    assert thin.bounds[0] > 3.5 and thin.bounds[2] < 7.5            # only the neck, not the blobs
    assert thin.area == pytest.approx(0.3 * 3, rel=0.4)


def test_thin_regions_ignores_a_solid_square():
    assert P.thin_regions(box(0, 0, 5, 5), 0.8).is_empty


def test_thicken_makes_the_neck_survive():
    geom = dumbbell()
    assert len(S.polygons(geom.buffer(-0.39))) == 2                  # the neck is lost before
    out = P.thicken(geom, 0.8)
    assert out.is_valid
    assert len(S.polygons(out.buffer(-0.39))) == 1                   # and kept after
    assert P.thicken(box(0, 0, 5, 5), 0.8).equals(box(0, 0, 5, 5))


def test_analyse_on_a_real_build():
    p = ModelParams(lon=-3.70, lat=40.42, width_m=1600, height_m=1200, size_mm=100, resolution_mm=0.6, color="color")
    result = build(p, FakeSources(), name="Lilliput")
    t0 = time.monotonic()
    report = P.analyse(result)
    assert time.monotonic() - t0 < 20
    for key in ("nozzle_mm", "layer_mm", "score", "verdict", "parts", "buildings", "layers", "trees", "advice"):
        assert key in report
    assert 0 <= report["score"] <= 100
    assert report["verdict"] in ("ok", "review", "risky")
    assert report["nozzle_mm"] == 0.4 and report["layer_mm"] == 0.2
    assert set(report["parts"]) == set(result.parts)
    assert all(info["bodies"] >= 1 for info in report["parts"].values())
    assert isinstance(report["advice"], list) and report["advice"]
    assert set(report["layers"]) == {"roads", "rails", "paths", "green"}


def test_analyse_flags_thin_things():
    thin_building = box(0, 0, 0.5, 6)
    fake = SimpleNamespace(
        parts={"buildings": S.prism(thin_building, 0, 1)},
        footprints={"buildings": [thin_building, box(10, 10, 14, 14)], "roads": dumbbell(0.2), "trees": ([[1, 1], [2, 2]], [0.2, 1.5])},
        params=SimpleNamespace(color="color", min_road_mm=0.5, water_depth_mm=0.8, base_mm=4.0))
    report = P.analyse(fake)
    assert report["buildings"]["thin_walls"] == 1
    assert report["trees"]["thin_crowns"] == 1
    assert report["layers"]["roads"]["thin_area_mm2"] > 0
    assert report["score"] < 100
