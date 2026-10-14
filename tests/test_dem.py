import io
import math

import httpx
import numpy as np
import pytest
from PIL import Image, TiffImagePlugin

from gulliver_hoard.errors import GulliverError
from gulliver_hoard.sources import dem
from gulliver_hoard.sources.http import Fetcher


def terrarium_png(value_m: float) -> bytes:
    v = value_m + 32768
    r, g = int(v // 256), int(v % 256)
    b = int(round((v - math.floor(v)) * 256))
    img = Image.new("RGB", (256, 256), (r, g, b))
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def geotiff(arr: np.ndarray, lon0: float, lat0: float, step: float) -> bytes:
    info = TiffImagePlugin.ImageFileDirectory_v2()
    info[33550] = (step, step, 0.0)
    info.tagtype[33550] = 12
    info[33922] = (0.0, 0.0, 0.0, lon0, lat0, 0.0)
    info.tagtype[33922] = 12
    out = io.BytesIO()
    Image.fromarray(arr.astype(np.int32)).save(out, "TIFF", tiffinfo=info)
    return out.getvalue()


def test_terrarium_decode_and_sample(tmp_path):
    assert dem.decode_terrarium(terrarium_png(123.5))[5, 5] == pytest.approx(123.5, abs=0.01)
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=terrarium_png(640.0))

    f = Fetcher(tmp_path / "cache", transport=httpx.MockTransport(handler))
    grid = dem.fetch_terrarium(f, (-3.71, 40.41, -3.69, 40.43), 5.0)
    assert grid.source.startswith("terrarium-z") and grid.res_m < 6
    assert float(grid.sample(np.array([-3.7]), np.array([40.42]))[0]) == pytest.approx(640.0, abs=0.01)
    n = len(calls)
    dem.fetch_terrarium(f, (-3.71, 40.41, -3.69, 40.43), 5.0)
    assert len(calls) == n                       # second time from the disk cache


def test_ign_choice_and_mosaic_with_fallback(tmp_path):
    assert dem.ign_covers((-3.71, 40.41, -3.69, 40.43)) and not dem.ign_covers((2.3, 48.8, 2.4, 48.9))
    assert dem.ign_coverage((-3.71, 40.41, -3.69, 40.43), 1.5)[1] == "Elevacion4258_5"
    assert dem.ign_coverage((-4.2, 40.0, -3.2, 40.8), 40)[1] == "Elevacion4258_25"

    def handler(request):
        url = str(request.url)
        if "idee.es" in url:
            arr = np.full((60, 60), 700, dtype=np.int32)
            arr[:, :10] = -99999                     # no data on the west edge
            return httpx.Response(200, content=geotiff(arr, -3.711, 40.431, 0.0004))
        return httpx.Response(200, content=terrarium_png(500.0))

    f = Fetcher(tmp_path / "cache", transport=httpx.MockTransport(handler))
    d = dem.fetch_dem(f, (-3.71, 40.41, -3.69, 40.43), 4.0)
    assert d.source.startswith("ign-5m+terrarium")
    east = float(d.sample(np.array([-3.695]), np.array([40.42]))[0])
    west = float(d.sample(np.array([-3.7105]), np.array([40.42]))[0])
    assert east == pytest.approx(700) and west == pytest.approx(500)
    assert "Instituto Geográfico Nacional" in d.attribution and d.notes


def test_ign_failure_falls_back_to_terrarium(tmp_path):
    def handler(request):
        if "idee.es" in str(request.url):
            return httpx.Response(500, text="down")
        return httpx.Response(200, content=terrarium_png(300.0))

    f = Fetcher(tmp_path / "cache", transport=httpx.MockTransport(handler))
    d = dem.fetch_dem(f, (-3.71, 40.41, -3.69, 40.43), 4.0)
    assert d.source.startswith("terrarium") and any("IGN" in n for n in d.notes)
    with pytest.raises(GulliverError):
        dem.fetch_dem(f, (-3.71, 40.41, -3.69, 40.43), 4.0, prefer="ign")
