"""From parameters to solids: elevation + OpenStreetMap -> the parts of a printable model (terrain, buildings, water, roads,
green, frame), optionally cut into tiles with pin holes, every part a closed manifold.

Order of work: elevation grid (smoothed to the source's real resolution), sea from the elevation, roads / rails / contour
lines engraved or raised in the grid, the terrain solid, water cut into it at a flat level per piece (lakes stay flat, long
rivers step down), bridges over water, buildings standing on the lowest point of their footprint, then the outline shape,
the frame, the text on the bottom, the tiles and their pin holes. In colour mode the same solid is partitioned into parts
(one per filament) that fit each other exactly."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union

from ..errors import Cancelled, GulliverError
from ..geo import format_coord, nice_scale
from ..params import MAX_CELLS, ModelParams
from ..sources import osm as osm_mod
from . import solids as S

PART_COLORS = {
    "terrain": "#D8CCB4",
    "buildings": "#F2EEE6",
    "water": "#5E9FD1",
    "roads": "#8A8478",
    "green": "#7FA56A",
    "frame": "#3A3A3A",
}
PART_LABELS = {
    "terrain": ("Terreno", "Terrain"),
    "buildings": ("Edificios", "Buildings"),
    "water": ("Agua", "Water"),
    "roads": ("Carreteras", "Roads"),
    "green": ("Zonas verdes", "Green areas"),
    "frame": ("Marco", "Frame"),
}
SKIN_MM = 0.6                 # thickness of a colour layer on top of the terrain
WATER_PIECE_M = 300.0         # a water body longer than this is split so its level can step down
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


def _dilate(mask: np.ndarray, cells: int) -> np.ndarray:
    if cells <= 0:
        return mask
    from scipy.ndimage import binary_dilation
    return binary_dilation(mask, iterations=cells)


def _unpinch(geom: Any, d: float = 0.02) -> Any:
    """Open the shape by ``d`` mm: parts that touch at a single point (a pinch, which becomes an edge shared by four faces
    in the solid) come apart; nothing thicker than 2 d changes."""
    if geom is None or geom.is_empty:
        return geom
    out = geom.buffer(-d, join_style="mitre").buffer(d * 0.9, join_style="round", quad_segs=2)   # round: corners never meet again
    return out if not out.is_empty else None


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
    model_box = box(0.0, 0.0, W_mm, H_mm)
    footprint = S.outline(p.shape, W_mm, H_mm)
    to_mm = lambda g: _to_mm(g, k, area.width_m, area.height_m)  # noqa: E731

    # 3. roads, rails --------------------------------------------------------------------------------------------------
    clock.step(0.25, "Roads and contour lines")
    rank = p.effective_road_rank()
    road_lines, bridge_lines = [], []
    for r in feats.roads:
        if r.rank > rank or r.tunnel or r.line is None:
            continue
        width = max(r.width_m * k, p.min_road_mm)
        line = to_mm(r.line)
        road_lines.append((line, width))
        if r.bridge:
            bridge_lines.append((line, width))
    if p.rails and p.roads != "none":
        for rail in feats.rails:
            road_lines.append((to_mm(rail), max(4.0 * k, p.min_road_mm * 0.8)))
    roads_geom = _line_buffer(road_lines) if road_lines else None
    if roads_geom is not None:
        roads_geom = roads_geom.intersection(model_box.buffer(1.0))
    road_mask = _inside(roads_geom, X, Y) if roads_geom is not None and p.roads != "none" else np.zeros_like(sea)

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

    if p.roads in ("engraved", "raised") and road_mask.any():
        rm = road_mask & ~water_mask
        if p.roads == "engraved":
            Z = np.where(rm, np.maximum(Z - p.road_depth_mm, base * 0.5 + 0.3), Z)
        else:
            Z = np.where(rm, Z + p.road_depth_mm, Z)

    # contour lines
    interval = p.contour_interval(relief_m)
    contour_mask = np.zeros_like(sea)
    if interval > 0:
        c = np.floor((elev - ref) / interval)
        edge = np.zeros_like(sea)
        edge[:, :-1] |= c[:, :-1] != c[:, 1:]
        edge[:-1, :] |= c[:-1, :] != c[1:, :]
        contour_mask = _dilate(edge, max(0, int(round(0.45 / p.resolution_mm / 2.0)))) & ~water_mask & ~road_mask
        Z = np.where(contour_mask, np.maximum(Z - p.contour_depth_mm, base * 0.5 + 0.3), Z)
        clock.log(f"Contour lines every {interval:g} m")

    # sea surface and floor in the grid
    if sea.any():
        Z = np.where(sea, base - p.water_depth_mm, Z)

    # 4. terrain solid --------------------------------------------------------------------------------------------------
    clock.step(0.35, "Terrain solid")
    terrain = S.heightfield(Z, xs, ys)
    top = float(Z.max())

    # 5. water cut at a flat level per piece ---------------------------------------------------------------------------
    # One prism of the whole water outline, intersected with "slabs" that start at each piece's floor: the walls of the
    # cut are the real banks (never two coincident walls), and the floor steps down from piece to piece.
    water_levels: list[float] = []
    cut = S.EMPTY
    water_solid = S.EMPTY
    if water_geom is not None:
        clock.step(0.45, "Water")
        piece_mm = WATER_PIECE_M * k
        minx, miny, maxx, maxy = water_geom.bounds
        gx = max(1, int(math.ceil((maxx - minx) / piece_mm)))
        gy = max(1, int(math.ceil((maxy - miny) / piece_mm)))
        shapely.prepare(water_geom)
        up_slabs, level_slabs, floors = [], [], []
        for a in range(gx):
            for b in range(gy):
                cell = box(minx + a * piece_mm, miny + b * piece_mm, minx + (a + 1) * piece_mm, miny + (b + 1) * piece_mm)
                if not water_geom.intersects(cell):
                    continue
                for poly in S.polygons(water_geom.intersection(cell)):
                    if poly.area < 0.02:
                        continue
                    inside = _inside(poly, X, Y)
                    if inside.any():
                        level = float(Z0[inside].min())
                    else:
                        pt = poly.representative_point()
                        level = float(_sample(Z0, xs, ys, np.array([pt.x]), np.array([pt.y]))[0])
                    floor = max(level - p.water_depth_mm, 0.6)
                    slab = poly.buffer(0.5, join_style="mitre").intersection(cell)
                    up_slabs.append(S.prism(slab, floor, top + 5.0))
                    level_slabs.append(S.prism(slab, floor, max(level, floor + 0.2)))
                    floors.append(floor)
                    water_levels.append(level)
        if up_slabs:
            gfloor = min(floors) - 0.5
            outline_prism = S.prism(water_geom, gfloor, top + 5.0)
            cut = S.intersect(outline_prism, S.union(up_slabs))
            water_solid = S.intersect(outline_prism, S.union(level_slabs))
    if sea.any():
        sea_poly = S.mask_to_polygons(sea, xs[0] - (xs[1] - xs[0]) / 2, ys[0] - (ys[1] - ys[0]) / 2, xs[1] - xs[0])
        sea_poly = sea_poly.intersection(model_box)
        if not sea_poly.is_empty:
            water_solid = S.union([water_solid, S.prism(sea_poly, base - p.water_depth_mm, base)])
            water_geom = sea_poly if water_geom is None else water_geom.union(sea_poly)
    terrain_wet = S.subtract(terrain, [cut]) if not cut.is_empty() else terrain

    # 6. bridges over water ------------------------------------------------------------------------------------------------
    bridges = []
    if p.bridges and water_geom is not None and bridge_lines:
        for line, width in bridge_lines:
            if line is None or not line.intersects(water_geom):
                continue
            ends = np.array([line.coords[0], line.coords[-1]])
            deck = float(_sample(Z, xs, ys, ends[:, 0], ends[:, 1]).max())
            if p.roads == "raised":
                deck += p.road_depth_mm
            shape = line.buffer(width / 2.0 + 0.15, cap_style="flat").intersection(model_box)
            bridges.append(S.prism(shape, 0.0, deck))
    bridge_solid = S.union(bridges)
    if not bridge_solid.is_empty():
        water_solid = S.subtract(water_solid, [bridge_solid])

    # 7. buildings ----------------------------------------------------------------------------------------------------------
    building_solids = []
    building_shapes = []
    dropped = 0
    if p.buildings and feats.buildings:
        clock.step(0.55, "Buildings")
        for bld in feats.buildings:
            shape = to_mm(bld.shape)
            if shape is None:
                continue
            shape = shape.intersection(model_box)
            if shape.is_empty or shape.area < p.min_building_mm2:
                dropped += 1
                continue
            shape = shape.simplify(0.03).buffer(0.02, join_style="mitre")   # neighbours overlap instead of touching
            if shape.is_empty:
                continue
            if water_geom is not None and water_geom.contains(shape.representative_point()):
                continue
            coords = np.array([c for poly in S.polygons(shape) for c in poly.exterior.coords])
            ground = _sample(Z, xs, ys, coords[:, 0], coords[:, 1])
            z0 = max(float(ground.min()) - 0.3, 0.4)
            h = max((bld.height_m - bld.min_height_m) * k * p.building_exag, p.min_building_height_mm)
            z1 = float(ground.max()) + h
            building_solids.append(S.prism(shape, z0, z1))
            building_shapes.append(shape)
        clock.log(f"{len(building_solids)} buildings on the model, {dropped} too small")
    buildings = S.union(building_solids)

    # 8. colour layers on the terrain ----------------------------------------------------------------------------------------
    clock.step(0.68, "Parts")
    solid_top = max([top] + [s.bounding_box()[5] for s in building_solids]) + 5.0
    terrain_body = S.union([terrain_wet, bridge_solid])
    parts: dict[str, Any] = {}
    roads_skin = S.EMPTY
    green_skin = S.EMPTY
    if p.color == "color":
        # the single-piece twin (for the STL): the same solid, never split, so it stays one closed body
        parts[SINGLE] = S.union([terrain_body, buildings])
        # a colour layer is the top SKIN_MM of the terrain inside its outline: cutter = outline prism above the lowered
        # terrain; layer = terrain ∩ cutter and terrain -= cutter (complementary, never two coincident top faces)
        lowered = terrain_body.translate((0.0, 0.0, -SKIN_MM))
        wet = water_geom.buffer(0.05) if water_geom is not None else None   # layers stop just short of the banks
        road_area = None
        if roads_geom is not None and p.roads != "none":
            road_area = roads_geom.intersection(model_box)
            if wet is not None:
                over = [line.buffer(width / 2.0, quad_segs=3) for line, width in bridge_lines] if p.bridges else []
                on_bridges = shapely.union_all(np.array(over, dtype=object)).intersection(water_geom.buffer(0.08)) if over else None
                road_area = road_area.difference(wet)
                if on_bridges is not None and not on_bridges.is_empty:
                    road_area = road_area.union(on_bridges)
            road_area = _unpinch(road_area) if not road_area.is_empty else None
        green_area = None
        if p.green and feats.green is not None:
            g = to_mm(feats.green).intersection(model_box)
            if wet is not None:
                g = g.difference(wet)
            if road_area is not None:
                g = g.difference(road_area.buffer(0.05))
            green_area = _unpinch(g) if not g.is_empty else None
        road_cut = S.subtract(S.prism(road_area, -1.0, solid_top), [lowered]) if road_area is not None else S.EMPTY
        green_cut = S.subtract(S.prism(green_area, -1.0, solid_top), [lowered]) if green_area is not None else S.EMPTY
        roads_skin = S.intersect(terrain_body, road_cut)
        green_skin = S.intersect(terrain_body, green_cut)
        terrain_body = S.subtract(terrain_body, [road_cut, green_cut, buildings])
        parts["terrain"] = terrain_body
        parts["buildings"] = buildings
        parts["water"] = water_solid
        parts["roads"] = roads_skin
        parts["green"] = green_skin
    else:
        parts["terrain"] = S.union([terrain_body, buildings])

    # 9. outline shape ----------------------------------------------------------------------------------------------------
    if p.shape != "rect":
        cutter = S.prism(footprint, -1.0, solid_top + 10.0)
        parts = {n: S.intersect(m, cutter) for n, m in parts.items()}

    # 10. frame --------------------------------------------------------------------------------------------------------------
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

    # 11. text on the bottom -------------------------------------------------------------------------------------------------
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

    # 12. tiles and pins -------------------------------------------------------------------------------------------------------
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
        "roads_drawn": len(road_lines),
        "road_rank": rank,
        "water_pieces": len(water_levels),
        "sea": bool(sea.any()),
        "contour_interval_m": interval,
        "bridges": len(bridges),
        "tiles": [{"id": t["id"], "size_mm": [round(t["box"][2] - t["box"][0], 1), round(t["box"][3] - t["box"][1], 1)]} for t in tiles],
        "pins": len(pins),
        "features": feats.summary(),
        "timings_s": clock.times,
    }
    clock.step(0.92, "Done")
    masks = {"water": water_mask, "roads": road_mask & ~water_mask, "contours": contour_mask}
    footprints = {"outline": outer, "model": footprint, "buildings": building_shapes, "water": water_geom, "roads": roads_geom,
                  "green": to_mm(feats.green).intersection(model_box) if feats.green is not None else None}
    return BuildResult(p, parts, tiles, Z, xs, ys, masks, footprints, stats, notes, [a for a in attribution if a], whole)
