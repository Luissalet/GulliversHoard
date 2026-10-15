"""Real roof and tower heights from free national surface models (DSM: the top of what a laser scanner saw from the air,
buildings, spires and trees included), read without any key: WCS 2.0.1 / 1.0.0 and WMS GetMap services of the Netherlands
(AHN4), France (IGN LiDAR HD), England (Environment Agency), North Rhine-Westphalia, Norway, Poland and Flanders, and the
Cloud-Optimised GeoTIFFs of swisstopo (swissSURFACE3D, found through its STAC catalogue, newest year of each tile).

``fetch`` asks the service in its own coordinate system in tiles below its size limit (at its native resolution, or coarser
where the service can scale), puts every tile onto one regular lon/lat grid of about ``res_m`` metres (keeping the highest
value where a coarse pixel covers several fine ones, so spires are not averaged away) and caches the grid on disk.
``building_heights`` then turns that grid into one roof height, one top height and the towers of each footprint: the
ground beside the building is taken from the model itself (a low percentile of a ring around the footprint, away from other
buildings), unless the model is already heights above ground (France offers that one directly)."""

from __future__ import annotations

import io
import json
import math
from dataclasses import dataclass
from typing import Any, Callable, Optional

import numpy as np

from ..errors import GulliverError
from .http import Fetcher

MAX_AREA_KM2 = 25.0
MAX_PIXELS = 36_000_000            # of the grid handed back; past it the grid is made coarser
M_PER_DEG = 111_320.0
FAR = 1e30                         # float nodata such as 3.4028e38 in either sign

STAC = "https://data.geo.admin.ch/api/stac/v0.9/collections/ch.swisstopo.swisssurface3d-raster/items"


def _poly(*pts: tuple[float, float]) -> Any:
    from shapely.geometry import Polygon
    return Polygon(pts)


@dataclass(frozen=True, eq=False)
class Provider:
    """One free surface model. ``kind`` picks how it is asked: ``wcs2`` (WCS 2.0.1 KVP), ``wcs1`` (WCS 1.0.0 with a pixel
    size), ``wms`` (WMS 1.3.0 GetMap as float GeoTIFF), ``stac`` (swisstopo COGs)."""

    key: str
    name: str
    country: str
    attribution: str
    kind: str
    url: str
    crs: int                                   # coordinate system the service is asked in
    native_m: float
    outline: Any                               # rough country outline (lon, lat); nodata decides at the edges
    layer: str = ""                            # coverage id / layer
    ndsm_layer: str = ""                       # layer giving heights above ground, when the service has one
    axes: tuple[str, str] = ("x", "y")         # WCS 2 subset labels for (east/lon, north/lat)
    fmt: str = "image/tiff"
    scalable: bool = False                     # can be asked for coarser pixels than the native ones
    tile_px: int = 2000                        # pixels a side of one request (under the service's own limit)
    nodata: tuple[float, ...] = ()
    zero_is_nodata: bool = False
    licence: str = ""

    def covers(self, lon: float, lat: float) -> bool:
        from shapely.geometry import Point
        return bool(self.outline.contains(Point(lon, lat)))


PROVIDERS: dict[str, Provider] = {p.key: p for p in (
    Provider(
        "ch", "swissSURFACE3D Raster", "Suiza", "Modelo de superficies swissSURFACE3D © swisstopo (datos abiertos OGD)", "stac",
        STAC, 2056, 0.5,
        _poly((5.95, 46.1), (6.1, 46.4), (7.0, 47.5), (7.6, 47.6), (8.6, 47.8), (9.5, 47.55), (9.55, 47.0), (10.5, 46.9),
              (10.45, 46.5), (9.0, 45.85), (8.4, 46.2), (7.0, 45.9)),
        licence="swisstopo OGD", nodata=(-9999.0,)),
    Provider(
        "fl", "Digitaal Hoogtemodel Vlaanderen II, DSM", "Flandes", "Modelo de superficies © Digitaal Vlaanderen (uso gratuito)",
        "wcs2", "https://geo.api.vlaanderen.be/el-dsm/wcs", 4258, 1.0,
        _poly((2.55, 51.09), (3.37, 51.38), (4.25, 51.43), (4.9, 51.47), (5.55, 51.3), (5.85, 50.95), (5.2, 50.75), (4.5, 50.7),
              (3.6, 50.72), (2.9, 50.72), (2.55, 50.8)),
        layer="EL.GridCoverage.DSM", tile_px=1400, nodata=(-9999.0,), licence="Gebruiksrecht Digitaal Vlaanderen"),
    Provider(
        "nl", "AHN4, modelo de superficies (DSM)", "Países Bajos", "Modelo de superficies AHN4 © Rijkswaterstaat (CC0)", "wcs2",
        "https://service.pdok.nl/rws/ahn/wcs/v1_0", 28992, 0.5,
        _poly((3.35, 51.37), (4.3, 51.4), (5.0, 51.5), (5.5, 51.3), (5.65, 50.8), (6.05, 50.75), (6.2, 51.1), (5.95, 51.8),
              (6.8, 51.95), (7.05, 52.25), (7.05, 52.65), (7.2, 53.2), (7.25, 53.55), (6.0, 53.7), (4.7, 53.1), (4.55, 52.5),
              (3.7, 51.85)),
        layer="dsm_05m", scalable=True, tile_px=3000, licence="CC0"),
    Provider(
        "nrw", "DOM1 Renania del Norte-Westfalia", "Alemania (NRW)",
        "Modelo de superficies DOM1 © Land NRW / Geobasis NRW (dl-de/zero-2.0)", "wcs2",
        "https://www.wcs.nrw.de/geobasis/wcs_nw_dom", 25832, 1.0,
        _poly((5.9, 50.75), (6.4, 50.32), (7.1, 50.5), (8.0, 50.6), (8.3, 51.1), (8.5, 51.3), (9.2, 51.5), (9.45, 52.0),
              (8.9, 52.5), (7.2, 52.4), (7.05, 52.25), (6.8, 51.95), (5.95, 51.8)),
        layer="nw_dom", scalable=True, tile_px=2000, licence="dl-de/zero-2.0"),
    Provider(
        "fr", "IGN LiDAR HD, MNS", "Francia", "Modelo de superficies LiDAR HD © IGN (Licence Ouverte)", "wms",
        "https://data.geopf.fr/wms-r/wms", 2154, 0.5,
        _poly((-4.8, 48.4), (-1.5, 49.7), (2.5, 51.1), (4.8, 50.2), (6.2, 49.5), (8.2, 49.0), (7.6, 47.6), (6.9, 47.4),
              (6.0, 46.2), (7.0, 45.9), (7.1, 44.1), (7.7, 43.8), (3.2, 42.4), (-1.8, 43.4), (-1.2, 46.0), (-2.3, 47.2)),
        layer="IGNF_LIDAR-HD_MNS_ELEVATION.ELEVATIONGRIDCOVERAGE.LAMB93",
        ndsm_layer="IGNF_LIDAR-HD_MNH_ELEVATION.ELEVATIONGRIDCOVERAGE.LAMB93",
        fmt="image/geotiff", scalable=True, tile_px=2500, nodata=(-9999.0,), licence="Licence Ouverte / Etalab"),
    Provider(
        "uk", "EA LIDAR Composite DSM 1 m (first return)", "Inglaterra",
        "Modelo de superficies LIDAR Composite © Environment Agency (Open Government Licence)", "wcs2",
        "https://environment.data.gov.uk/spatialdata/lidar-composite-digital-surface-model-first-return-dsm-1m/wcs", 27700, 1.0,
        _poly((-5.8, 49.9), (1.5, 50.9), (1.8, 52.8), (0.2, 53.7), (-0.3, 54.5), (-1.6, 55.8), (-2.2, 55.8), (-3.05, 54.95),
              (-3.4, 54.2), (-3.0, 53.4), (-3.1, 52.5), (-2.7, 51.6), (-3.2, 51.35), (-3.6, 51.2), (-4.6, 50.8)),
        layer="df4e3ec3-315e-48aa-aaaf-b5ae74d7b2bb__Lidar_Composite_Elevation_FZ_DSM_1m", axes=("E", "N"), scalable=True,
        tile_px=2000, licence="OGL v3 (unverified)"),
    Provider(
        "pl", "NMPT, modelo de superficies (GUGiK)", "Polonia", "Modelo de superficies NMPT © GUGiK (datos abiertos)", "wcs2",
        "https://mapy.geoportal.gov.pl/wss/service/PZGIK/NMPT/GRID1/WCS/DigitalSurfaceModel", 2180, 0.5,
        _poly((14.1, 53.9), (14.2, 52.8), (14.7, 52.1), (15.0, 51.0), (15.0, 50.8), (16.8, 50.2), (18.0, 49.8), (18.8, 49.5),
              (22.5, 49.0), (24.1, 50.4), (23.6, 51.5), (23.5, 52.2), (23.2, 52.6), (23.9, 52.8), (23.5, 53.9), (22.8, 54.35),
              (19.5, 54.45), (18.3, 54.8), (16.5, 54.6)),
        layer="DSM_PL-EVRF2007-NH", fmt="image/x-aaigrid", scalable=True, tile_px=1000, licence="datos abiertos GUGiK"),
    Provider(
        "no", "Høydedata, modelo de superficies (DOM)", "Noruega", "Modelo de superficies © Kartverket (Høydedata)", "wcs1",
        "https://wcs.geonorge.no/skwms1/wcs.hoyde-dom_somlos", 25833, 0.5,
        _poly((4.5, 58.0), (4.5, 62.0), (11.0, 67.5), (16.0, 70.0), (31.0, 71.0), (31.0, 69.5), (28.0, 69.0), (24.0, 68.6),
              (20.5, 69.05), (18.0, 68.4), (15.5, 66.2), (13.5, 64.5), (12.2, 63.0), (12.0, 61.0), (12.5, 60.0), (11.7, 59.0),
              (8.0, 58.0)),
        layer="las_dom", fmt="GeoTIFF", scalable=True, tile_px=2000, zero_is_nodata=True, licence="Kartverket (NLOD)"),
)}


def providers_for(bbox: tuple[float, float, float, float]) -> list[Provider]:
    """Every provider that covers the middle of ``bbox`` (best first), then those that cover only a corner."""
    lon0, lat0, lon1, lat1 = bbox
    cx, cy = (lon0 + lon1) / 2.0, (lat0 + lat1) / 2.0
    first = [p for p in PROVIDERS.values() if p.covers(cx, cy)]
    corners = [(lon0, lat0), (lon0, lat1), (lon1, lat0), (lon1, lat1)]
    rest = [p for p in PROVIDERS.values() if p not in first and any(p.covers(x, y) for x, y in corners)]
    return first + rest


def provider_for(bbox: tuple[float, float, float, float]) -> Optional[Provider]:
    """The provider for the area (the one covering its centre), or None: Madrid, the Amazon, most of the world."""
    found = providers_for(bbox)
    return found[0] if found else None


# -- reading what the services answer ----------------------------------------------------------------------------------------
def _is_error(response: Any) -> bool:
    ct = response.headers.get("content-type", "").lower()
    if ct.startswith("multipart"):
        return False
    return any(s in ct for s in ("xml", "html", "json", "text/plain"))


def multipart_part(data: bytes, prefix: bytes = b"image/") -> bytes:
    """Body of the first part of a multipart/related answer whose Content-Type starts with ``prefix`` (a plain body is
    returned as it is). WCS 2.0 of Flanders and Poland answers like this: a GML description, then the data."""
    data = data.lstrip()
    if not data.startswith(b"--"):
        return data
    boundary = data[:data.find(b"\n")].strip()
    for part in data.split(boundary)[1:]:
        for sep in (b"\r\n\r\n", b"\n\n"):
            head, found, body = part.partition(sep)
            if found:
                break
        else:
            continue
        if b"content-type: " + prefix in head.lower():
            return body
    raise GulliverError("source_failed", "The surface model answered without any raster.", "Try again later.")


def parse_aaigrid(data: bytes) -> tuple[np.ndarray, Any]:
    """An Arc/Info ASCII grid as (float32 rows north to south, affine transform)."""
    from rasterio.transform import from_origin

    head: dict[str, float] = {}
    pos = 0
    while True:
        eol = data.find(b"\n", pos)
        line = data[pos:eol if eol >= 0 else len(data)].split()
        if len(line) == 2 and line[0].decode("ascii", "ignore").lower() in (
                "ncols", "nrows", "xllcorner", "yllcorner", "xllcenter", "yllcenter", "cellsize", "nodata_value"):
            head[line[0].decode().lower()] = float(line[1])
            pos = eol + 1
            if eol < 0:
                break
            continue
        break
    try:
        ncols, nrows, cell = int(head["ncols"]), int(head["nrows"]), head["cellsize"]
        arr = np.array(data[pos:].split(), dtype=np.float32).reshape(nrows, ncols)
    except (KeyError, ValueError) as exc:
        raise GulliverError("source_failed", "The surface model answered with a grid that could not be read.", "Try again later.") from exc
    if "nodata_value" in head:
        arr[arr == np.float32(head["nodata_value"])] = np.nan
    xll = head["xllcorner"] if "xllcorner" in head else head["xllcenter"] - cell / 2.0
    yll = head["yllcorner"] if "yllcorner" in head else head["yllcenter"] - cell / 2.0
    return arr, from_origin(xll, yll + nrows * cell, cell, cell)


def _decode_tiff(data: bytes) -> tuple[np.ndarray, Any, Any]:
    import rasterio
    from rasterio.io import MemoryFile
    try:
        with MemoryFile(multipart_part(data)) as mem, mem.open() as ds:
            arr = ds.read(1).astype(np.float32)
            if ds.nodata is not None and np.isfinite(ds.nodata):
                arr[arr == np.float32(ds.nodata)] = np.nan
            return arr, ds.transform, ds.crs
    except rasterio.errors.RasterioError as exc:
        raise GulliverError("source_failed", "The surface model answered with a raster that could not be read.", "Try again later.") from exc


def _clean(arr: np.ndarray, p: Provider) -> np.ndarray:
    arr = np.asarray(arr, dtype=np.float32)
    bad = ~np.isfinite(arr) | (np.abs(arr) > FAR)
    for v in p.nodata:
        bad |= arr == np.float32(v)
    if p.zero_is_nodata:
        bad |= arr == 0.0
    arr = arr.copy()
    arr[bad] = np.nan
    return arr


def _request(p: Provider, ndsm: bool, box: tuple[float, float, float, float], step: float) -> tuple[dict, str]:
    """Query parameters of one tile ``box`` = (x0, y0, x1, y1) in the provider's coordinates, pixels ``step`` wide."""
    x0, y0, x1, y1 = box
    layer = p.ndsm_layer if (ndsm and p.ndsm_layer) else p.layer
    w = max(1, int(round((x1 - x0) / step)))
    h = max(1, int(round((y1 - y0) / step)))
    if p.kind == "wms":
        return {"SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetMap", "LAYERS": layer, "STYLES": "", "CRS": f"EPSG:{p.crs}",
                "BBOX": f"{x0:.3f},{y0:.3f},{x1:.3f},{y1:.3f}", "WIDTH": w, "HEIGHT": h, "FORMAT": p.fmt}, p.url
    if p.kind == "wcs1":
        return {"SERVICE": "WCS", "VERSION": "1.0.0", "REQUEST": "GetCoverage", "COVERAGE": layer, "CRS": f"EPSG:{p.crs}",
                "BBOX": f"{x0:.3f},{y0:.3f},{x1:.3f},{y1:.3f}", "WIDTH": w, "HEIGHT": h, "FORMAT": p.fmt}, p.url
    fmt = "%.8f" if p.crs == 4258 else "%.3f"
    params: dict[str, Any] = {
        "SERVICE": "WCS", "VERSION": "2.0.1", "REQUEST": "GetCoverage", "COVERAGEID": layer, "FORMAT": p.fmt,
        "SUBSET": [f"{p.axes[0]}({fmt % x0},{fmt % x1})", f"{p.axes[1]}({fmt % y0},{fmt % y1})"]}
    if p.scalable and step > p.native_m * 1.05:
        params["SCALEFACTOR"] = f"{p.native_m / step:.4f}"
    return params, p.url


def _fetch_tile(fetcher: Fetcher, p: Provider, ndsm: bool, box: tuple[float, float, float, float], step: float):
    params, url = _request(p, ndsm, box, step)
    for attempt in (1, 2):                                   # some of these servers drop a connection now and then
        try:
            data = fetcher.get(url, params=params, bucket="dsm", ext=".tif", accept=lambda r: not _is_error(r))
            break
        except GulliverError as exc:
            if attempt == 2 or exc.code not in ("unreachable", "source_failed"):
                raise
    if p.fmt == "image/x-aaigrid":
        arr, transform = parse_aaigrid(multipart_part(data, b"image/x-aaigrid"))
        crs = p.crs
    else:
        arr, transform, _file_crs = _decode_tiff(data)       # the file's CRS name is not always a clean EPSG code
        crs = p.crs
    return _clean(arr, p), transform, crs


# -- the regular lon/lat grid -------------------------------------------------------------------------------------------------
class _Grid:
    """The output: a lon/lat raster of heights, to which tiles in any coordinate system are added (highest value wins)."""

    def __init__(self, bbox: tuple[float, float, float, float], res_m: float):
        from rasterio.transform import from_origin

        lon0, lat0, lon1, lat1 = bbox
        coslat = max(0.05, math.cos(math.radians((lat0 + lat1) / 2.0)))
        self.dlat = res_m / M_PER_DEG
        self.dlon = res_m / (M_PER_DEG * coslat)
        self.w = max(1, int(math.ceil((lon1 - lon0) / self.dlon)))
        self.h = max(1, int(math.ceil((lat1 - lat0) / self.dlat)))
        self.lon0, self.lat1 = lon0, lat1
        self.transform = from_origin(lon0, lat1, self.dlon, self.dlat)
        self.data = np.full((self.h, self.w), np.nan, dtype=np.float32)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return (self.lon0, self.lat1 - self.h * self.dlat, self.lon0 + self.w * self.dlon, self.lat1)

    def window(self, lonlat_bounds: tuple[float, float, float, float]) -> Optional[tuple[int, int, int, int]]:
        l, b, r, t = lonlat_bounds
        c0 = max(0, int(math.floor((l - self.lon0) / self.dlon)))
        c1 = min(self.w, int(math.ceil((r - self.lon0) / self.dlon)))
        r0 = max(0, int(math.floor((self.lat1 - t) / self.dlat)))
        r1 = min(self.h, int(math.ceil((self.lat1 - b) / self.dlat)))
        return (r0, r1, c0, c1) if (c1 > c0 and r1 > r0) else None

    def add(self, source: Any, src_transform: Any, src_crs: Any, src_bounds: tuple[float, float, float, float],
            *, src_nodata: float = float("nan")) -> None:
        """Reproject ``source`` (an array, or a rasterio band) into the part of the grid it covers."""
        from rasterio.crs import CRS
        from rasterio.enums import Resampling
        from rasterio.warp import reproject, transform_bounds

        crs = CRS.from_user_input(src_crs)
        win = self.window(transform_bounds(crs, "EPSG:4326", *src_bounds, densify_pts=21))
        if win is None:
            return
        r0, r1, c0, c1 = win
        sub = np.full((r1 - r0, c1 - c0), np.nan, dtype=np.float32)
        sub_transform = self.transform @ type(self.transform).translation(c0, r0)
        reproject(source=source, destination=sub, src_transform=src_transform, src_crs=crs, dst_transform=sub_transform,
                  dst_crs="EPSG:4326", resampling=Resampling.max, src_nodata=src_nodata, dst_nodata=float("nan"))
        cur = self.data[r0:r1, c0:c1]
        np.fmax(cur, sub, out=cur)

    def valid_fraction(self) -> float:
        return float(np.isfinite(self.data).mean())


def _tile_boxes(box: tuple[float, float, float, float], tile_m: float) -> list[tuple[float, float, float, float]]:
    x0, y0, x1, y1 = box
    nx = max(1, int(math.ceil((x1 - x0) / tile_m - 1e-9)))
    ny = max(1, int(math.ceil((y1 - y0) / tile_m - 1e-9)))
    tw, th = (x1 - x0) / nx, (y1 - y0) / ny
    return [(x0 + i * tw, y0 + j * th, x0 + (i + 1) * tw, y0 + (j + 1) * th) for j in range(ny - 1, -1, -1) for i in range(nx)]


def _fill_service(fetcher: Fetcher, p: Provider, ndsm: bool, bbox: tuple[float, float, float, float], res_m: float,
                  grid: _Grid, progress: Optional[Callable[[str], None]]) -> None:
    from rasterio.warp import transform_bounds

    nb = transform_bounds("EPSG:4326", f"EPSG:{p.crs}", *bbox, densify_pts=21)
    step = p.native_m
    if p.scalable and res_m >= 1.8 * p.native_m:
        step = round(res_m, 3)                      # the service resamples; its pixels are about the grid's
    degrees = p.crs == 4258
    unit = 1.0 / M_PER_DEG if degrees else 1.0      # metres -> the service's units
    pad = 2.0 * step * unit
    box = (nb[0] - pad, nb[1] - pad, nb[2] + pad, nb[3] + pad)
    if degrees:
        # the service's pixels are about native_m metres; a tile is tile_px of them
        boxes = _tile_boxes(box, p.tile_px * p.native_m * unit)
        step_u = p.native_m * unit
    else:
        boxes = _tile_boxes(box, p.tile_px * step)
        step_u = step
    for i, b in enumerate(boxes):
        arr, transform, crs = _fetch_tile(fetcher, p, ndsm, b, step_u)
        if np.isfinite(arr).any():
            grid.add(arr, transform, crs, b)
        if progress:
            progress(f"Modelo de superficies ({p.country}): {i + 1}/{len(boxes)}")


def _fill_stac(fetcher: Fetcher, p: Provider, bbox: tuple[float, float, float, float], grid: _Grid,
               progress: Optional[Callable[[str], None]]) -> None:
    import rasterio

    params = {"bbox": ",".join(f"{v:.6f}" for v in bbox), "limit": 100}
    url, features = p.url, []
    for _page in range(6):
        doc = json.loads(fetcher.get(url, params=params, bucket="dsm", ext=".json", max_age_s=86400.0,
                                     accept=lambda r: not r.headers.get("content-type", "").startswith("text/html")))
        features += doc.get("features", [])
        nxt = next((ln.get("href") for ln in doc.get("links", []) if ln.get("rel") == "next"), None)
        if not nxt or not doc.get("features"):
            break
        url, params = nxt, None
    newest: dict[str, tuple[str, str]] = {}                 # tile -> (year, href)
    for f in features:
        parts = str(f.get("id", "")).split("_")
        if len(parts) < 3:
            continue
        year, tile = parts[-2], parts[-1]
        assets = [a for a in (f.get("assets") or {}).values() if str(a.get("href", "")).endswith(".tif")]
        best = next((a for a in assets if "_0.5_" in a["href"]), assets[0] if assets else None)
        if best and (tile not in newest or year > newest[tile][0]):
            newest[tile] = (year, best["href"])
    hrefs = [h for _y, h in sorted(newest.values(), key=lambda v: v[1])]
    env = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif", GDAL_HTTP_MAX_RETRY="3",
               GDAL_HTTP_RETRY_DELAY="1", VSI_CACHE="TRUE")
    with rasterio.Env(**env):
        for i, href in enumerate(hrefs):
            try:
                ds = rasterio.open("/vsicurl/" + href)
            except rasterio.errors.RasterioIOError:
                continue
            with ds:
                nodata = -9999.0 if ds.nodata is None else float(ds.nodata)
                grid.add(rasterio.band(ds, 1), ds.transform, ds.crs, tuple(ds.bounds), src_nodata=nodata)
            if progress:
                progress(f"Modelo de superficies ({p.country}): {i + 1}/{len(hrefs)}")


def fetch(fetcher: Fetcher, bbox: tuple[float, float, float, float], *, res_m: float = 1.0,
          progress: Optional[Callable[[str], None]] = None, ndsm: bool = False, provider: Optional[Provider] = None
          ) -> tuple[np.ndarray, tuple[float, float, float, float], str, bool]:
    """``(heights, bounds, attribution, is_ndsm)`` over ``bbox`` = (lon0, lat0, lon1, lat1): float32 metres, rows north to
    south, NaN where the model has no data; ``bounds`` = (lon0, lat0, lon1, lat1) of the array. ``is_ndsm`` is True when the
    values are already heights above the ground (only with ``ndsm=True`` on a provider that has such a layer, France).
    Several providers that overlap the area are tried in turn until one has data."""
    lon0, lat0, lon1, lat1 = bbox
    if not (lon1 > lon0 and lat1 > lat0):
        raise GulliverError("invalid", "The area is empty.", "Give an area with a positive width and height.")
    area_km2 = (lon1 - lon0) * M_PER_DEG * math.cos(math.radians((lat0 + lat1) / 2.0)) * (lat1 - lat0) * M_PER_DEG / 1e6
    if area_km2 > MAX_AREA_KM2:
        raise GulliverError("too_large", f"The area ({area_km2:.0f} km²) is too large to read real building heights.",
                            f"Use an area of at most {MAX_AREA_KM2:.0f} km².")
    candidates = [provider] if provider else providers_for(bbox)
    if not candidates:
        raise GulliverError("unavailable", "There is no free surface model for this area.",
                            "Building heights will come from the map's own height tags and floor counts.")
    res_m = max(0.25, float(res_m))
    coslat = math.cos(math.radians((lat0 + lat1) / 2.0))
    while (math.ceil((lon1 - lon0) * M_PER_DEG * coslat / res_m) * math.ceil((lat1 - lat0) * M_PER_DEG / res_m)) > MAX_PIXELS:
        res_m *= 1.1
    try:
        import rasterio  # noqa: F401
    except ImportError as exc:                               # pragma: no cover - a dependency
        raise GulliverError("unavailable", "rasterio is not installed.", "pip install rasterio") from exc
    last: Optional[GulliverError] = None
    for p in candidates:
        use_ndsm = bool(ndsm and p.ndsm_layer)
        key = f"dsm|{p.key}|{use_ndsm}|{lon0:.6f}|{lat0:.6f}|{lon1:.6f}|{lat1:.6f}|{res_m:.3f}"
        hit = fetcher.cached("dsm_grid", key, ".npz")
        if hit is not None:
            z = np.load(io.BytesIO(hit))
            return z["h"], tuple(float(v) for v in z["bounds"]), p.attribution, use_ndsm
        grid = _Grid(bbox, max(res_m, p.native_m if p.kind == "stac" else 0.0))
        try:
            if p.kind == "stac":
                _fill_stac(fetcher, p, bbox, grid, progress)
            else:
                _fill_service(fetcher, p, use_ndsm, bbox, grid.dlat * M_PER_DEG, grid, progress)
        except GulliverError as exc:
            last = exc
            continue
        except Exception as exc:  # noqa: BLE001 - a read failure: the next provider, or the build carries on without
            last = GulliverError("source_failed", f"The surface model ({p.country}) could not be read: {exc}", "Try again later.")
            continue
        if grid.valid_fraction() < 0.02:
            last = GulliverError("source_failed", f"The surface model ({p.country}) has no data here.", "")
            continue
        buf = io.BytesIO()
        np.savez_compressed(buf, h=grid.data, bounds=np.array(grid.bounds, dtype=np.float64))
        fetcher.store("dsm_grid", key, buf.getvalue(), ".npz")
        return grid.data, grid.bounds, p.attribution, use_ndsm
    raise last or GulliverError("source_failed", "No surface model could be read.", "Try again later.")


# -- heights of buildings -----------------------------------------------------------------------------------------------------
def _sample_grid(heights: np.ndarray, bounds: tuple[float, float, float, float], projector: Any,
                 minx: float, maxy: float, cell: float, W: int, H: int) -> np.ndarray:
    """``heights`` sampled (nearest) on a metre grid with its top-left corner at (minx, maxy) in the projection's metres."""
    h, w = heights.shape
    lon0, lat0, lon1, lat1 = bounds
    dlon, dlat = (lon1 - lon0) / w, (lat1 - lat0) / h
    xs = minx + (np.arange(W) + 0.5) * cell
    ys = maxy - (np.arange(H) + 0.5) * cell
    lons = np.asarray(projector.inv(xs, np.full(W, ys[H // 2]))[0], dtype=np.float64)       # the projection is a plain
    lats = np.asarray(projector.inv(np.full(H, xs[W // 2]), ys)[1], dtype=np.float64)       # local one: lon follows x, lat y
    cols = np.floor((lons - lon0) / dlon).astype(np.int64)
    rows = np.floor((lat1 - lats) / dlat).astype(np.int64)
    cok = np.nonzero((cols >= 0) & (cols < w))[0]
    rok = np.nonzero((rows >= 0) & (rows < h))[0]
    out = np.full((H, W), np.nan, dtype=np.float32)
    if len(cok) and len(rok):
        out[np.ix_(rok, cok)] = heights[np.ix_(rows[rok], cols[cok])]
    return out


def building_heights(heights: np.ndarray, bounds: tuple[float, float, float, float], projector: Any, shapes: list, *,
                     is_ndsm: bool = False, ground_ring_m: tuple[float, float] = (2.0, 8.0), others: Any = None,
                     tower_over_m: float = 6.0, tower_min_area_m2: float = 6.0, ground_pct: float = 20.0,
                     max_cells: int = 40_000_000
                     ) -> list[Optional[dict]]:
    """For each footprint (shapely polygons in metres of ``projector``) a dict, or None where the model has no data:

    ``roof_m``: 90th percentile of the height above ground inside the footprint;
    ``top_m``: its highest point that is at least 2 m wide (so a lone noisy cell or a crane's wire does not count; a spire's
    tip does), or the 99.5th percentile if that is higher;
    ``towers``: ``[(polygon in metres, top_m), ...]`` for the connected parts of the footprint more than ``tower_over_m``
    above ``roof_m`` and at least ``tower_min_area_m2`` wide, highest first.

    The ground is 0 for an nDSM; otherwise the ``ground_pct``-th (20th) percentile of the model in the ring ``ground_ring_m`` around the
    footprint, leaving out the area of ``others`` (all the buildings, a shapely geometry; by default the footprints
    themselves)."""
    import rasterio.features
    from rasterio.transform import Affine
    from scipy import ndimage
    from shapely.geometry import shape as to_shape

    out: list[Optional[dict]] = [None] * len(shapes)
    todo = [i for i, s in enumerate(shapes) if s is not None and not s.is_empty]
    if not todo or heights.size == 0 or not np.isfinite(heights).any():
        return out
    h, w = heights.shape
    lon0, lat0, lon1, lat1 = bounds
    x_a, y_a = projector.fwd(lon0, lat0)
    x_b, _ = projector.fwd(lon0 + (lon1 - lon0) / w, lat0)
    _, y_b = projector.fwd(lon0, lat0 + (lat1 - lat0) / h)
    cell = max(abs(float(x_b - x_a)), abs(float(y_b - y_a)), 0.25)
    ring_in, ring_out = float(ground_ring_m[0]), float(ground_ring_m[1])
    pad = 2.5 * ring_out + 2 * cell
    bb = np.array([shapes[i].bounds for i in todo], dtype=np.float64)
    minx, miny = float(bb[:, 0].min()) - pad, float(bb[:, 1].min()) - pad
    maxx, maxy = float(bb[:, 2].max()) + pad, float(bb[:, 3].max()) + pad
    W, H = int(math.ceil((maxx - minx) / cell)), int(math.ceil((maxy - miny) / cell))
    if W * H > max_cells:
        cell *= math.sqrt(W * H / max_cells)
        W, H = int(math.ceil((maxx - minx) / cell)), int(math.ceil((maxy - miny) / cell))
    G = _sample_grid(heights, bounds, projector, minx, maxy, cell, W, H)
    T = Affine(cell, 0.0, minx, 0.0, -cell, maxy)
    lab = rasterio.features.rasterize(((shapes[i], i + 1) for i in todo), out_shape=(H, W), transform=T, fill=0, dtype="int32")
    solid = lab > 0
    if others is not None and not others.is_empty:
        solid |= rasterio.features.rasterize([(others, 1)], out_shape=(H, W), transform=T, fill=0, dtype="uint8").astype(bool)
    pad_px = int(math.ceil(pad / cell))
    ones8 = np.ones((3, 3), dtype=bool)
    for i in todo:
        sx0, sy0, sx1, sy1 = shapes[i].bounds
        c0 = max(0, int((sx0 - minx) / cell) - pad_px)
        c1 = min(W, int((sx1 - minx) / cell) + pad_px + 1)
        r0 = max(0, int((maxy - sy1) / cell) - pad_px)
        r1 = min(H, int((maxy - sy0) / cell) + pad_px + 1)
        if c1 <= c0 or r1 <= r0:
            continue
        sub = G[r0:r1, c0:c1]
        own = lab[r0:r1, c0:c1] == i + 1
        if not own.any():                                    # smaller than a cell: the cell under its middle
            p = shapes[i].representative_point()
            cc, rr = int((p.x - minx) / cell) - c0, int((maxy - p.y) / cell) - r0
            if 0 <= rr < own.shape[0] and 0 <= cc < own.shape[1]:
                own[rr, cc] = True
            else:
                continue
        fin = np.isfinite(sub)
        vals = sub[own & fin]
        if vals.size == 0:
            continue
        if is_ndsm:
            ground = 0.0
        else:
            dist = ndimage.distance_transform_edt(~own) * cell
            ground = None
            for outer in (ring_out, ring_out * 2.5):
                ring = (dist > ring_in) & (dist <= outer) & ~solid[r0:r1, c0:c1] & fin
                if int(ring.sum()) >= 8:
                    ground = float(np.percentile(sub[ring], ground_pct))
                    break
            if ground is None:
                continue
        rel = np.maximum(vals - ground, 0.0)
        roof = float(np.percentile(rel, 90))
        top = float(np.percentile(rel, 99.5))
        rel_sub = sub - ground
        k = int(round(2.0 / cell))
        if k >= 2 and own.shape[0] >= k and own.shape[1] >= k:      # a spire's tip is a few cells wide, a lone spike is one:
            opened = ndimage.grey_opening(np.where(own & fin, rel_sub, 0.0), size=(k, k))   # keep what is 2 m wide
            top = max(top, float(opened.max()))
        towers: list[tuple[Any, float]] = []
        thr = roof + tower_over_m
        hi = own & fin & (rel_sub > thr)
        if hi.any():
            comp, n = ndimage.label(hi, structure=ones8)
            idx = np.arange(1, n + 1)
            areas = ndimage.sum(np.ones_like(comp, dtype=np.float32), comp, idx) * cell * cell
            tops = ndimage.maximum(np.where(hi, rel_sub, 0.0), comp, idx)
            wt = T @ Affine.translation(c0, r0)
            for k in np.argsort(-np.asarray(tops))[:12]:
                if areas[k] < tower_min_area_m2:
                    continue
                geoms = [g for g, _v in rasterio.features.shapes((comp == k + 1).astype(np.uint8), mask=comp == k + 1, transform=wt)]
                if not geoms:
                    continue
                poly = to_shape(geoms[0]).buffer(0).simplify(0.6 * cell, preserve_topology=True)
                poly = poly.intersection(shapes[i])
                if poly.is_empty:
                    continue
                if poly.geom_type != "Polygon":
                    parts = [g for g in getattr(poly, "geoms", []) if g.geom_type == "Polygon"]
                    if not parts:
                        continue
                    poly = max(parts, key=lambda g: g.area)
                towers.append((poly, round(float(tops[k]), 2)))
            towers.sort(key=lambda t: -t[1])
            if towers:
                top = max(top, towers[0][1])
        out[i] = {"roof_m": round(roof, 2), "top_m": round(top, 2), "towers": towers}
    return out
