import time
from dataclasses import replace

import httpx
import numpy as np
import pytest
from shapely.geometry import box

from gulliver_hoard.errors import GulliverError
from gulliver_hoard.geo import Projector
from gulliver_hoard.sources import dsm
from gulliver_hoard.sources.http import Fetcher

LON, LAT = 5.1213, 52.0908            # Utrecht, the Dom


def synthetic(res=1.0, size=200, ground=3.0):
    """A DSM on a lon/lat grid around (LON, LAT): ground at 3 m, a box building 20 m tall at x 20..60, y -20..20 metres
    from the middle, and a 50 m tower on it (x 40..50, y -5..5)."""
    proj = Projector(LON, LAT)
    n = int(size / res)
    xs = (np.arange(n) + 0.5) * res - size / 2.0
    X, Y = np.meshgrid(xs, xs[::-1])
    h = np.full((n, n), ground, dtype=np.float32)
    h[(X >= 20) & (X < 60) & (Y >= -20) & (Y < 20)] = ground + 20.0
    h[(X >= 40) & (X < 50) & (Y >= -5) & (Y < 5)] = ground + 50.0
    lo = proj.inv(-size / 2.0, -size / 2.0)
    hi = proj.inv(size / 2.0, size / 2.0)
    return h, (lo[0], lo[1], hi[0], hi[1]), proj


def test_roof_top_and_tower_from_a_dsm():
    h, bounds, proj = synthetic()
    foot = box(20, -20, 60, 20)
    far = box(-80, -30, -50, 0)                                     # bare ground: roof ~ 0
    res = dsm.building_heights(h, bounds, proj, [foot, far], is_ndsm=False)
    a, b = res
    assert a["roof_m"] == pytest.approx(20.0, abs=1.0)
    assert a["top_m"] == pytest.approx(50.0, abs=1.0)
    assert len(a["towers"]) == 1
    poly, top = a["towers"][0]
    assert top == pytest.approx(50.0, abs=1.0)
    assert poly.area == pytest.approx(100.0, rel=0.35) and foot.contains(poly.buffer(-0.01))
    assert b["roof_m"] == pytest.approx(0.0, abs=0.5) and b["towers"] == []


def test_a_spike_is_ignored_and_a_thin_spire_is_not():
    h, bounds, proj = synthetic()
    n = h.shape[0]
    h[n // 2 + 10, n // 2 + 30] = 90.0                               # one noisy cell on the roof (x 30, y -10)
    foot = box(20, -20, 60, 20)
    (a,) = dsm.building_heights(h, bounds, proj, [foot])
    assert a["top_m"] == pytest.approx(50.0, abs=1.0)
    h2, _b, _p = synthetic()
    h2[n // 2 - 3:n // 2 + 3, n // 2 + 44:n // 2 + 47] = 3.0 + 80.0     # a 3 x 6 m spire, 80 m tall
    (b,) = dsm.building_heights(h2, bounds, proj, [foot])
    assert b["top_m"] == pytest.approx(80.0, abs=1.0)


def test_ndsm_path_needs_no_ground():
    h, bounds, proj = synthetic()
    nd = np.where(h > 3.5, h - 3.0, 0.0).astype(np.float32)
    foot = box(20, -20, 60, 20)
    (a,) = dsm.building_heights(nd, bounds, proj, [foot], is_ndsm=True)
    assert a["roof_m"] == pytest.approx(20.0, abs=1.0) and a["top_m"] == pytest.approx(50.0, abs=1.0)
    assert len(a["towers"]) == 1


def test_ground_ring_ignores_neighbouring_buildings():
    h, bounds, proj = synthetic()
    near = box(20, -20, 60, 20)
    neighbour = box(62, -20, 80, 20)                                # 2 m away, also standing on the ground
    h2 = h.copy()
    n = h.shape[0]
    xs = (np.arange(n) + 0.5) - 100.0
    X, Y = np.meshgrid(xs, xs[::-1])
    h2[(X >= 62) & (X < 80) & (Y >= -20) & (Y < 20)] = 33.0
    res = dsm.building_heights(h2, bounds, proj, [near, neighbour], is_ndsm=False)
    assert res[0]["roof_m"] == pytest.approx(20.0, abs=1.0)          # the 33 m neighbour did not become the "ground"
    assert res[1]["roof_m"] == pytest.approx(30.0, abs=1.5)


def test_no_data_gives_none():
    h, bounds, proj = synthetic()
    h[:] = np.nan
    assert dsm.building_heights(h, bounds, proj, [box(20, -20, 60, 20)]) == [None]
    h, bounds, proj = synthetic()
    assert dsm.building_heights(h, bounds, proj, [box(500, 500, 520, 520)]) == [None]    # outside the model


def test_many_footprints_are_quick():
    proj = Projector(LON, LAT)
    n = 2000
    h = np.full((n, n), 3.0, dtype=np.float32)
    shapes = []
    for i in range(80):
        for j in range(80):
            x0, y0 = -900 + i * 22, -900 + j * 22
            shapes.append(box(x0, y0, x0 + 14, y0 + 14))
            r, c = int(n / 2 - (y0 + 7)), int(n / 2 + (x0 + 7))
            h[r - 7:r + 7, c - 7:c + 7] = 3.0 + 6.0 + (i + j) % 7
    lo, hi = proj.inv(-1000, -1000), proj.inv(1000, 1000)
    t = time.time()
    res = dsm.building_heights(h, (lo[0], lo[1], hi[0], hi[1]), proj, shapes)
    assert time.time() - t < 15.0 and len(res) == 6400 and all(r is not None for r in res)
    assert res[0]["roof_m"] == pytest.approx(6.0, abs=0.7)


def test_providers_by_place():
    assert dsm.provider_for((5.10, 52.08, 5.14, 52.10)).key == "nl"                 # Utrecht
    assert dsm.provider_for((2.34, 48.85, 2.36, 48.86)).key == "fr"                 # Paris
    assert dsm.provider_for((7.44, 46.94, 7.46, 46.96)).key == "ch"                 # Bern
    assert dsm.provider_for((-0.12, 51.50, -0.10, 51.51)).key == "uk"               # London
    assert dsm.provider_for((6.95, 50.94, 6.97, 50.95)).key == "nrw"               # Cologne
    assert dsm.provider_for((10.70, 59.90, 10.72, 59.91)).key == "no"               # Oslo
    assert dsm.provider_for((21.00, 52.23, 21.02, 52.24)).key == "pl"               # Warsaw
    assert dsm.provider_for((3.72, 51.05, 3.74, 51.06)).key == "fl"                 # Ghent
    assert dsm.provider_for((-3.71, 40.41, -3.69, 40.43)) is None                   # Madrid
    assert dsm.provider_for((-60.0, -3.1, -59.9, -3.0)) is None                     # Amazon
    for p in dsm.PROVIDERS.values():
        assert p.attribution and p.native_m > 0 and p.name


def test_fetch_refuses_big_areas_and_unknown_places(tmp_path):
    f = Fetcher(tmp_path / "c", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(GulliverError) as e:
        dsm.fetch(f, (5.0, 52.0, 5.1, 52.1))                        # ~ 69 km²
    assert e.value.code == "too_large"
    with pytest.raises(GulliverError) as e:
        dsm.fetch(f, (-3.71, 40.41, -3.69, 40.43))
    assert e.value.code == "unavailable"


def test_multipart_and_aaigrid():
    asc = b"ncols 2\nnrows 2\nxllcorner 10\nyllcorner 20\ncellsize 5\nNODATA_value -9999\n1 2\n3 -9999\n"
    body = b"\r\n--wcs\nContent-Type: text/xml\n\n<gml/>\n--wcs\nContent-Type: image/x-aaigrid\n\n" + asc + b"\n--wcs--\n"
    arr, tr = dsm.parse_aaigrid(dsm.multipart_part(body, b"image/x-aaigrid"))
    assert arr.shape == (2, 2) and arr[0, 1] == 2 and np.isnan(arr[1, 1])
    assert (tr.a, tr.c, tr.f) == (5, 10, 30)                        # north-up, top edge at 20 + 2 * 5


def nl_tile(request: httpx.Request) -> httpx.Response:
    """A fake AHN4 WCS: ground 3 m, a 20 m building and a 50 m tower, in Dutch RD metres around the Dom."""
    from rasterio.io import MemoryFile
    from rasterio.transform import from_origin
    from rasterio.warp import transform

    q = dict(request.url.params.multi_items())
    sub = [v for k, v in request.url.params.multi_items() if k == "SUBSET"]
    (x0, x1), (y0, y1) = (tuple(float(t) for t in s[s.index("(") + 1:-1].split(",")) for s in sub)
    step = 0.5 / float(q.get("SCALEFACTOR", 1.0))
    w, h = int(round((x1 - x0) / step)), int(round((y1 - y0) / step))
    cx, cy = transform("EPSG:4326", "EPSG:28992", [LON], [LAT])
    cx, cy = cx[0], cy[0]
    xs = x0 + (np.arange(w) + 0.5) * step - cx
    ys = y1 - (np.arange(h) + 0.5) * step - cy
    X, Y = np.meshgrid(xs, ys)
    a = np.full((h, w), 3.0, dtype=np.float32)
    a[(X >= 20) & (X < 60) & (Y >= -20) & (Y < 20)] = 23.0
    a[(X >= 40) & (X < 50) & (Y >= -5) & (Y < 5)] = 53.0
    a[X < -150] = 3.4028235e38                                       # nodata
    with MemoryFile() as mem:
        with mem.open(driver="GTiff", width=w, height=h, count=1, dtype="float32", crs="EPSG:28992",
                      transform=from_origin(x0, y1, step, step), nodata=3.4028234663852886e+38) as ds:
            ds.write(a, 1)
        return httpx.Response(200, content=mem.read(), headers={"content-type": "image/tiff"})


def test_fetch_tiles_mosaic_cache_and_heights(tmp_path):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return nl_tile(request)

    f = Fetcher(tmp_path / "c", transport=httpx.MockTransport(handler))
    p = replace(dsm.PROVIDERS["nl"], tile_px=250)                    # small tiles: the 400 m area needs several
    proj = Projector(LON, LAT)
    lo, hi = proj.inv(-200, -200), proj.inv(200, 200)
    bbox = (lo[0], lo[1], hi[0], hi[1])
    seen = []
    heights, bounds, attribution, is_ndsm = dsm.fetch(f, bbox, res_m=1.0, provider=p, progress=seen.append)
    assert len(calls) > 1 and len(seen) == len(calls) and "SCALEFACTOR=0.5" in calls[0]
    assert heights.dtype == np.float32 and heights.shape[0] > 350 and not is_ndsm
    assert "AHN4" in attribution and bounds[0] <= bbox[0] and bounds[3] >= bbox[3] - 1e-9
    assert np.isnan(heights).any()                                                  # nodata became NaN
    n = len(calls)
    again = dsm.fetch(f, bbox, res_m=1.0, provider=p)
    assert len(calls) == n and np.array_equal(again[0], heights, equal_nan=True)       # second time from the disk cache
    (r,) = dsm.building_heights(heights, bounds, proj, [box(20, -20, 60, 20)])
    assert r["roof_m"] == pytest.approx(20.0, abs=1.5) and r["top_m"] == pytest.approx(50.0, abs=1.5)
    assert len(r["towers"]) == 1


def test_fetch_falls_back_to_the_next_provider(tmp_path):
    def handler(request):
        if "geopf.fr" in str(request.url):
            return httpx.Response(200, content=b"<ServiceExceptionReport/>", headers={"content-type": "text/xml"})
        return httpx.Response(404)

    f = Fetcher(tmp_path / "c", transport=httpx.MockTransport(handler))
    with pytest.raises(GulliverError) as e:
        dsm.fetch(f, (2.349, 48.852, 2.351, 48.854))
    assert e.value.code == "source_failed"
