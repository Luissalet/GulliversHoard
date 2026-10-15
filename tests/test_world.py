"""The worldwide sources: canopy heights, land cover and building footprints, and a build that uses all three."""

import numpy as np
from shapely.geometry import Polygon

from gulliver_hoard.geo import Projector
from gulliver_hoard.mesh.build import build
from gulliver_hoard.outputs import _check
from gulliver_hoard.params import ModelParams
from gulliver_hoard.sources import canopy, landcover, msbuildings
from gulliver_hoard.sources.fake import FakeSources
from gulliver_hoard.sources.osm import Building

from conftest import DEMO


def test_quadkeys_and_tiles():
    assert canopy.quadkey(3, 5, 3) == "213"
    x, y = canopy._tile_xy(-3.70, 40.42)
    assert len(canopy.quadkey(x, y)) == 9
    assert landcover.tile_name(-3.70, 40.42) == "N39W006"
    assert landcover.tile_name(-60.02, -3.10) == "S06W063"


def chm_with_trees(bbox, centres_m, height=18.0, res=1.2):
    """A canopy raster over ``bbox`` with round crowns ``centres_m`` (metres east/north of the south-west corner)."""
    (mx0, mx1), (my0, my1) = canopy.merc(np.array([bbox[0], bbox[2]]), np.array([bbox[1], bbox[3]]))
    w, h = int((mx1 - mx0) / res), int((my1 - my0) / res)
    out = np.zeros((h, w), dtype=np.uint8)
    yy, xx = np.mgrid[0:h, 0:w]
    stretch = 1.0 / np.cos(np.radians((bbox[1] + bbox[3]) / 2))      # mercator metres per ground metre
    for cx, cy in centres_m:
        px, py = cx * stretch / res, h - cy * stretch / res
        d = np.hypot(xx - px, yy - py) * res / stretch
        out = np.maximum(out, np.clip(height * (1 - (d / 5.0) ** 2), 0, None).astype(np.uint8))
    return out, (mx0, my0, mx0 + w * res, my0 + h * res)


def test_canopy_peaks_are_trees_with_their_height():
    lon0, lat0 = -60.02, -3.10
    bbox = (lon0, lat0, lon0 + 0.002, lat0 + 0.002)
    centres = [(40, 40), (120, 60), (60, 160), (170, 170)]
    chm, bounds = chm_with_trees(bbox, centres)
    proj = Projector(lon0 + 0.001, lat0 + 0.001)
    found = canopy.trees(chm, bounds, proj)
    assert len(found) == len(centres)
    for _x, _y, h, crown in found:
        assert 12 <= h <= 18.5 and 3.0 <= crown <= 16.0
    # a model coarser than the crowns keeps them apart
    assert len(canopy.trees(chm, bounds, proj, min_spacing_m=300)) == 1
    area = canopy.canopy_area(chm, bounds, proj, min_height_m=3.0, min_area_m2=10.0)
    assert area is not None and 4 * 30 < area.area < 4 * 120


def test_landcover_classes_become_smooth_polygons():
    lon0, lat0 = 9.0, 0.5
    n = 120                                                        # 120 px of 10 m = 1.2 km
    classes = np.full((n, n), landcover.GRASS, dtype=np.uint8)
    classes[10:60, 10:60] = landcover.TREES                         # a wood 50 px across
    classes[80:100, 80:110] = landcover.WATER                       # a lake of 20 x 30 px
    bounds = (lon0, lat0, lon0 + n * landcover.DEG, lat0 + n * landcover.DEG)
    proj = Projector(lon0 + n * landcover.DEG / 2, lat0 + n * landcover.DEG / 2)
    woods = landcover.area(classes, bounds, proj, landcover.GROUPS["woods"])
    lake = landcover.area(classes, bounds, proj, landcover.GROUPS["water"])
    px = landcover.DEG * 111320.0 * np.cos(np.radians(lat0))           # about 9.3 m at this latitude
    assert abs(woods.area - (50 * px) ** 2) / (50 * px) ** 2 < 0.06
    assert abs(lake.area - 600 * px * px) / (600 * px * px) < 0.1
    assert landcover.area(classes, bounds, proj, landcover.GROUPS["snow"]) is None


def test_footprints_fill_the_gaps_and_lend_heights():
    sq = lambda x, y, s=10: Polygon([(x, y), (x + s, y), (x + s, y + s), (x, y + s)])  # noqa: E731
    osm = [Building(sq(0, 0), 9.0, 0.0, "yes", estimated=True), Building(sq(50, 0), 12.0, 0.0, "yes")]
    extra = [Building(sq(1, 1), 15.0, 0.0, "yes"),                  # over the first: lends its height
             Building(sq(100, 100), 5.5, 0.0, "house", estimated=True)]   # where the map has nothing
    out, added, heights = msbuildings.fill(osm, extra)
    assert added == 1 and heights == 1 and len(out) == 3
    assert out[0].height_m == 15.0 and not out[0].estimated
    proj = Projector(-60.0, -3.0)
    lon, lat = proj.inv(np.array([0, 12, 12, 0, 0]), np.array([0, 0, 9, 9, 0]))
    b = msbuildings.to_buildings([{"outer": list(zip(lon, lat)), "height": None}], proj, sq(-500, -500, 1000))
    assert len(b) == 1 and b[0].kind == "house" and b[0].estimated and abs(b[0].shape.area - 108) < 1


class WorldSources(FakeSources):
    """The invented town plus a canopy, a land cover and footprints laid around it."""

    def canopy(self, bbox, *, progress=None):
        w = (bbox[2] - bbox[0]) * 111320 * np.cos(np.radians((bbox[1] + bbox[3]) / 2))
        h = (bbox[3] - bbox[1]) * 111320
        rng = np.random.default_rng(3)
        centres = [(x, y) for x, y in zip(rng.uniform(0.05, 0.3, 40) * w, rng.uniform(0.6, 0.95, 40) * h)]
        return chm_with_trees(bbox, centres, res=2.4)

    def landcover(self, bbox, *, progress=None):
        res = landcover.DEG * 2
        w, h = int((bbox[2] - bbox[0]) / res), int((bbox[3] - bbox[1]) / res)
        classes = np.full((h, w), landcover.BUILT, dtype=np.uint8)
        classes[: h // 4, :] = landcover.GRASS                       # the north strip is countryside
        classes[: h // 6, w // 2: w // 2 + w // 6] = landcover.TREES   # with a wood in it
        return classes, (bbox[0], bbox[3] - h * res, bbox[0] + w * res, bbox[3])

    def footprints(self, bbox, *, progress=None):
        lon0, lat1 = bbox[0], bbox[3]
        out = []
        for i in range(12):
            x, y = lon0 + 0.0015 + i * 0.0003, lat1 - 0.0015
            out.append({"outer": [(x, y), (x + 0.00015, y), (x + 0.00015, y + 0.0001), (x, y + 0.0001), (x, y)],
                        "height": 6.0 if i % 2 else None})
        return out


def test_a_build_with_the_worldwide_sources_stays_closed():
    p = ModelParams(**{**DEMO, "size_mm": 120, "resolution_mm": 0.5, "color": "color", "tree_size_mm": 0.8,
                       "width_m": 1600, "height_m": 1200})
    res = build(p, WorldSources())
    st = res.stats
    assert "canopy" in st["trees_from"] and st["trees"] > 20
    assert st["footprints_added"] >= 6 and "footprints" in st["building_source"]
    assert "woods" in st["landcover"] and "green" in st["landcover"]
    assert any("WorldCover" in a for a in res.attribution) and any("Meta" in a for a in res.attribution)
    for name, part in res.parts.items():
        assert _check(part)["watertight"], name
