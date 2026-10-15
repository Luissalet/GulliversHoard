import json

import httpx
import numpy as np
import pytest
from rasterio.transform import from_origin
from shapely.geometry import Polygon

from gulliver_hoard.errors import GulliverError
from gulliver_hoard.sources import openbuildings as ob
from gulliver_hoard.sources.http import Fetcher


def raster(size=120):
    """Presence and height arrays at 1 m (north-up, origin at (0, size)): a 10 x 16 m house of 6 m, an L-shaped block of 9 m,
    two houses sharing a wall (4 m and 7 m), a 2 x 3 m shed (too small), and a house without a height."""
    pres = np.zeros((size, size), dtype=np.float32)
    hei = np.zeros((size, size), dtype=np.float32)

    def put(x0, y0, x1, y1, h, p=1.0):                      # metres east/north of the origin
        pres[size - y1:size - y0, x0:x1] = p
        hei[size - y1:size - y0, x0:x1] = h

    put(10, 10, 26, 20, 6.0)                                 # rectangle
    put(50, 10, 70, 18, 9.0)                                 # L: two arms
    put(62, 18, 70, 40, 9.0)
    put(10, 50, 22, 62, 4.0)                                 # two houses with a thin, softer wall between them
    put(23, 50, 35, 62, 7.0)
    pres[size - 62:size - 50, 22:23] = 0.3
    put(80, 80, 82, 83, 3.0)                                 # shed
    put(90, 50, 100, 60, 0.0)                                # a building the model gave no height to
    return pres, hei


def test_footprints_heights_and_squaring():
    pres, hei = raster()
    items = ob.footprints_from_arrays(pres, hei, from_origin(0, 120, 1, 1))
    polys = [(Polygon(i["outer"]), i["height"]) for i in items]
    assert len(items) == 5                                   # shed dropped; the two houses are two
    by_height = {h: p for p, h in polys}
    house = by_height[6.0]
    assert house.area == pytest.approx(160.0, rel=0.12)
    assert house.area / house.minimum_rotated_rectangle.area == pytest.approx(1.0, abs=0.02)       # squared
    assert len(house.exterior.coords) == 5                   # four corners
    ell = by_height[9.0]
    assert ell.area / ell.minimum_rotated_rectangle.area < 0.8 and len(ell.exterior.coords) > 5      # the L is left alone
    assert by_height[4.0].area == pytest.approx(144.0, rel=0.2) and by_height[7.0].area == pytest.approx(144.0, rel=0.2)
    assert not by_height[4.0].intersection(by_height[7.0]).area > 5.0
    assert sorted(h for _p, h in polys if h is None) == [None]       # the one without a height


def test_nothing_where_there_is_no_building():
    z = np.zeros((50, 50), dtype=np.float32)
    assert ob.footprints_from_arrays(z, z, from_origin(0, 50, 1, 1)) == []
    pres = z.copy()
    pres[10:12, 10:12] = 1.0                                 # 4 m²
    assert ob.footprints_from_arrays(pres, pres * 3, from_origin(0, 50, 1, 1)) == []


def test_keep_drops_the_neighbours_blobs():
    pres, hei = raster()
    items = ob.footprints_from_arrays(pres, hei, from_origin(0, 120, 1, 1), keep=lambda x, y: x < 40)
    assert len(items) == 3 and all(Polygon(i["outer"]).centroid.x < 40 for i in items)


def test_covers():
    def around(lon, lat):
        return (lon - 0.007, lat - 0.006, lon + 0.007, lat + 0.006)
    assert ob.covers(around(-60.9405, -2.6235))              # Novo Airão
    assert ob.covers(around(3.38, 6.52))                     # Lagos
    assert ob.covers(around(72.88, 19.07))                   # Mumbai
    assert ob.covers(around(106.85, -6.2))                   # Jakarta
    assert ob.covers(around(-74.07, 4.71))                   # Bogotá
    assert ob.covers(around(-99.13, 19.43))                  # Mexico City
    assert ob.covers(around(-66.1, 18.45))                   # San Juan
    assert not ob.covers(around(-3.70, 40.42))               # Madrid
    assert not ob.covers(around(2.35, 48.85))                # Paris
    assert not ob.covers(around(5.12, 52.09))                # Utrecht
    assert not ob.covers(around(151.2, -33.87))              # Sydney
    assert not ob.covers(around(-122.4, 37.8))               # San Francisco


MANIFEST_NAMES = [f"v1/manifests/93_EPSG_32720_{y}_06_30.json" for y in (2022, 2023)] + ["v1/manifests/95_EPSG_32720_2023_06_30.json",
                                                                                       "v1/manifests/01_EPSG_32723_2023_06_30.json"]


def manifest(year, tiles):
    return {"uriPrefix": "gs://open-buildings-temporal-data/v1/geotiffs/9",
            "tilesets": [{"id": "a0", "sources": [
                {"uris": [f"abcd_{year}_06_30/tile_{k}.tif"], "affineTransform": {"scaleX": 0.5, "translateX": x, "scaleY": -0.5, "translateY": y},
                 "dimensions": {"width": 25000, "height": 25000}} for k, (x, y) in tiles.items()]}]}


def handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "/storage/v1/b/" in url:
        return httpx.Response(200, json={"items": [{"name": n} for n in MANIFEST_NAMES]}, headers={"content-type": "application/json"})
    if "93_EPSG_32720_2023" in url:
        return httpx.Response(200, json=manifest(2023, {"A": (722556.0, 9710804.0), "B": (735056.0, 9710804.0)}),
                              headers={"content-type": "application/json"})
    if "EPSG_32720_2022" in url or "95_EPSG" in url:
        return httpx.Response(200, json=manifest(2022, {"Z": (0.0, 1000.0)}), headers={"content-type": "application/json"})
    return httpx.Response(404)


def test_tiles_for_picks_the_newest_year_and_the_tiles_over_the_area(tmp_path):
    f = Fetcher(tmp_path / "c", transport=httpx.MockTransport(handler))
    epsg, year, tiles = ob.tiles_for(f, (-60.9480, -2.6295, -60.9330, -2.6175))
    assert (epsg, year) == (32720, 2023) and len(tiles) == 1
    assert tiles[0]["url"] == "https://storage.googleapis.com/open-buildings-temporal-data/v1/geotiffs/9abcd_2023_06_30/tile_A.tif"
    assert (tiles[0]["x0"], tiles[0]["y1"], tiles[0]["x1"]) == (722556.0, 9710804.0, 735056.0)
    n = f.requests
    ob.tiles_for(f, (-60.9480, -2.6295, -60.9330, -2.6175))
    assert f.requests == n                                    # the listing and the manifests come from the disk cache
    assert ob.tiles_for(f, (-59.0, -2.6, -58.99, -2.59))[1] == 0       # no tile over it


def test_fetch_end_to_end_with_a_synthetic_block(tmp_path, monkeypatch):
    f = Fetcher(tmp_path / "c", transport=httpx.MockTransport(handler))
    bbox = (-60.9480, -2.6295, -60.9330, -2.6175)
    seen = []

    def fake_block(tiles, crs, x0, y0, x1, y1):
        w, h = int(round(x1 - x0)), int(round(y1 - y0))
        pres = np.zeros((h, w), dtype=np.float32)
        hei = np.zeros((h, w), dtype=np.float32)
        r, c = h // 2, w // 2                                 # a 12 x 10 m house, 5.5 m tall, in the middle of the block
        pres[r - 5:r + 5, c - 6:c + 6] = 1.0
        hei[r - 5:r + 5, c - 6:c + 6] = 5.5
        seen.append((crs, x1 - x0))
        return pres, hei

    monkeypatch.setattr(ob, "_read_block", fake_block)
    msgs = []
    items = ob.fetch(f, bbox, progress=msgs.append)
    assert seen and seen[0][0] == "EPSG:32720" and msgs
    assert len(items) >= 1
    for it in items:
        assert it["height"] == pytest.approx(5.5)
        ring = np.asarray(it["outer"])
        assert bbox[0] <= ring[:, 0].mean() <= bbox[2] and bbox[1] <= ring[:, 1].mean() <= bbox[3]
        assert tuple(ring[0]) == tuple(ring[-1])
    n = f.requests
    again = ob.fetch(f, bbox)
    assert again == items and f.requests == n and len(seen) == len(seen)         # from the cache, nothing read


def test_fetch_refuses_big_areas_and_returns_nothing_without_tiles(tmp_path):
    f = Fetcher(tmp_path / "c", transport=httpx.MockTransport(handler))
    with pytest.raises(GulliverError) as e:
        ob.fetch(f, (-61.0, -2.7, -60.9, -2.6))               # ~ 120 km²
    assert e.value.code == "too_large"
    assert ob.fetch(f, (-59.0, -2.6, -58.99, -2.59)) == []
    json.dumps(ob.ATTRIBUTION)
    assert "Google" in ob.ATTRIBUTION
