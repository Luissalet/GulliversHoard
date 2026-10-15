"""From parameters to solids: elevation + OpenStreetMap -> the parts of a printable model (terrain, buildings, water, roads,
green, frame), optionally cut into tiles with pin holes, every part a closed manifold.

Order of work: elevation grid (smoothed to the source's real resolution), sea traced from the elevation, the terrain
solid with roads / rails / contour lines cut or raised along their real outlines, water cut into it down to one continuous
surface (lakes flat, rivers sloping gently with no steps), bridges over water, buildings standing on the lowest point of their
footprint, then the outline shape, the frame, the text on the bottom, the tiles and their pin holes. In colour mode the
same solid is partitioned into parts (one per filament) that fit each other exactly."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

from ..errors import Cancelled, GulliverError
from ..geo import format_coord, nice_scale
from ..params import MAX_CELLS, ModelParams
from ..sources import aerial as aerial_mod
from ..sources import catastro as catastro_mod
from ..sources import osm as osm_mod
from . import detail
from . import solids as S

PART_COLORS = {
    "terrain": "#D8CCB4",
    "buildings": "#F2EEE6",
    "water": "#5E9FD1",
    "roads": "#8A8478",
    "rails": "#6B6660",
    "green": "#7FA56A",
    "trees": "#3E7A3A",
    "rock": "#9A968F",
    "snow": "#F7F7F4",
    "frame": "#3A3A3A",
}
PART_LABELS = {
    "terrain": ("Terreno", "Terrain"),
    "buildings": ("Edificios", "Buildings"),
    "water": ("Agua", "Water"),
    "roads": ("Carreteras", "Roads"),
    "rails": ("Vías del tren", "Railways"),
    "green": ("Zonas verdes", "Green areas"),
    "trees": ("Árboles", "Trees"),
    "rock": ("Roca", "Rock"),
    "snow": ("Nieve", "Snow"),
    "frame": ("Marco", "Frame"),
}
SKIN_MM = 0.6                 # thickness of a colour layer on top of the terrain
BRIDGE_DECK_MM = 0.5          # thickness of a bridge deck over its arches
OVERPASS_DECK_MM = 0.35       # a bridge off the water (over a street, a railway) is thinner: the clearance under it is small
OVERPASS_DIP_MM = 0.15        # ...and drawn when the ground under it is at least its thickness plus this below the deck
WEIR = {"weir": (0.4, 3.0, 0.25), "dam": (0.6, 8.0, 0.6)}   # kind -> (narrowest mm, real width m, top above the water mm)
ROAD_GAP_MM = 0.18            # ground narrower than twice this between two roads becomes road
ROAD_ISLAND_MM2 = 0.8         # traffic islands smaller than this become road
MIN_BUILDING_ABS_MM2 = 0.06   # below this a building is never drawn, whatever block it belongs to
CONTOUR_MM = 0.5              # width of an engraved contour line
PATH_KINDS = {"footway", "path", "cycleway", "steps", "bridleway", "track", "pedestrian"}
WATER_REACH_M = 150.0         # the water level at a point is never above the lowest ground this far along the water
LABEL_DEPTH_MM = 0.6
SINGLE = "_single"            # key of the one-piece twin kept beside the colour parts

Progress = Callable[[float, str], None]


@dataclass
class BuildResult:
    params: ModelParams
    parts: dict[str, Any]                         # name -> Manifold (whole model)
    tiles: list[dict[str, Any]] = field(default_factory=list)   # [{id, row, col, box, parts: {name: Manifold}}]
    Z: Optional[np.ndarray] = None                # terrain grid used (mm), for the thumbnail
    xs: Optional[np.ndarray] = None
    ys: Optional[np.ndarray] = None
    masks: dict[str, np.ndarray] = field(default_factory=dict)
    footprints: dict[str, Any] = field(default_factory=dict)   # 2D shapes in mm (buildings, water, roads, green, outline)
    stats: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    attribution: list[str] = field(default_factory=list)
    single: Any = None                            # the whole model as one closed body (the STL)


class _Clock:
    def __init__(self, progress: Optional[Progress], cancelled: Optional[Callable[[], bool]]):
        self.progress = progress
        self.cancelled = cancelled
        self.t0 = time.monotonic()
        self.times: dict[str, float] = {}
        self._last = self.t0
        self._stage = ""

    def step(self, fraction: float, message: str) -> None:
        if self.cancelled and self.cancelled():
            raise Cancelled()
        now = time.monotonic()
        if self._stage:
            self.times[self._stage] = round(now - self._last, 2)
        self._last = now
        self._stage = message
        if self.progress:
            self.progress(fraction, message)

    def log(self, message: str) -> None:
        if self.cancelled and self.cancelled():
            raise Cancelled()
        if self.progress:
            self.progress(-1.0, message)


def _gaussian(Z: np.ndarray, sigma: float) -> np.ndarray:
    if sigma <= 0.05:
        return Z
    from scipy.ndimage import gaussian_filter
    return gaussian_filter(Z, sigma=sigma, mode="nearest")


def _to_mm(geom: Any, k: float, w_m: float, h_m: float) -> Any:
    if geom is None or geom.is_empty:
        return None
    return affinity.affine_transform(geom, [k, 0.0, 0.0, k, w_m / 2.0 * k, h_m / 2.0 * k])


def _sample(Z: np.ndarray, xs: np.ndarray, ys: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Bilinear sample of the grid at model points (clamped to the grid)."""
    fx = np.clip((np.asarray(x) - xs[0]) / (xs[1] - xs[0]), 0, len(xs) - 1.000001)
    fy = np.clip((np.asarray(y) - ys[0]) / (ys[1] - ys[0]), 0, len(ys) - 1.000001)
    j = fx.astype(np.int64)
    i = fy.astype(np.int64)
    tx, ty = fx - j, fy - i
    return (Z[i, j] * (1 - tx) * (1 - ty) + Z[i, j + 1] * tx * (1 - ty) + Z[i + 1, j] * (1 - tx) * ty + Z[i + 1, j + 1] * tx * ty)


def _inside(geom: Any, X: np.ndarray, Y: np.ndarray) -> np.ndarray:
    """Boolean grid: which grid points are inside ``geom`` (only the points in its bounding box are tested)."""
    out = np.zeros(X.shape, dtype=bool)
    if geom is None or geom.is_empty:
        return out
    minx, miny, maxx, maxy = geom.bounds
    sel = (X >= minx) & (X <= maxx) & (Y >= miny) & (Y <= maxy)
    if not sel.any():
        return out
    shapely.prepare(geom)
    out[sel] = shapely.contains_xy(geom, X[sel], Y[sel])
    return out


def _unpinch(geom: Any, d: float = 0.02) -> Any:
    """Open the shape by ``d`` mm: parts that touch at a single point (a pinch, which becomes an edge shared by four faces
    in the solid) come apart; nothing thicker than 2 d changes."""
    if geom is None or geom.is_empty:
        return geom
    out = geom.buffer(-d, join_style="mitre").buffer(d * 0.9, join_style="round", quad_segs=2)   # round: corners never meet again
    return out if not out.is_empty else None


def _padded(F: np.ndarray, xs: np.ndarray, ys: np.ndarray, pad: float = 1.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The grid with one more row/column on every side ``pad`` mm out (edge values), so shapes traced from it reach past
    the model's border instead of stopping on it (a wall exactly on the border would coincide with the terrain's)."""
    return (np.pad(F, 1, mode="edge"), np.concatenate([[xs[0] - pad], xs, [xs[-1] + pad]]),
            np.concatenate([[ys[0] - pad], ys, [ys[-1] + pad]]))


def _below(F: np.ndarray, xs: np.ndarray, ys: np.ndarray, level: float) -> Any:
    """Polygons where the grid is below ``level``, traced with marching squares (interpolated, not cell by cell)."""
    import contourpy
    F, xs, ys = _padded(F, xs, ys)
    lo = float(np.nanmin(F))
    if lo >= level:
        return None
    gen = contourpy.contour_generator(xs, ys, F, fill_type=contourpy.FillType.OuterOffset)
    points, offsets = gen.filled(lo - 1.0, level)
    polys = []
    for pts, offs in zip(points, offsets):
        rings = [pts[offs[i]:offs[i + 1]] for i in range(len(offs) - 1)]
        rings = [r for r in rings if len(r) >= 4]
        if rings:
            poly = Polygon(rings[0], rings[1:]).buffer(0)
            if not poly.is_empty:
                polys.append(poly)
    if not polys:
        return None
    out = shapely.union_all(np.array(polys, dtype=object)).simplify(0.02)
    return out if not out.is_empty else None


def _contour_lines(F: np.ndarray, xs: np.ndarray, ys: np.ndarray, levels: list[float]) -> list[Any]:
    """Contour lines of the grid at ``levels`` as shapely lines (marching squares)."""
    import contourpy
    F, xs, ys = _padded(F, xs, ys)
    gen = contourpy.contour_generator(xs, ys, F, line_type=contourpy.LineType.Separate)
    out = []
    for level in levels:
        for seg in gen.lines(level):
            if len(seg) >= 2:
                line = LineString(seg).simplify(0.03)
                if line.length > 0.5:
                    out.append(line)
    return out


def _shifted(solid: Any, dz: float, W: float, H: float, grow: float) -> Any:
    """A copy of the terrain moved ``dz`` up and stretched ``grow`` mm out on every side (negative: shrunk in), so its walls
    never sit on the original's walls."""
    return solid.translate((-W / 2.0, -H / 2.0, 0.0)).scale(((W + 2 * grow) / W, (H + 2 * grow) / H, 1.0)).translate(
        (W / 2.0, H / 2.0, dz))


def _water_levels(Z0: np.ndarray, wet: np.ndarray, cell_mm: float, reach_mm: float, flat_mm: float = 0.25) -> tuple[np.ndarray, int]:
    """The water surface as a grid (mm), defined everywhere (outside the water it repeats the nearest water point).

    A river falls along its course, so one flat level per water body would sit above the ground downstream (or bury the
    banks upstream), and flat levels per piece leave terraces: straight steps across the river. Here, body by body, the
    level at each wet point is the lowest ground of that body within ``reach_mm`` (never above the nearby banks), smoothed
    along the water, then held under the ground right around it: a surface that slopes gently and has no steps. A body
    whose level hardly changes (lakes, reservoirs, ponds) stays exactly flat at its lowest point.
    Returns (levels, number of water bodies)."""
    from scipy import ndimage as ndi

    if not wet.any():
        return Z0.copy(), 0
    big = float(Z0.max()) + 1000.0
    L = np.full(Z0.shape, big)
    r = max(1, int(round(reach_mm / max(cell_mm, 1e-6))))
    sigma = max(1.0, r / 2.0)
    m = r + int(3 * sigma) + 2
    lab, n = ndi.label(wet, structure=np.ones((3, 3)))
    for body, sl in enumerate(ndi.find_objects(lab), start=1):
        if sl is None:
            continue
        ys_ = slice(max(0, sl[0].start - m), min(Z0.shape[0], sl[0].stop + m))
        xs_ = slice(max(0, sl[1].start - m), min(Z0.shape[1], sl[1].stop + m))
        mine = lab[ys_, xs_] == body
        G = np.where(mine, Z0[ys_, xs_], big)
        near = ndi.minimum_filter(G, size=5)
        lo = float(G[mine].min())
        if float(near[mine].max()) - lo <= flat_mm:
            level = np.full(G.shape, lo)
        else:
            low = ndi.minimum_filter(G, size=2 * r + 1)
            w = ndi.gaussian_filter(mine.astype(np.float64), sigma)
            smooth = ndi.gaussian_filter(np.where(mine, low, 0.0), sigma) / np.maximum(w, 1e-9)
            level = np.minimum(smooth, near)
            if float(level[mine].max() - level[mine].min()) <= flat_mm:
                level = np.full(G.shape, float(level[mine].min()))
        L[ys_, xs_] = np.where(mine, level, L[ys_, xs_])
    _d, (iy, ix) = ndi.distance_transform_edt(~wet, return_indices=True)
    return L[iy, ix], n


def _close(geom: Any, gap: float, min_hole: float) -> Any:
    """Morphological closing (gaps narrower than ``2 gap`` filled) and holes smaller than ``min_hole`` mm² filled."""
    if geom is None or geom.is_empty:
        return geom
    out = geom.buffer(gap, quad_segs=3).buffer(-gap, quad_segs=3)
    polys = []
    for poly in S.polygons(out):
        holes = [h for h in poly.interiors if Polygon(h).area >= min_hole]
        polys.append(Polygon(poly.exterior, holes))
    return shapely.union_all(np.array(polys, dtype=object)) if polys else out


def _supported(i: int, cands: list[tuple[Any, Any]], tree: Any, min_height_m: float) -> bool:
    """Whether a building part that starts ``min_height_m`` up stands on other parts at least that tall (else it is drawn
    down to the ground instead of floating)."""
    shape = cands[i][1]
    under = [j for j in tree.query(shape, predicate="intersects") if j != i and cands[j][0].height_m >= min_height_m - 0.5]
    if not under:
        return False
    covered = shapely.union_all(np.array([cands[j][1] for j in under], dtype=object)).intersection(shape).area
    return covered >= 0.5 * shape.area


def _line_buffer(lines: list[tuple[Any, float]]) -> Any:
    """Union of lines buffered by their half widths (flat caps would leave gaps at joints, so round joins and caps)."""
    if not lines:
        return None
    pieces = [line.buffer(width / 2.0, quad_segs=3) for line, width in lines if line is not None and not line.is_empty]
    if not pieces:
        return None
    return shapely.union_all(np.array(pieces, dtype=object))


def build(params: ModelParams, sources: Any, *, progress: Optional[Progress] = None, cancelled: Optional[Callable[[], bool]] = None,
          name: str = "") -> BuildResult:
    clock = _Clock(progress, cancelled)
    p = params
    area = p.area
    k = p.scale_mm_per_m
    W_mm, H_mm = p.model_size_mm
    notes: list[str] = []
    attribution: list[str] = []

    cells = p.estimated_cells()
    if cells > MAX_CELLS:
        need = p.resolution_mm * math.sqrt(cells / MAX_CELLS) * 1.02
        raise GulliverError("too_large", f"The terrain mesh would have {cells:,} cells (limit {MAX_CELLS:,}).",
                            f"Raise resolution_mm to at least {need:.2f} or make the model smaller.", cells=cells)

    # 1. elevation --------------------------------------------------------------------------------------------------------
    clock.step(0.02, "Elevation")
    pad_m = 3 * p.cell_ground_m + 30.0
    dem = sources.dem(area.bbox_padded(pad_m), p.cell_ground_m, prefer=p.dem, progress=clock.log)
    attribution.append(dem.attribution)
    notes.extend(dem.notes)
    nx = max(3, int(round(W_mm / p.resolution_mm)) + 1)
    ny = max(3, int(round(H_mm / p.resolution_mm)) + 1)
    xs = np.linspace(0.0, W_mm, nx)
    ys = np.linspace(0.0, H_mm, ny)
    X, Y = np.meshgrid(xs, ys)
    proj = area.projector
    lon, lat = proj.inv(X / k - area.width_m / 2.0, Y / k - area.height_m / 2.0)
    elev = dem.sample(lon.ravel(), lat.ravel()).reshape(ny, nx).astype(np.float64)
    cell_m = p.cell_ground_m
    sigma = p.smooth if p.smooth >= 0 else min(6.0, max(0.0, 0.45 * dem.res_m / cell_m))
    elev = _gaussian(elev, sigma)

    # sea: at or below 0 m and touching the border
    sea = np.zeros_like(elev, dtype=bool)
    if p.sea and p.water and (elev <= 0.3).any():
        from scipy.ndimage import label
        low = elev <= 0.3
        lab, _n = label(low)
        border = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
        if border:
            sea = np.isin(lab, list(border))
    land = elev[~sea] if (~sea).any() else elev.ravel()
    ref = 0.0 if sea.any() else float(np.nanmin(land))
    relief_m = float(np.nanmax(land) - np.nanmin(land)) if land.size else 0.0
    kz = k * p.z_exag
    span_mm = (float(np.nanmax(land)) - ref) * kz if land.size else 0.0
    cap_mm = p.max_relief_ratio * max(W_mm, H_mm)
    if span_mm > cap_mm > 0:                      # a mountain range drawn at the city exaggeration would be a forest of needles
        kz *= cap_mm / span_mm
        notes.append(f"Vertical exaggeration lowered to x{kz / k:.2f} so the relief stays within "
                     f"{p.max_relief_ratio:.0%} of the model's width.")
    base = p.base_mm
    if p.water and base < p.water_depth_mm + 1.0:
        base = p.water_depth_mm + 1.0
        notes.append(f"Base raised to {base:.1f} mm so water fits above the bottom.")
    Z0 = base + (np.maximum(elev, ref) - ref) * kz            # bare terrain, before any engraving
    Z = Z0.copy()
    clock.log(f"Relief {np.nanmin(land):.0f}-{np.nanmax(land):.0f} m -> {Z0.min():.1f}-{Z0.max():.1f} mm, smoothing {sigma:.1f} cells")

    # 2. OpenStreetMap ----------------------------------------------------------------------------------------------------
    needs_osm = p.buildings or p.water or p.waterways or p.roads != "none" or p.rails or p.green
    feats = osm_mod.Features()
    if needs_osm:
        clock.step(0.15, "OpenStreetMap")
        raw = sources.osm(area.bbox_padded(pad_m), progress=clock.log, cancelled=cancelled)
        feats = osm_mod.parse(raw, proj, area.width_m, area.height_m, pad_m=pad_m)
        attribution.append(osm_mod.ATTRIBUTION)
        clock.log(f"{len(feats.buildings)} buildings, {len(feats.roads)} roads, {len(feats.waterways)} waterways, "
                  f"{len(feats.rails)} rails")
    building_source = "osm"
    if p.buildings and p.buildings_source != "osm" and hasattr(sources, "catastro"):
        try:
            parts = sources.catastro(area.bbox_padded(pad_m), progress=clock.log, cancelled=cancelled)
        except GulliverError as exc:
            parts = None
            notes.append(f"The cadastre did not answer ({exc.message}); buildings from OpenStreetMap.")
        if parts:
            clip_m = box(-area.width_m / 2 - pad_m, -area.height_m / 2 - pad_m, area.width_m / 2 + pad_m, area.height_m / 2 + pad_m)
            cad = catastro_mod.to_buildings(parts, proj, clip_m)
            if cad:
                feats.buildings = catastro_mod.merge(feats.buildings, cad)
                building_source = "cadastre"
                attribution.append(catastro_mod.ATTRIBUTION)
                clock.log(f"{len(cad)} building parts from the cadastre")
        elif p.buildings_source == "cadastre":
            notes.append("The cadastre has no buildings here (outside Spain, or Navarre / the Basque Country); OpenStreetMap's were used.")
    model_box = box(0.0, 0.0, W_mm, H_mm)
    footprint = S.outline(p.shape, W_mm, H_mm)
    to_mm = lambda g: _to_mm(g, k, area.width_m, area.height_m)  # noqa: E731

    # 3. roads, rails --------------------------------------------------------------------------------------------------
    clock.step(0.25, "Roads and contour lines")
    rank = p.effective_road_rank()
    road_lines, bridge_lines, rail_lines, path_lines = [], [], [], []
    for r in feats.roads:
        if r.tunnel or r.line is None:
            continue
        if r.rank > rank or p.roads == "none":
            if p.paths and r.kind in PATH_KINDS:
                path_lines.append((to_mm(r.line), max(r.width_m * k, p.min_path_mm)))
            continue
        width = max(r.width_m * k, p.min_road_mm)
        line = to_mm(r.line)
        road_lines.append((line, width))
        if r.bridge:
            bridge_lines.append((line, width, "road"))
    if p.rails:
        for rail in feats.rails:
            if rail.kind == "tram" or rail.line is None:      # trams run in the street
                continue
            line = to_mm(rail.line)
            width = max(rail.width_m * k, p.min_road_mm * 0.9)
            rail_lines.append((line, width))
            if rail.bridge:
                bridge_lines.append((line, width, "rail"))
    roads_geom = _line_buffer(road_lines) if road_lines else None
    if roads_geom is not None:
        roads_geom = roads_geom.intersection(model_box.buffer(1.0))
    road_shape = None
    if roads_geom is not None and not roads_geom.is_empty and p.roads != "none":
        # close the slivers of ground left between roads that nearly touch (they print as needles) and fill traffic islands
        # too small to print; then the outline is what is cut
        roads_geom = _close(roads_geom, ROAD_GAP_MM, ROAD_ISLAND_MM2).intersection(model_box.buffer(1.0))
        road_shape = _unpinch(roads_geom)
    road_mask = _inside(roads_geom, X, Y) if roads_geom is not None and p.roads != "none" else np.zeros_like(sea)
    rail_shape = None
    if rail_lines:
        rg = _line_buffer(rail_lines).intersection(model_box.buffer(1.0))
        rail_shape = _unpinch(rg) if not rg.is_empty else None
    green_mm = to_mm(feats.green).intersection(model_box) if feats.green is not None else None
    path_shape = None
    # in a town the paths are the ones inside parks and woods; on open land (meadows) every trail is drawn, over the rock
    # and the snow too
    walk_area = model_box if p.meadows else green_mm
    if path_lines and walk_area is not None and not walk_area.is_empty:
        pg = _line_buffer(path_lines).intersection(walk_area if p.meadows else walk_area.buffer(0.3))
        if road_shape is not None:
            pg = pg.difference(road_shape.buffer(0.1))
        path_shape = _unpinch(pg) if not pg.is_empty else None

    # sea outline traced from the elevation (low pockets inland are not sea)
    sea_poly = None
    if sea.any():
        F = np.where((elev <= 0.3) & ~sea, 1.0, elev)
        sea_poly = _below(F, xs, ys, 0.3)
        sea_poly = _unpinch(sea_poly) if sea_poly is not None else None

    # water geometry (areas + rivers drawn as lines)
    water_geom = None
    if p.water or p.waterways:
        pieces = []
        if p.water and feats.water is not None:
            pieces.append(to_mm(feats.water))
        if p.waterways and feats.waterways:
            lines = [(to_mm(line), max(w * k, 0.6)) for line, w, kind in feats.waterways
                     if kind in ("river", "canal") or w * k >= 0.35]
            buf = _line_buffer(lines)
            if buf is not None:
                pieces.append(buf)
        if pieces:
            water_geom = shapely.union_all(np.array(pieces, dtype=object)).intersection(model_box.buffer(1.0))
            water_geom = _unpinch(water_geom) if not water_geom.is_empty else None
    water_mask = _inside(water_geom, X, Y) | sea

    # contour lines: traced from the elevation as lines, engraved below as a cut that follows them exactly
    interval = p.contour_interval(relief_m)
    contour_mask = np.zeros_like(sea)          # only for the preview picture
    contour_shape = None
    if interval > 0:
        c = np.floor((elev - ref) / interval)
        edge = np.zeros_like(sea)
        edge[:, :-1] |= c[:, :-1] != c[:, 1:]
        edge[:-1, :] |= c[:-1, :] != c[1:, :]
        contour_mask = edge & ~water_mask & ~road_mask
        hi = float(np.nanmax(elev))
        levels = [ref + n * interval for n in range(1, int((hi - ref) / interval) + 1)]
        lines = _contour_lines(elev, xs, ys, levels)
        if lines:
            contour_shape = shapely.union_all(np.array([l.buffer(CONTOUR_MM / 2.0, quad_segs=2) for l in lines], dtype=object))
            if road_shape is not None and p.roads == "raised":
                contour_shape = contour_shape.difference(road_shape.buffer(0.1))
            if rail_shape is not None:
                contour_shape = contour_shape.difference(rail_shape.buffer(0.1))
            contour_shape = _unpinch(contour_shape) if not contour_shape.is_empty else None
        clock.log(f"Contour lines every {interval:g} m")

    # 4. terrain solid --------------------------------------------------------------------------------------------------
    # roads and contour lines are cut from the solid along their real outlines (never stepped along the grid): the cut is
    # the outline's prism above a copy of the terrain lowered by the depth, so the floor follows the ground
    clock.step(0.35, "Terrain solid")
    terrain = S.heightfield(Z, xs, ys)
    top = float(Z.max())
    over_top = top + max(p.road_depth_mm, p.rail_height_mm) + 5.0
    raises = []
    if road_shape is not None and p.roads == "raised":
        raised = _shifted(terrain, p.road_depth_mm, W_mm, H_mm, -0.05)
        raises.append(S.intersect(S.prism(road_shape, 0.3, over_top), raised))
    if rail_shape is not None:                 # railway beds stand on the ground
        bed = _shifted(terrain, p.rail_height_mm, W_mm, H_mm, -0.05)
        raises.append(S.intersect(S.prism(rail_shape, 0.25, over_top), bed))
    if raises:
        terrain = S.union([terrain] + raises)
    relief_cuts = []
    if path_shape is not None and p.color == "mono":     # in one colour the park paths are shallow grooves
        relief_cuts.append(S.subtract(S.prism(path_shape, -1.6, over_top),
                                      [_shifted(terrain, -min(0.25, p.road_depth_mm), W_mm, H_mm, 0.05)]))
    if road_shape is not None and p.roads == "engraved":
        relief_cuts.append(S.subtract(S.prism(road_shape, -1.0, over_top), [_shifted(terrain, -p.road_depth_mm, W_mm, H_mm, 0.05)]))
    if contour_shape is not None:
        relief_cuts.append(S.subtract(S.prism(contour_shape, -1.3, over_top),
                                      [_shifted(terrain, -p.contour_depth_mm, W_mm, H_mm, 0.05)]))
    if relief_cuts:
        terrain = S.subtract(terrain, relief_cuts)
    top = max(top, terrain.bounding_box()[5])

    # 5. water: one continuous surface ------------------------------------------------------------------------------------
    # The level is a grid (see _water_levels): rivers slope gently with no terraces, lakes stay flat. The cut is the water
    # outline's prism above the floor surface (level − depth), so the walls of the cut are the real banks; the water part
    # fills from that floor up to the level. Both surfaces are heightfields on a copy of the grid padded past the outline,
    # so none of their walls meets another solid's wall.
    water_bodies = 0
    level_grid = None
    cut = S.EMPTY
    water_solid = S.EMPTY
    if water_geom is not None:
        clock.step(0.45, "Water")
        wet = _inside(water_geom.buffer(p.resolution_mm * 0.75), X, Y)
        level_grid, water_bodies = _water_levels(Z0, wet, p.resolution_mm, WATER_REACH_M * k)
        minx, miny, maxx, maxy = water_geom.bounds
        pad = 2 * p.resolution_mm
        jx = np.where((xs >= minx - pad) & (xs <= maxx + pad))[0]
        iy = np.where((ys >= miny - pad) & (ys <= maxy + pad))[0]
        if len(jx) >= 2 and len(iy) >= 2:
            sub = (slice(iy[0], iy[-1] + 1), slice(jx[0], jx[-1] + 1))
            floor_g = np.maximum(level_grid[sub] - p.water_depth_mm, 0.6)
            top_g = np.maximum(level_grid[sub], floor_g + 0.2)
            fz, fx, fy = _padded(floor_g, xs[jx[0]:jx[-1] + 1], ys[iy[0]:iy[-1] + 1], pad=1.5)
            tz, _tx, _ty = _padded(top_g, xs[jx[0]:jx[-1] + 1], ys[iy[0]:iy[-1] + 1], pad=1.5)
            below_floor = S.heightfield(fz, fx, fy)
            below_top = S.heightfield(tz, fx, fy)
            gfloor = float(floor_g.min()) - 0.5
            outline_prism = S.prism(water_geom, gfloor, over_top + 5.0)
            cut = S.subtract(outline_prism, [below_floor])
            water_solid = S.subtract(S.intersect(outline_prism, below_top), [below_floor])
    if sea_poly is not None:
        # the sea: cut to a flat floor along the traced coastline (which reaches past the border, see _padded)
        cut = S.union([cut, S.prism(sea_poly, base - p.water_depth_mm, over_top + 5.0)])
        inside = sea_poly.intersection(model_box)
        if not inside.is_empty:
            water_solid = S.union([water_solid, S.prism(inside, base - p.water_depth_mm, base)])
            water_geom = inside if water_geom is None else water_geom.union(inside)
    if not water_solid.is_empty():
        water_solid = S.intersect(water_solid, S.prism(model_box, -1.0, over_top + 10.0))
    terrain_wet = S.subtract(terrain, [cut]) if not cut.is_empty() else terrain

    # 6. bridges: over water a deck carried on piers and round arches (or a solid causeway when arches are off); off the
    # water (over a street, a railway, a ravine) the elevation model has no bridge, only the dip under it, so the deck is
    # drawn when the ground under it falls far enough below its ends, on piers and arches standing on that ground
    spans: list[tuple[Any, Any, float, float, float, bool]] = []   # (line, deck shape, width, deck, level under it, wet)
    if p.bridges and bridge_lines:
        for line, width, kind in bridge_lines:
            if line is None or line.is_empty:
                continue
            wet_span = water_geom is not None and line.intersects(water_geom)
            ends = np.array([line.coords[0], line.coords[-1]])
            deck = float(_sample(Z, xs, ys, ends[:, 0], ends[:, 1]).max())
            if kind == "rail":
                deck += p.rail_height_mm
            elif p.roads == "raised":
                deck += p.road_depth_mm
            elif p.roads == "engraved":
                deck -= p.road_depth_mm      # the deck carries on the groove of the road
            if wet_span:
                over = line.intersection(water_geom)
                probe = detail.along([g for g in getattr(over, "geoms", [over]) if g.geom_type == "LineString"], 0.5)
                surface = level_grid if level_grid is not None else Z0
                level = float(_sample(surface, xs, ys, probe[:, 0], probe[:, 1]).min()) if len(probe) else base
            else:
                probe = detail.along([line.intersection(model_box)], 0.25)
                if not len(probe):
                    continue
                level = float(_sample(Z, xs, ys, probe[:, 0], probe[:, 1]).min())
                if deck - OVERPASS_DECK_MM - level < OVERPASS_DIP_MM:
                    continue                 # the ground fills it: the road just runs over it
            shape = line.buffer(width / 2.0 + 0.15, cap_style="flat").intersection(model_box)
            if not shape.is_empty:
                spans.append((line, shape, width + 0.3, deck, level, wet_span))
    # bridges that touch share one deck height (two decks a hair apart would leave a sliver step between them)
    group = list(range(len(spans)))
    def root(i: int) -> int:
        while group[i] != i:
            group[i] = group[group[i]]
            i = group[i]
        return i
    for i in range(len(spans)):
        for j in range(i + 1, len(spans)):
            if spans[i][1].intersects(spans[j][1]):
                group[root(i)] = root(j)
    top_of: dict[int, float] = {}
    for i, span in enumerate(spans):
        top_of[root(i)] = max(top_of.get(root(i), span[3]), span[3])
    bridges = []
    arch_span = min(10.0, max(3.0, 28.0 * k))
    arched = 0
    for i, (line, shape, width, _deck, level, wet_span) in enumerate(spans):
        deck = top_of[root(i)]
        if not wet_span and (not p.arches or deck - OVERPASS_DECK_MM - level < 0.3):
            bridges.append(S.prism(shape, deck - OVERPASS_DECK_MM, deck))   # a plain deck on its ends: the road under stays open
        elif not wet_span:
            clipped = line.intersection(model_box.buffer(-0.01))
            for piece in [g for g in getattr(clipped, "geoms", [clipped]) if g.geom_type == "LineString" and g.length > 0.2]:
                solid = detail.arch_bridge(piece, width, deck, OVERPASS_DECK_MM, level, 0.3, arch_span)
                bridges.append(S.intersect(solid, S.prism(model_box, -1.0, deck + 1.0)))
        elif p.arches:
            clipped = line.intersection(model_box.buffer(-0.01))
            pieces = [g for g in getattr(clipped, "geoms", [clipped]) if g.geom_type == "LineString" and g.length > 0.2]
            for piece in pieces:
                solid = detail.arch_bridge(piece, width, deck, BRIDGE_DECK_MM, level, 0.3, arch_span)
                bridges.append(S.intersect(solid, S.prism(model_box, -1.0, deck + 1.0)))
            arched += 1 if deck - BRIDGE_DECK_MM - level >= 0.3 else 0
        else:
            bridges.append(S.prism(shape, 0.0, deck))
    overpasses = sum(1 for sp in spans if not sp[5])

    # weirs and dams: a low wall across the water (the level around it stays as it is), in the terrain's colour
    weir_shapes = []
    if water_geom is not None and level_grid is not None and feats.weirs:
        reach = water_geom.buffer(0.3)
        for geom, kind in feats.weirs:
            narrow, real_w, above = WEIR[kind]
            g = to_mm(geom)
            if g is None or g.is_empty:
                continue
            band = g if g.geom_type in ("Polygon", "MultiPolygon") else g.buffer(max(narrow, real_w * k) / 2.0, cap_style="flat")
            band = band.intersection(reach).intersection(model_box)
            if band.is_empty or band.area < 0.05 or not band.intersects(water_geom):
                continue
            inner = band.intersection(water_geom)
            pts = detail.along([q.exterior for q in S.polygons(inner)], 0.2)
            if not len(pts):
                continue
            lv = _sample(level_grid, xs, ys, pts[:, 0], pts[:, 1])
            floor = max(float(lv.min()) - p.water_depth_mm - 0.1, 0.4)
            bridges.append(S.prism(band, floor, float(lv.max()) + above))
            weir_shapes.append(band)
    bridge_solid = S.union(bridges)
    if not bridge_solid.is_empty():
        water_solid = S.subtract(water_solid, [bridge_solid])

    # 7. buildings ----------------------------------------------------------------------------------------------------------
    building_solids = []
    building_shapes = []
    dropped = 0
    sink = max(0.3, p.road_depth_mm + 0.1, p.contour_depth_mm + 0.1)   # below any groove next to the walls
    roofs = 0
    if p.buildings and feats.buildings:
        clock.step(0.55, "Buildings")
        cands = []
        for bld in feats.buildings:
            shape = to_mm(bld.shape)
            if shape is None:
                continue
            shape = shape.intersection(model_box)
            if shape.is_empty or shape.area < MIN_BUILDING_ABS_MM2:
                dropped += 1
                continue
            cands.append((bld, shape))
        # a small building is kept when it belongs to a block of touching buildings big enough to print (a row of
        # terraced houses), and dropped only when it stands alone
        block_area = np.zeros(len(cands))
        if cands:
            blocks = [g for g in S.polygons(shapely.union_all(np.array([c[1].buffer(0.05) for c in cands], dtype=object)))]
            tree = shapely.STRtree(blocks)
            pts = [c[1].representative_point() for c in cands]
            hit_in, hit_block = tree.query(pts, predicate="within")
            block_area[hit_in] = np.array([blocks[j].area for j in hit_block])
        tops_tree = shapely.STRtree([c[1] for c in cands]) if cands else None
        for i, (bld, shape) in enumerate(cands):
            tall = bld.height_m * k * p.building_exag >= 1.5 and shape.area >= 0.12      # a tower or a spire stays
            if shape.area < p.min_building_mm2 and block_area[i] < 2.0 * p.min_building_mm2 and not tall:
                dropped += 1
                continue
            shape = shape.simplify(0.03).buffer(0.02, join_style="mitre")   # neighbours overlap instead of touching
            if shape.is_empty:
                continue
            if water_geom is not None and water_geom.contains(shape.representative_point()):
                continue
            coords = np.array([c for poly in S.polygons(shape) for c in poly.exterior.coords])
            ground = _sample(Z, xs, ys, coords[:, 0], coords[:, 1])
            kv = k * p.building_exag
            z0 = max(float(ground.min()) - sink, 0.4)
            if bld.part and bld.min_height_m > 2.0 and _supported(i, cands, tops_tree, bld.min_height_m):
                z0 = float(ground.max()) + bld.min_height_m * kv - 0.02      # a spire on its tower, a dome on its drum
                z1 = float(ground.max()) + max(bld.height_m * kv, bld.min_height_m * kv + p.min_building_height_mm)
            else:
                z1 = float(ground.max()) + max((bld.height_m - (bld.min_height_m if bld.part else 0.0)) * kv,
                                               p.min_building_height_mm)
            roof = detail.default_roof(bld.kind, bld.roof, p.roofs)
            roof_h = bld.roof_height_m * kv if bld.roof_height_m is not None else None
            if roof in detail.POINTED and bld.part and bld.min_height_m > 2.0 and roof_h is None:
                roof_h = z1 - z0                                # the whole part is the spire
            solid = detail.building_solid(shape, z0, z1, roof, roof_h, bld.roof_across, kv) if p.roofs != "flat" else \
                S.prism(shape, z0, z1)
            roofs += 1 if roof not in ("", "flat") else 0
            building_solids.append(solid)
            building_shapes.append(shape)
        clock.log(f"{len(building_solids)} buildings on the model ({roofs} with a roof), {dropped} too small")
    buildings = S.union(building_solids)

    # 7b. what covers a mountain, read from the aerial photo (Spain): bare rock, meadow and woods where they really are,
    # instead of guessing the rock from the slope alone (a steep meadow is green, a gentle limestone plateau is rock)
    cover = None
    photo_woods = None
    if p.ground_from_photo and p.zones and relief_m >= 60.0 and hasattr(sources, "aerial_cover"):
        try:
            got = sources.aerial_cover(area.bbox, max(2.0, cell_m / 2.0), progress=clock.log)
        except GulliverError as exc:
            got = None
            notes.append(f"The aerial photo did not load ({exc.message}); rock from the slope only.")
        if got:
            masks, (b0, b1, b2, b3) = got
            h_px, w_px = next(iter(masks.values())).shape
            glon, glat = proj.inv(X / k - area.width_m / 2.0, Y / k - area.height_m / 2.0)
            col = np.clip(((np.asarray(glon) - b0) / (b2 - b0) * w_px).astype(int), 0, w_px - 1)
            row = np.clip(((b3 - np.asarray(glat)) / (b3 - b1) * h_px).astype(int), 0, h_px - 1)
            cover = {name: m[row, col] for name, m in masks.items()}
            attribution.append(aerial_mod.COVER_ATTRIBUTION)
            if p.trees and cover["wood"].any():
                wood = _below(-_gaussian(cover["wood"].astype(float), 1.0), xs, ys, -0.5)
                if wood is not None:
                    keep = [q for q in S.polygons(wood) if q.area >= 4.0 * p.tree_size_mm ** 2]
                    photo_woods = shapely.union_all(np.array(keep, dtype=object)) if keep else None
            clock.log("Rock, meadows and woods read from the aerial photo")

    # 8. trees ---------------------------------------------------------------------------------------------------------------
    trees_solid = S.EMPTY
    n_trees = 0
    P, R = np.zeros((0, 2)), np.zeros(0)
    seen_from_air = False
    if p.trees and (feats.trees or feats.tree_rows or feats.woods is not None or feats.parks is not None or photo_woods is not None):
        clock.step(0.62, "Trees")
        crown = p.tree_size_mm
        mapped = []
        for x, y, h_m, crown_m in feats.trees:
            pt = to_mm(Point(x, y))
            if pt is not None:
                mapped.append((pt.x, pt.y, max(crown_m or h_m * 0.55 or 6.0, 3.0) * k))
        seen_from_air = False
        # single trees from the aerial photo only where a tree is still a tree on the model (finer than about 1:15.000);
        # further out a crown would be smaller than the smallest printable one and woods are filled instead
        if p.tree_source == "auto" and hasattr(sources, "aerial_trees") and crown / k <= 16.0:
            try:
                air = sources.aerial_trees(area.bbox, proj, progress=clock.log)
            except GulliverError as exc:
                air = None
                notes.append(f"The aerial photo did not load ({exc.message}); trees from OpenStreetMap only.")
            if air:
                seen_from_air = True
                attribution.append(aerial_mod.ATTRIBUTION)
                known = np.array([(m[0], m[1]) for m in mapped]) if mapped else np.zeros((0, 2))
                added = 0
                for x, y, h_m, crown_m in air:
                    pt = to_mm(Point(x, y))
                    if pt is None:
                        continue
                    if len(known) and np.min(np.hypot(known[:, 0] - pt.x, known[:, 1] - pt.y)) < max(crown_m * k, crown) * 0.6:
                        continue                                 # already mapped
                    mapped.append((pt.x, pt.y, crown_m * k))
                    added += 1
                clock.log(f"{added} trees seen on the aerial photo")
        house_shapes = shapely.union_all(np.array(building_shapes, dtype=object)) if building_shapes else None
        light = [g for g in (house_shapes, water_geom, rail_shape) if g is not None]
        blockers = light + [g for g in (road_shape, path_shape) if g is not None]
        blocked = shapely.union_all(np.array([g.buffer(crown * 0.3) for g in blockers], dtype=object)) if blockers else None
        blocked_light = shapely.union_all(np.array([g.buffer(crown * 0.2) for g in light], dtype=object)) if light else None
        clip = lambda g: g.intersection(model_box) if g is not None and not g.is_empty else None  # noqa: E731
        seed = int(abs(p.lon * 1e5) + abs(p.lat * 1e5)) % (2 ** 32)
        woods_mm = to_mm(feats.woods) if feats.woods is not None else None
        if photo_woods is not None:
            woods_mm = photo_woods if woods_mm is None else woods_mm.union(photo_woods)
        P, R = detail.plant(woods=clip(woods_mm), parks=clip(to_mm(feats.parks)), mapped=mapped,
                            rows=[to_mm(l) for l in feats.tree_rows], blocked=blocked, blocked_light=blocked_light,
                            inside=footprint.buffer(-crown * 0.6), crown_mm=crown,
                            density=p.tree_density, rng=np.random.default_rng(seed),
                            scatter_parks=not seen_from_air, scatter_woods=not seen_from_air)
        trees_solid = detail.trees_solid(P, R, lambda x, y: _sample(Z, xs, ys, x, y), sink=0.3 + p.road_depth_mm)
        n_trees = len(P)
        clock.log(f"{n_trees} trees")

    # 9. colour layers on the terrain ----------------------------------------------------------------------------------------
    clock.step(0.68, "Parts")
    solid_top = max([top, trees_solid.bounding_box()[5] if not trees_solid.is_empty() else top]
                    + [s.bounding_box()[5] for s in building_solids]) + 5.0
    terrain_body = S.union([terrain_wet, bridge_solid])
    parts: dict[str, Any] = {}
    layers_drawn: list[str] = []
    zone_shapes = {}
    if p.color == "color":
        # the single-piece twin (for the STL): the same solid, never split, so it stays one closed body
        parts[SINGLE] = S.union([terrain_body, buildings, trees_solid])
        ground_all = terrain_body
        # a colour layer is the top SKIN_MM of the terrain inside its outline: cutter = outline prism above the lowered
        # terrain; layer = terrain ∩ cutter and terrain -= cutter (complementary, never two coincident top faces)
        lowered = terrain_body.translate((0.0, 0.0, -SKIN_MM))
        wet = water_geom.buffer(0.05) if water_geom is not None else None   # layers stop just short of the banks
        terrain_body = S.subtract(terrain_body, [buildings])               # once, before the layers are taken off it

        def on_relief(shape: Any, kind: str) -> Any:
            """A road or railway layer: just inside its groove/bed walls (a wall of the layer on a wall of the groove
            would be two coincident faces), off the water but on its bridges."""
            if shape is None:
                return None
            out = shape.buffer(-0.03, join_style="mitre").intersection(model_box)
            if wet is not None:
                over = [line.buffer(width / 2.0 - 0.03, quad_segs=3) for line, width, kd in bridge_lines if kd == kind] \
                    if p.bridges else []
                on_bridges = shapely.union_all(np.array(over, dtype=object)).intersection(water_geom.buffer(0.08)) if over else None
                out = out.difference(wet)
                if on_bridges is not None and not on_bridges.is_empty:
                    out = out.union(on_bridges)
            return _unpinch(out) if not out.is_empty else None

        areas: list[tuple[str, Any]] = [("roads", on_relief(road_shape, "road")), ("rails", on_relief(rail_shape, "rail"))]
        if p.zones:
            snow = []
            line_m = detail.snow_line(land, p.snow_m)
            if line_m is not None:
                snow.append(_below(-elev, xs, ys, -line_m))
            if feats.ice is not None:
                snow.append(to_mm(feats.ice))
            snow = [g for g in snow if g is not None and not g.is_empty]
            if snow:
                areas.append(("snow", shapely.union_all(np.array(snow, dtype=object))))
            rock = []
            if relief_m >= 60.0:
                slope = _gaussian(detail.slope_deg(elev, cell_m), 1.0)
                if cover is not None:
                    # rock where the photo shows bare stone (not on the flat floor of a valley), where the photo says
                    # nothing (shadow) and the slope is steep, and on any cliff
                    known = cover["veg"] | cover["dry"] | cover["bare"]
                    bare = (cover["bare"] & (slope >= 10.0)) | (~known & (slope >= p.rock_slope_deg)) | (slope >= 60.0)
                    rock.append(_below(-_gaussian(bare.astype(float), 1.0), xs, ys, -0.5))
                else:
                    rock.append(_below(-slope, xs, ys, -p.rock_slope_deg))
            if feats.rock is not None:
                rock.append(to_mm(feats.rock))
            rock = [g for g in rock if g is not None and not g.is_empty]
            if rock:
                areas.append(("rock", shapely.union_all(np.array(rock, dtype=object))))
        if p.green and (green_mm is not None or p.meadows):
            g = model_box if p.meadows else green_mm      # meadows: every open bit of land is green
            areas.append(("green", g))
        taken = None
        cutters = []
        zone_shapes: dict[str, Any] = {}
        # the layers stay a hair off the walls of the buildings standing on them (a layer cut by a wall whose corner sits
        # on the layer's edge would leave two bodies touching along that corner)
        houses = shapely.union_all(np.array(building_shapes, dtype=object)).buffer(0.03, join_style="mitre") \
            if building_shapes else None
        for name, area_geom in areas:
            if area_geom is None or area_geom.is_empty:
                continue
            g = area_geom.intersection(model_box)
            if name not in ("roads", "rails") and wet is not None:
                g = g.difference(wet)
            if houses is not None:
                g = g.difference(houses)
            if path_shape is not None and name in ("snow", "rock", "green"):
                g = g.difference(path_shape)          # the paths show the ground through the green, the rock and the snow
            if taken is not None:
                g = g.difference(taken.buffer(0.08))
            if name in ("snow", "rock"):              # no speckles: drop crumbs, smooth the edge
                g = shapely.union_all(np.array([q for q in S.polygons(g) if q.area >= 0.8] or [Polygon()], dtype=object))
            g = _unpinch(g) if not g.is_empty else None
            if g is None or g.is_empty:
                continue
            taken = g if taken is None else taken.union(g)
            cutter = S.subtract(S.prism(g, -1.0, solid_top), [lowered])
            parts[name] = S.intersect(terrain_body, cutter)
            zone_shapes[name] = g
            cutters.append(cutter)
            layers_drawn.append(name)
        terrain_body = S.subtract(terrain_body, cutters)
        parts["terrain"] = terrain_body
        parts["buildings"] = buildings
        parts["water"] = water_solid
        if not trees_solid.is_empty():
            parts["trees"] = S.subtract(trees_solid, [ground_all, buildings])
    else:
        parts["terrain"] = S.union([terrain_body, buildings, trees_solid])

    # 10. outline shape ----------------------------------------------------------------------------------------------------
    if p.shape != "rect":
        cutter = S.prism(footprint, -1.0, solid_top + 10.0)
        parts = {n: S.intersect(m, cutter) for n, m in parts.items()}

    # 11. frame --------------------------------------------------------------------------------------------------------------
    outer = footprint
    if p.frame_mm > 0:
        outer = footprint.buffer(p.frame_mm, join_style="mitre" if p.shape in ("rect", "hexagon") else "round")
        boundary = footprint.exterior
        pts = np.array([boundary.interpolate(t, normalized=True).coords[0] for t in np.linspace(0, 1, 400)])
        edge_z = _sample(Z0, xs, ys, np.clip(pts[:, 0], 0, W_mm), np.clip(pts[:, 1], 0, H_mm))
        frame_top = float(np.percentile(edge_z, 90)) + 1.0
        frame = S.prism(outer.difference(footprint), 0.0, frame_top)
        # for unions the frame reaches 0.05 mm into the model, so the two never meet on a coincident wall
        joined = S.prism(outer.difference(footprint.buffer(-0.05, join_style="mitre")), 0.0, frame_top)
        if p.color == "color":
            parts["frame"] = frame
            parts[SINGLE] = S.union([parts[SINGLE], joined])
        else:
            parts["terrain"] = S.union([parts["terrain"], joined])

    # 12. text on the bottom -------------------------------------------------------------------------------------------------
    bounds = outer.bounds
    tiles_n = p.tiles_x * p.tiles_y
    label_lines: list[str] = []
    cx, cy = footprint.centroid.x, footprint.centroid.y
    if p.label_bottom:
        label_lines = [l.strip() for l in (p.label.split("\n") if p.label.strip() else [name or "", format_coord(p.lon, p.lat),
                       f"{nice_scale(p.scale_denominator)} · Z x{p.z_exag:g}"]) if l.strip()]
    if label_lines:
        tw = (bounds[2] - bounds[0]) / (p.tiles_x if tiles_n > 1 else 1)
        th = (bounds[3] - bounds[1]) / (p.tiles_y if tiles_n > 1 else 1)
        height = max(2.5, min(7.0, min(tw, th) * 0.06))
        cx, cy = footprint.centroid.x, footprint.centroid.y
        if tiles_n > 1:  # on the tile that holds the centre, centred on that tile
            col = min(p.tiles_x - 1, int((cx - bounds[0]) / tw))
            row = min(p.tiles_y - 1, int((bounds[3] - cy) / th))
            cx = bounds[0] + (col + 0.5) * tw
            cy = bounds[3] - (row + 0.5) * th
        eng = S.text_engraving(label_lines, height, (cx, cy), LABEL_DEPTH_MM, max_width=min(tw, th) * 0.8)
        parts["terrain"] = S.subtract(parts["terrain"], [eng])
        if SINGLE in parts:
            parts[SINGLE] = S.subtract(parts[SINGLE], [eng])

    # 13. tiles and pins -------------------------------------------------------------------------------------------------------
    tiles: list[dict[str, Any]] = []
    pins: list[dict[str, Any]] = []
    if tiles_n > 1:
        clock.step(0.8, "Tiles")
        if p.pins:
            pin_z = max(p.pin_diameter_mm / 2.0 + 0.6, min(base * 0.45, base - p.water_depth_mm - p.pin_diameter_mm / 2.0 - 0.4))
            if pin_z + p.pin_diameter_mm / 2.0 > base - (p.water_depth_mm if p.water else 0) - 0.3:
                notes.append("The base is too thin for pin holes; they were left out (make the base thicker).")
            else:
                holes, pins = S.pin_holes(outer, bounds, p.tiles_x, p.tiles_y, diameter=p.pin_diameter_mm, depth=p.pin_depth_mm, z=pin_z)
                if not holes.is_empty():
                    parts = {n: S.subtract(m, [holes]) for n, m in parts.items()}
        for t in S.tile_boxes(bounds, p.tiles_x, p.tiles_y):
            x0, y0, x1, y1 = t["box"]
            cutter = S.prism(box(x0, y0, x1, y1), -1.0, solid_top + 10.0)
            tparts = {n: S.intersect(m, cutter) for n, m in parts.items()}
            tparts = {n: S.clean(m) for n, m in tparts.items()}
            tparts = {n: m for n, m in tparts.items() if not m.is_empty()}
            if not tparts:
                continue
            if p.tile_labels and "terrain" in tparts:
                tile_fp = outer.intersection(box(x0, y0, x1, y1))
                if not tile_fp.is_empty:
                    c = tile_fp.representative_point() if not tile_fp.contains(tile_fp.centroid) else tile_fp.centroid
                    off = 0.0 if not label_lines else min(y1 - y0, 40.0) * 0.32
                    has_label = label_lines and x0 <= cx <= x1 and y0 <= cy <= y1
                    tag = S.text_engraving([t["id"]], 4.0, (c.x, c.y - off if has_label else c.y), LABEL_DEPTH_MM,
                                           max_width=(x1 - x0) * 0.5)
                    tparts["terrain"] = S.subtract(tparts["terrain"], [tag])
                    if SINGLE in tparts:
                        tparts[SINGLE] = S.subtract(tparts[SINGLE], [tag])
            t["parts"] = tparts
            tiles.append(t)

    parts = {n: S.clean(m) for n, m in parts.items()}
    parts = {n: m for n, m in parts.items() if not m.is_empty()}
    clock.step(0.9, "Checks")
    single = parts.pop(SINGLE, None)
    for t in tiles:
        t["single"] = t["parts"].pop(SINGLE, None)
    whole = single if single is not None else S.union(list(parts.values()))
    bb = whole.bounding_box()
    volume = whole.volume()
    area_mm2 = whole.surface_area()
    shell = min(volume, area_mm2 * 0.9)
    grams = (shell + 0.15 * max(0.0, volume - shell)) * 1.24 / 1000.0
    stats = {
        "scale": nice_scale(p.scale_denominator),
        "scale_denominator": round(p.scale_denominator, 1),
        "size_mm": [round(bb[3] - bb[0], 1), round(bb[4] - bb[1], 1), round(bb[5] - bb[2], 1)],
        "ground_m": [round(area.width_m), round(area.height_m)],
        "area_km2": round(area.km2, 3),
        "relief_m": [round(float(np.nanmin(land)), 1), round(float(np.nanmax(land)), 1)],
        "dem": {"source": dem.source, "res_m": round(dem.res_m, 1), "smoothing_cells": round(sigma, 2)},
        "grid": [nx, ny],
        "triangles": int(sum(m.num_tri() for m in parts.values())),
        "volume_cm3": round(volume / 1000.0, 1),
        "weight_g_pla": round(grams, 0),
        "parts": {n: {"volume_cm3": round(m.volume() / 1000.0, 2), "triangles": m.num_tri(), "genus": m.genus()} for n, m in parts.items()},
        "buildings": len(building_solids),
        "buildings_dropped": dropped,
        "building_source": building_source,
        "roads_drawn": len(road_lines),
        "road_rank": rank,
        "water_pieces": water_bodies,
        "sea": bool(sea.any()),
        "contour_interval_m": interval,
        "bridges": len(spans),
        "overpasses": overpasses,
        "weirs": len(weir_shapes),
        "arched_bridges": arched,
        "rails_drawn": len(rail_lines),
        "paths_drawn": len(path_lines),
        "trees": n_trees,
        "trees_from_air": seen_from_air if p.trees else False,
        "ground_from_photo": cover is not None,
        "roofs": roofs,
        "colour_layers": layers_drawn,
        "tiles": [{"id": t["id"], "size_mm": [round(t["box"][2] - t["box"][0], 1), round(t["box"][3] - t["box"][1], 1)]} for t in tiles],
        "pins": len(pins),
        "features": feats.summary(),
        "timings_s": clock.times,
    }
    clock.step(0.92, "Done")
    masks = {"water": water_mask, "roads": road_mask & ~water_mask, "contours": contour_mask}
    footprints = {"outline": outer, "model": footprint, "buildings": building_shapes, "water": water_geom, "roads": roads_geom,
                  "green": green_mm, "rails": rail_shape, "paths": path_shape, "trees": (P, R) if n_trees else None,
                  "snow": zone_shapes.get("snow"), "rock": zone_shapes.get("rock"),
                  "bridges": shapely.union_all(np.array([sp[1] for sp in spans], dtype=object)) if spans else None,
                  "weirs": shapely.union_all(np.array(weir_shapes, dtype=object)) if weir_shapes else None}
    return BuildResult(p, parts, tiles, Z, xs, ys, masks, footprints, stats, notes, [a for a in attribution if a], whole)
