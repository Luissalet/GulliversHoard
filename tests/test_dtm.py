import io
import re

import httpx
import numpy as np
import pytest
import rasterio
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from rasterio.warp import transform_bounds

from gulliver_hoard.sources import dem, dtm
from gulliver_hoard.sources.http import Fetcher


def tiff(arr, transform, crs):
    with MemoryFile() as mem:
        with mem.open(driver="GTiff", width=arr.shape[1], height=arr.shape[0], count=1, dtype="float32",
                      crs=crs, transform=transform, nodata=-9999) as ds:
            ds.write(arr.astype("float32"), 1)
        return mem.read()


def test_real_terrain_layers_and_resolution_gate():
    assert dtm.candidates((5.11, 52.08, 5.12, 52.09), 2)[0].layer == "dtm_05m"
    assert "MNT" in dtm.candidates((2.33, 48.85, 2.34, 48.86), 2)[0].layer
    assert "MDT_2M" in dtm.candidates((-1.65, 42.81, -1.64, 42.82), 2)[0].layer
    assert "Modelo de suelo" in dtm.candidates((-1.99, 43.31, -1.98, 43.32), 1)[0].layer
    assert not dtm.candidates((-1.65, 42.81, -1.64, 42.82), 5)
    assert not dtm.candidates((4, 51, 7, 53), 1)
    assert not dtm.candidates((5.11, 52.08, 5.12, 52.09), 40)


def test_ground_resampling_keeps_a_valley_instead_of_highest_surface_pixel():
    grid = dtm._GroundGrid((0, 0, .004, .004), 222.64)
    arr = np.array([[0, 0, 100, 100], [0, 0, 100, 100], [100, 100, 0, 0], [100, 100, 0, 0]], dtype="float32")
    tr = from_origin(0, .004, .001, .001)
    grid.add(arr, tr, "EPSG:4326", (0, 0, .004, .004))
    assert np.isfinite(grid.data).all()
    assert grid.data.min() < 45 and grid.data.max() < 100


def test_wcs_terrain_read_and_grid_cache(tmp_path):
    box = (5.111, 52.081, 5.112, 52.082)
    p = dtm.candidates(box, 1)[0]
    calls = []
    def handler(req):
        calls.append(req)
        assert req.url.params["COVERAGEID"] == "dtm_05m"
        assert "dsm" not in str(req.url).lower()
        x, y = [tuple(float(v) for v in re.search(r"\((.+)\)", s).group(1).split(","))
                for s in req.url.params.get_list("SUBSET")]
        b = (x[0], y[0], x[1], y[1])
        w = h = 128
        arr = np.full((h, w), 2.5, dtype="float32")
        return httpx.Response(200, content=tiff(arr, from_origin(b[0], b[3], (b[2]-b[0])/w, (b[3]-b[1])/h), p.crs))
    f = Fetcher(tmp_path, transport=httpx.MockTransport(handler))
    grid = dem.fetch_dem(f, box, 1)
    assert grid.source == "dtm-nl" and grid.res_m == 1
    assert grid.sample(np.array([5.1115]), np.array([52.0815]))[0] == pytest.approx(2.5)
    count = len(calls)
    assert dem.fetch_dem(f, box, 1).source == grid.source and len(calls) == count
    assert "PDOK" in grid.attribution


def test_failed_terrain_keeps_worldwide_fallback(tmp_path):
    from test_dem import terrarium_png
    def handler(req):
        if "pdok.nl" in str(req.url):
            return httpx.Response(200, content=b"broken geotiff")
        return httpx.Response(200, content=terrarium_png(7))
    f = Fetcher(tmp_path, transport=httpx.MockTransport(handler))
    grid = dem.fetch_dem(f, (5.111, 52.081, 5.112, 52.082), 1)
    assert grid.source.startswith("terrarium") and any("AHN" in n for n in grid.notes)
    # An explicit old source still does exactly what was selected.
    assert dem.fetch_dem(f, (5.111, 52.081, 5.112, 52.082), 1, prefer="terrarium").source.startswith("terrarium")


def test_enclosed_survey_gap_uses_ground_but_open_water_is_not_filled():
    a = np.full((12, 12), 3., dtype="float32")
    a[4:7, 4:7] = np.nan
    a[:3, :2] = np.nan
    filled, count = dtm._fill_enclosed_gaps(a, 1)
    assert count == 9 and np.all(filled[4:7, 4:7] == 3)
    assert np.isnan(filled[:3, :2]).all()
    # A gap wider than the interpolation reach keeps its centre missing.
    a = np.zeros((100, 100), dtype="float32")
    a[10:90, 10:90] = np.nan
    assert np.isnan(dtm._fill_enclosed_gaps(a, 1)[0][50, 50])


def test_open_roof_gap_does_not_borrow_radar_roof_height_or_move_the_coast():
    class Reference:
        def sample(self, lon, lat):
            # A radar rooftop and an open sea cell in the same gap.
            return np.where(lon < 3, 20., -1.)
    data = np.full((6, 6), 3., dtype="float32")
    data[:4, :4] = np.nan
    grid = dem.DemGrid(data, "lonlat", (0., 5.), (1., 1.), res_m=1.)
    dtm.fill_land_gaps(grid, Reference())
    assert np.all(grid.data[:4, :3] == 3.)
    assert np.isnan(grid.data[:4, 3]).all()


def test_preview_lists_the_source_without_downloading(services, monkeypatch):
    monkeypatch.setattr(services.sources, "fake", False)
    p = services.make_params({"lon":5.1115,"lat":52.0815,"width_m":200,"height_m":200})
    assert "AHN DTM" in services.area_check(p)["dem"]
