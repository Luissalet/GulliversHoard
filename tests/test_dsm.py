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
    assert dsm.provider_for((-60.0, -3.1, -59.9, -3.0)) is None                     # Amazon
    for p in dsm.PROVIDERS.values():
        assert p.attribution and p.native_m > 0 and p.name


def test_spanish_regions_come_before_the_national_model():
    keys = lambda b: [p.key for p in dsm.providers_for(b)]                         # noqa: E731
    assert keys((-1.65, 42.81, -1.64, 42.82)) == ["es_nav", "es"]                   # Pamplona: LiDAR 2 m of Navarra, then the IGN
    assert keys((-1.99, 43.31, -1.97, 43.32)) == ["es_gip", "es"]                   # San Sebastian
    assert keys((-3.81, 43.46, -3.80, 43.47)) == ["es_cant", "es"]                  # Santander
    for name, b in {"Bilbao": (-2.94, 43.26, -2.93, 43.27), "Barcelona": (2.18, 41.39, 2.19, 41.40),
                    "Sevilla": (-5.99, 37.38, -5.98, 37.39), "Madrid": (-3.71, 40.41, -3.69, 40.43),
                    "Valencia": (-0.38, 39.46, -0.37, 39.47), "Palma": (2.64, 39.56, 2.65, 39.57),
                    "Vigo": (-8.72, 42.23, -8.71, 42.24), "Santiago": (-8.55, 42.87, -8.54, 42.88)}.items():
        assert keys(b) == ["es"], name
    assert keys((-16.26, 28.46, -16.25, 28.47)) == []                               # Canarias: not verified, not offered
    assert keys((-9.15, 38.71, -9.13, 38.72)) == []                                 # Lisbon is not Spain
    es = dsm.PROVIDERS["es"]
    assert es.layer == "mds05" and es.ndsm_layer == "mdsn_e025" and es.kind == "wcs2"
    assert dsm.PROVIDERS["es_nav"].ndsm_layer.endswith("Alturas_2M") and dsm.PROVIDERS["es_nav"].axes == ("E", "N")


def test_keyed_provider_is_ignored_without_its_key_and_used_with_it():
    cph = (12.56, 55.67, 12.58, 55.69)                                              # Copenhagen
    assert dsm.providers_for(cph) == [] and dsm.provider_for(cph) is None
    assert dsm.providers_for(cph, {}) == [] and dsm.providers_for(cph, {"other": "x"}) == []
    assert dsm.providers_for(cph, {"dataforsyningen": ""}) == []                    # an empty key is no key
    assert [p.key for p in dsm.providers_for(cph, {"dataforsyningen": "abc"})] == ["dk"]
    assert dsm.provider_for(cph, {"dataforsyningen": "abc"}).needs_key == "dataforsyningen"
    assert dsm.provider_for((12.56, 55.67, 12.58, 55.69), {"dataforsyningen": "abc"}).layer == "dhm_overflade"
    assert all(p.needs_key == "" for k, p in dsm.PROVIDERS.items() if k != "dk")    # only Denmark needs one


def test_fetch_refuses_big_areas_and_unknown_places(tmp_path):
    f = Fetcher(tmp_path / "c", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(GulliverError) as e:
        dsm.fetch(f, (5.0, 52.0, 5.1, 52.1))                        # ~ 69 km²
    assert e.value.code == "too_large"
    with pytest.raises(GulliverError) as e:
        dsm.fetch(f, (-60.0, -3.1, -59.99, -3.09))                  # the Amazon
    assert e.value.code == "unavailable"
    with pytest.raises(GulliverError) as e:                         # Denmark without its token: nothing to ask
        dsm.fetch(f, (12.56, 55.67, 12.57, 55.68))
    assert e.value.code == "unavailable"
    with pytest.raises(GulliverError) as e:                         # asking for the keyed provider by name does not skip the key
        dsm.fetch(f, (12.56, 55.67, 12.57, 55.68), provider=dsm.PROVIDERS["dk"])
    assert e.value.code == "unavailable" and "key" in e.value.message


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


def fake_tile(request: httpx.Request, epsg: int, x_of, native: float, ground=10.0, top=60.0):
    """A GeoTIFF for a WCS 1.0.0 (BBOX) or 2.0.1 (SUBSET) request: ``ground`` metres with a 20 x 20 m block of ``top``
    metres in the middle of the requested area, in ``epsg`` metres."""
    from rasterio.io import MemoryFile
    from rasterio.transform import from_origin

    q = dict(request.url.params.multi_items())
    if "BBOX" in q:
        x0, y0, x1, y1 = (float(v) for v in q["BBOX"].split(","))
        w, h = int(q["WIDTH"]), int(q["HEIGHT"])
    else:
        (x0, x1), (y0, y1) = (tuple(float(t) for t in s[s.index("(") + 1:-1].split(","))
                              for _k, s in request.url.params.multi_items() if _k == "SUBSET")
        step = native / float(q.get("SCALEFACTOR", 1.0))
        w, h = int(round((x1 - x0) / step)), int(round((y1 - y0) / step))
    a = np.full((h, w), ground, dtype=np.float32)
    cx, cy = x_of
    xs = x0 + (np.arange(w) + 0.5) * (x1 - x0) / w
    ys = y1 - (np.arange(h) + 0.5) * (y1 - y0) / h
    X, Y = np.meshgrid(xs - cx, ys - cy)
    a[(abs(X) < 10) & (abs(Y) < 10)] = top
    with MemoryFile() as mem:
        with mem.open(driver="GTiff", width=w, height=h, count=1, dtype="float32", crs=f"EPSG:{epsg}",
                      transform=from_origin(x0, y1, (x1 - x0) / w, (y1 - y0) / h), nodata=-9999.0) as ds:
            ds.write(a, 1)
        return httpx.Response(200, content=mem.read(), headers={"content-type": "image/tiff"})


def test_denmark_with_a_token_asks_the_way_the_capabilities_say(tmp_path):
    from rasterio.warp import transform

    lon, lat = 12.5683, 55.6761
    cx, cy = (v[0] for v in transform("EPSG:4326", "EPSG:25832", [lon], [lat]))
    calls = []

    def handler(request):
        calls.append(request)
        q = dict(request.url.params.multi_items())
        if q.get("token") != "secret-token":
            return httpx.Response(403, text="User not authorized", headers={"content-type": "text/plain"})
        return fake_tile(request, 25832, (cx, cy), 0.4, ground=5.0, top=95.0)

    f = Fetcher(tmp_path / "c", transport=httpx.MockTransport(handler))
    proj = Projector(lon, lat)
    lo, hi = proj.inv(-120, -120), proj.inv(120, 120)
    bbox = (lo[0], lo[1], hi[0], hi[1])
    heights, bounds, attribution, is_ndsm = dsm.fetch(f, bbox, res_m=1.0, keys={"dataforsyningen": "secret-token"})
    q = dict(calls[0].url.params.multi_items())
    assert str(calls[0].url).startswith("https://api.dataforsyningen.dk/dhm_wcs_DAF?")
    assert (q["SERVICE"], q["VERSION"], q["REQUEST"], q["COVERAGE"], q["CRS"], q["token"]) == (
        "WCS", "1.0.0", "GetCoverage", "dhm_overflade", "EPSG:25832", "secret-token")
    assert not is_ndsm and "Dataforsyningen" in attribution and np.nanmax(heights) == pytest.approx(95.0, abs=0.5)
    (r,) = dsm.building_heights(heights, bounds, proj, [box(-10, -10, 10, 10)])
    assert r["roof_m"] == pytest.approx(90.0, abs=2.0)                              # 95 above the 5 m ground
    # the token never reaches the cache key or the stored grid name
    assert not any("secret-token" in str(p) for p in (tmp_path / "c").rglob("*"))
    # a refused token says so, and without a token nothing is even tried
    f2 = Fetcher(tmp_path / "c2", transport=httpx.MockTransport(handler))
    with pytest.raises(GulliverError) as e:
        dsm.fetch(f2, bbox, keys={"dataforsyningen": "wrong"})
    assert e.value.code == "source_failed" and "key" in e.value.message and "wrong" not in e.value.message
    n = len(calls)
    with pytest.raises(GulliverError):
        dsm.fetch(f2, bbox)
    assert len(calls) == n


def test_spanish_request_shapes():
    es, nav, gip, cant = (dsm.PROVIDERS[k] for k in ("es", "es_nav", "es_gip", "es_cant"))
    params, url = dsm._request(es, True, (500000.0, 4790000.0, 504500.0, 4794500.0), 2.5)
    assert url == "https://wcs-mds.idee.es/mds" and params["COVERAGEID"] == "mdsn_e025"
    assert params["SUBSET"] == ["x(500000.000,504500.000)", "y(4790000.000,4794500.000)"] and "SCALEFACTOR" not in params
    params, _ = dsm._request(es, False, (500000.0, 4790000.0, 504500.0, 4794500.0), 5.0)
    assert params["COVERAGEID"] == "mds05" and params["SCALEFACTOR"] == "0.5000"
    params, url = dsm._request(nav, False, (610900.0, 4741600.0, 611100.0, 4741800.0), 2.0)
    assert url == "https://idena.navarra.es/ogc/wcs" and params["COVERAGEID"] == "IDENA.WCS__ELEVAC_Ras_MDS_2M"
    assert params["SUBSET"] == ["E(610900.000,611100.000)", "N(4741600.000,4741800.000)"]
    params, _ = dsm._request(nav, True, (610900.0, 4741600.0, 611100.0, 4741800.0), 2.0)
    assert params["COVERAGEID"] == "IDENA.WCS__ELEVAC_Ras_Alturas_2M"
    params, _ = dsm._request(gip, False, (582340.0, 4796586.0, 582540.0, 4796786.0), 1.0)
    assert (params["VERSION"], params["FORMAT"], params["WIDTH"], params["HEIGHT"], params["CRS"]) == ("1.0.0", "GEOTIFFFLOAT32", 200, 200, "EPSG:25830")
    assert params["COVERAGE"].startswith("LIDAR 2017") and "token" not in params
    params, _ = dsm._request(cant, False, (434700.0, 4812350.0, 435000.0, 4812650.0), 1.0)
    assert (params["COVERAGE"], params["FORMAT"], params["WIDTH"]) == ("23", "GeoTIFF", 300)


def test_navarra_mock_end_to_end_prefers_the_regional_model(tmp_path):
    from rasterio.warp import transform

    lon, lat = -1.6443, 42.8125
    cx, cy = (v[0] for v in transform("EPSG:4326", "EPSG:25830", [lon], [lat]))
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        return fake_tile(request, 25830, (cx, cy), 2.0, ground=445.0, top=495.0)

    f = Fetcher(tmp_path / "c", transport=httpx.MockTransport(handler))
    proj = Projector(lon, lat)
    lo, hi = proj.inv(-150, -150), proj.inv(150, 150)
    heights, bounds, attribution, is_ndsm = dsm.fetch(f, (lo[0], lo[1], hi[0], hi[1]), res_m=1.0)
    assert set(hosts) == {"idena.navarra.es"} and "Navarra" in attribution and not is_ndsm
    (r,) = dsm.building_heights(heights, bounds, proj, [box(-9, -9, 9, 9)])
    assert r["roof_m"] == pytest.approx(50.0, abs=2.0)


def test_known_heights_survive_the_surface_model_and_still_get_towers():
    from gulliver_hoard.mesh.build import _apply_dsm
    from gulliver_hoard.sources.osm import Building, Features
    h, bounds, proj = synthetic()
    foot = box(20, -20, 60, 20)
    for known, estimated, want in ((45.0, False, 45.0),     # the laser far below the cadastre (older than it): kept
                                   (18.0, False, 20.0),     # cadastre and laser agree: the laser's own height
                                   (9.0, False, 20.0),      # a block grown since the cadastre: the laser wins
                                   (9.0, True, 20.0)):      # a guess: the laser wins
        feats = Features()
        feats.buildings = [Building(foot, known, 0.0, "yes", estimated=estimated)]
        _apply_dsm(feats, h, bounds, proj, False)
        b = feats.buildings[0]
        assert b.height_m == pytest.approx(want, abs=1.0) and not b.estimated
        assert any(q.part and q.kind == "tower" for q in feats.buildings[1:])


def test_roof_fit_tells_the_shape_and_slope():
    from shapely.geometry import box as sbox
    rng = np.random.default_rng(4)
    xs, ys = np.meshgrid(np.arange(0.25, 20, 0.5), np.arange(0.25, 10, 0.5))
    x, y = xs.ravel(), ys.ravel()
    poly = sbox(0, 0, 20, 10)                                            # 20 x 10 m, long side along x
    noise = rng.normal(0, 0.12, x.shape)
    gabled = 7.0 + 0.6 * (5.0 - np.abs(y - 5.0)) + noise                 # ridge along the building, 10 m high
    f = dsm.roof_fit(x, y, gabled, poly)
    assert f["shape"] == "gabled" and not f["across"] and abs(f["ridge_m"] - 10.0) < 0.5 and abs(f["eaves_m"] - 7.0) < 0.5
    across = 7.0 + 0.4 * (10.0 - np.abs(x - 10.0)) + noise              # ridge across it
    f = dsm.roof_fit(x, y, across, poly)
    assert f["shape"] == "gabled" and f["across"]
    hipped = 7.0 + 0.6 * np.minimum(5.0 - np.abs(y - 5.0), 10.0 - np.abs(x - 10.0)) + noise
    assert dsm.roof_fit(x, y, hipped, poly)["shape"] == "hipped"
    assert dsm.roof_fit(x, y, 9.0 + noise, poly) is None                 # flat
    clutter = 9.0 + rng.normal(0, 1.2, x.shape)                          # machinery everywhere: not a pitched roof
    assert dsm.roof_fit(x, y, clutter, poly) is None


def test_laser_trees_skip_buildings_and_flat_structures():
    proj = Projector(LON, LAT)
    res, size = 1.0, 200
    n = int(size / res)
    xs = (np.arange(n) + 0.5) * res - size / 2.0
    X, Y = np.meshgrid(xs, xs[::-1])
    ground = 3.0 + 0.02 * X                                                   # a gentle slope
    h = ground.copy()
    rng = np.random.default_rng(1)
    trees_at = [(-60, -50, 14.0), (-40, 30, 9.0), (50, -60, 18.0)]
    for tx, ty, th in trees_at:
        d = np.hypot(X - tx, Y - ty)
        crown = np.clip(th * (1 - (d / 4.0) ** 2), 0, None) + rng.uniform(0, 0.6, X.shape) * (d < 4)
        h = np.maximum(h, ground + crown)
    h[(X >= 0) & (X < 40) & (Y >= 20) & (Y < 32)] += 7.0                      # a flat bridge deck
    h[(X >= 20) & (X < 60) & (Y >= -20) & (Y < 0)] += 15.0                     # a building
    lo, hi = proj.inv(-size / 2.0, -size / 2.0), proj.inv(size / 2.0, size / 2.0)
    found = dsm.trees(h.astype(np.float32), (lo[0], lo[1], hi[0], hi[1]), proj, (-90, -90, 90, 90),
                      buildings=box(20, -20, 60, 0))
    assert len(found) == 3
    for tx, ty, th in trees_at:
        best = min(found, key=lambda t: (t[0] - tx) ** 2 + (t[1] - ty) ** 2)
        assert abs(best[0] - tx) <= 2.5 and abs(best[1] - ty) <= 2.5 and abs(best[2] - th) < 3.0 and best[3] >= 3.0


def test_roof_tiers_drop_the_machinery_and_keep_the_levels():
    from rasterio.transform import Affine
    from shapely.geometry import box as sbox
    cell = 1.0
    yy, xx = np.mgrid[0:60, 0:60]
    own = (xx >= 5) & (xx < 55) & (yy >= 5) & (yy < 55)
    poly = sbox(5, 5, 55, 55)                                            # metres: x right, y up from the bottom row
    T = Affine(cell, 0.0, 0.0, 0.0, -cell, 60.0)
    rng = np.random.default_rng(0)
    roof = np.full(own.shape, 12.0) + rng.normal(0, 0.15, own.shape)
    for cx, cy in ((15, 15), (40, 20), (25, 45)):                       # air units 2 m wide, 2.5 m tall
        roof[cy:cy + 2, cx:cx + 2] += 2.5
    rel = np.where(own, roof, 0.3).astype(np.float32)
    assert dsm.roof_tiers(rel, own, cell, 12.3, T, poly) is None        # one level once the machinery is gone
    tiered = roof.copy()
    tiered[5:30, 5:55] += 9.0                                            # a block 9 m taller over the north half
    rel = np.where(own, tiered, 0.3).astype(np.float32)
    tiers = dsm.roof_tiers(rel, own, cell, 21.0, T, poly)
    assert len(tiers) == 2 and abs(tiers[0][0] - 12.0) < 0.6 and abs(tiers[1][0] - 21.0) < 0.6
    top = tiers[1][1]
    assert abs(top.area - 50 * 25) < 200 and top.bounds[1] >= 28 and len(top.exterior.coords) <= 8   # straight walls


def test_tiered_building_is_one_solid():
    from shapely.geometry import box as sbox
    from gulliver_hoard.mesh.build import _tiered
    shape = sbox(0, 0, 50, 50)
    tiers = [(12.0, sbox(0, 0, 50, 50)), (21.0, sbox(0, 25, 50, 50))]
    solid = _tiered(shape, tiers, 1.0, 2.0, 0.2, lambda g: g.buffer(0) if g is not None else None, 0.5)
    bb = solid.bounding_box()
    assert abs(bb[5] - (2.0 + 21.0 * 0.2)) < 1e-6 and solid.volume() > 0


def test_laser_tells_conifers_by_their_pointed_crowns():
    proj = Projector(LON, LAT)
    n = 120
    xs = np.arange(n) + 0.5 - n / 2.0
    X, Y = np.meshgrid(xs, xs[::-1])
    h = np.full(X.shape, 2.0)
    d1 = np.hypot(X + 25, Y)                                                  # a fir: a cone 16 m tall, 6 m wide
    h = np.maximum(h, 2.0 + np.clip(16.0 * (1 - d1 / 3.0), 0, None))
    d2 = np.hypot(X - 25, Y)                                                  # an oak: a dome 14 m tall, 10 m wide
    h = np.maximum(h, 2.0 + np.where(d2 < 5.0, 14.0 * np.sqrt(np.clip(1 - (d2 / 5.0) ** 2, 0, 1)), 0.0))
    h += np.random.default_rng(5).uniform(0, 0.5, h.shape) * (np.minimum(d1, d2) < 5)
    lo, hi = proj.inv(-n / 2.0, -n / 2.0), proj.inv(n / 2.0, n / 2.0)
    found = dsm.trees(h.astype(np.float32), (lo[0], lo[1], hi[0], hi[1]), proj, (-55, -55, 55, 55))
    fir = min(found, key=lambda t: (t[0] + 25) ** 2 + t[1] ** 2)
    oak = min(found, key=lambda t: (t[0] - 25) ** 2 + t[1] ** 2)
    assert fir[4] == 1 and oak[4] == 0


def test_laser_measures_crown_widths():
    proj = Projector(LON, LAT)
    n = 120
    xs = np.arange(n) + 0.5 - n / 2.0
    X, Y = np.meshgrid(xs, xs[::-1])
    h = np.full(X.shape, 1.0)
    for cx, r in ((-25, 3.0), (25, 8.0)):                                     # a slim tree and a broad one, both 12 m
        d = np.hypot(X - cx, Y)
        h = np.maximum(h, 1.0 + np.where(d < r, 12.0 * np.sqrt(np.clip(1 - (d / r) ** 2, 0, 1)), 0.0))
    lo, hi = proj.inv(-n / 2.0, -n / 2.0), proj.inv(n / 2.0, n / 2.0)
    found = dsm.trees(h.astype(np.float32), (lo[0], lo[1], hi[0], hi[1]), proj, (-55, -55, 55, 55), min_rough_m=0.0)
    slim = min(found, key=lambda t: (t[0] + 25) ** 2 + t[1] ** 2)
    broad = min(found, key=lambda t: (t[0] - 25) ** 2 + t[1] ** 2)
    assert slim[3] < 8.0 and broad[3] > 11.0
