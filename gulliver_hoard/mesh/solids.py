"""Closed solids with manifold3d: the terrain heightfield, prisms of 2D shapes, outline shapes, engraved text and alignment-pin
holes. Every function returns a ``manifold3d.Manifold`` (watertight by construction) in model millimetres (x east, y north,
z up, bottom of the base at z = 0)."""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Any, Iterable, Optional, Sequence

import manifold3d as mf
import numpy as np
import shapely
from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import MultiPolygon, Polygon, box
from shapely.ops import unary_union

Manifold = mf.Manifold
EMPTY = Manifold()


def heightfield(Z: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> Manifold:
    """A solid whose top is the grid ``Z[i, j]`` at (xs[j], ys[i]) and whose bottom is the plane z = 0.

    Rows go south to north (ys increasing), columns west to east. The bottom is a fan around its centre, the four walls
    join the top border to the bottom border; the triangles are oriented outwards."""
    ny, nx = Z.shape
    X, Y = np.meshgrid(xs, ys)
    top = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()])
    ids = np.arange(ny * nx, dtype=np.int64).reshape(ny, nx)
    a = ids[:-1, :-1].ravel()
    b = ids[:-1, 1:].ravel()
    c = ids[1:, 1:].ravel()
    d = ids[1:, :-1].ravel()
    # alternate the diagonal so slopes do not get a combed look
    alt = ((np.arange(ny - 1)[:, None] + np.arange(nx - 1)[None, :]) % 2 == 1).ravel()
    t1 = np.where(alt[:, None], np.column_stack([a, b, d]), np.column_stack([a, b, c]))
    t2 = np.where(alt[:, None], np.column_stack([b, c, d]), np.column_stack([a, c, d]))
    ring = np.concatenate([ids[0, :], ids[1:, -1], ids[-1, -2::-1], ids[-2:0:-1, 0]])
    n = ny * nx
    m = len(ring)
    bottom = np.column_stack([top[ring, 0], top[ring, 1], np.zeros(m)])
    centre = np.array([[(xs[0] + xs[-1]) / 2.0, (ys[0] + ys[-1]) / 2.0, 0.0]])
    verts = np.vstack([top, bottom, centre])
    k = np.arange(m)
    k1 = (k + 1) % m
    pk, pk1 = ring[k], ring[k1]
    bk, bk1 = n + k, n + k1
    walls1 = np.column_stack([pk, bk, bk1])
    walls2 = np.column_stack([pk, bk1, pk1])
    cid = n + m
    floor = np.column_stack([np.full(m, cid), bk1, bk])
    tris = np.vstack([t1, t2, walls1, walls2, floor]).astype(np.uint32)
    mesh = mf.Mesh(vert_properties=np.ascontiguousarray(verts, dtype=np.float32), tri_verts=np.ascontiguousarray(tris))
    solid = Manifold(mesh)
    if solid.status() != mf.Error.NoError:
        raise RuntimeError(f"terrain mesh is not manifold: {solid.status()}")
    return solid


def _rings(geom: Any) -> list[np.ndarray]:
    out = []
    for poly in polygons(geom):
        ext = np.asarray(poly.exterior.coords, dtype=np.float64)[:-1]
        if len(ext) >= 3:
            out.append(ext)
        for hole in poly.interiors:
            h = np.asarray(hole.coords, dtype=np.float64)[:-1]
            if len(h) >= 3:
                out.append(h)
    return out


def polygons(geom: Any) -> list[Polygon]:
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    return [g for g in getattr(geom, "geoms", []) for g in polygons(g)]


def cross_section(geom: Any) -> mf.CrossSection:
    rings = _rings(geom)
    if not rings:
        return mf.CrossSection()
    return mf.CrossSection(rings, mf.FillRule.EvenOdd)


def prism(geom: Any, z0: float, z1: float) -> Manifold:
    """The 2D shape ``geom`` (shapely, mm) extruded from z0 to z1."""
    if z1 <= z0 + 1e-6:
        return EMPTY
    cs = cross_section(geom)
    if cs.is_empty():
        return EMPTY
    return Manifold.extrude(cs, z1 - z0).translate((0.0, 0.0, z0))


def union(parts: Iterable[Manifold]) -> Manifold:
    items = [p for p in parts if p is not None and not p.is_empty()]
    if not items:
        return EMPTY
    if len(items) == 1:
        return items[0]
    return Manifold.batch_boolean(items, mf.OpType.Add)


def subtract(base: Manifold, cuts: Iterable[Manifold]) -> Manifold:
    items = [c for c in cuts if c is not None and not c.is_empty()]
    if base.is_empty() or not items:
        return base
    return Manifold.batch_boolean([base] + items, mf.OpType.Subtract)


def intersect(a: Manifold, b: Manifold) -> Manifold:
    if a.is_empty() or b.is_empty():
        return EMPTY
    return a ^ b


# -- outline shapes ----------------------------------------------------------------------------------------------------------
def outline(shape: str, width: float, height: float) -> Polygon:
    """The footprint of the model inside [0, width] x [0, height]."""
    if shape == "circle":
        r = min(width, height) / 2.0
        return shapely.Point(width / 2.0, height / 2.0).buffer(r, quad_segs=64)
    if shape == "hexagon":
        r = min(width, height) / 2.0
        cx, cy = width / 2.0, height / 2.0
        pts = [(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))) for a in range(0, 360, 60)]
        return Polygon(pts)
    if shape == "rounded":
        r = min(width, height) * 0.08
        return box(r, r, width - r, height - r).buffer(r, quad_segs=16)
    return box(0.0, 0.0, width, height)


# -- text ---------------------------------------------------------------------------------------------------------------------
FONT_CANDIDATES = ("arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "segoeuib.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "C:/Windows/Fonts/arialbd.ttf")


@lru_cache(maxsize=16)
def _font(px: int):
    for name in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=px)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def mask_to_polygons(mask: np.ndarray, x0: float, y0: float, cell: float) -> Any:
    """Union of the cells of a boolean mask (row 0 at y0, rows going +y) as shapely polygons, one rectangle per run."""
    boxes = []
    for i in range(mask.shape[0]):
        row = mask[i]
        if not row.any():
            continue
        d = np.diff(np.concatenate([[0], row.view(np.int8), [0]]))
        starts = np.flatnonzero(d == 1)
        ends = np.flatnonzero(d == -1)
        for s, e in zip(starts, ends):
            boxes.append(box(x0 + s * cell, y0 + i * cell, x0 + e * cell, y0 + (i + 1) * cell))
    if not boxes:
        return Polygon()
    return shapely.union_all(np.array(boxes, dtype=object))


def text_shape(lines: Sequence[str], height_mm: float, *, mirror: bool = False, px_per_mm: float = 12.0) -> Any:
    """Text (several lines, centred) as shapely polygons centred on (0, 0), ``height_mm`` the cap height of one line."""
    lines = [l for l in lines if l]
    if not lines:
        return Polygon()
    px = max(8, int(round(height_mm * px_per_mm * 1.35)))
    font = _font(px)
    probe = ImageDraw.Draw(Image.new("L", (8, 8)))
    sizes = [probe.textbbox((0, 0), l, font=font) for l in lines]
    w = max(b[2] - b[0] for b in sizes) + 8
    line_h = int(px * 1.25)
    h = line_h * len(lines) + 8
    img = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(img)
    for k, (line, b) in enumerate(zip(lines, sizes)):
        draw.text(((w - (b[2] - b[0])) / 2 - b[0], 4 + k * line_h - b[1] + (line_h - (b[3] - b[1])) / 2), line, fill=255, font=font)
    mask = np.asarray(img) > 127
    mask = mask[::-1]                                   # image rows go down, model rows go up
    if mirror:
        mask = mask[:, ::-1]
    cell = 1.0 / px_per_mm
    shape = mask_to_polygons(mask, -w * cell / 2.0, -h * cell / 2.0, cell)
    return shape.simplify(cell * 0.4)


def text_engraving(lines: Sequence[str], height_mm: float, centre: tuple[float, float], depth: float, *, max_width: float,
                   bottom: bool = True) -> Manifold:
    """A solid to subtract: the text cut ``depth`` mm into the bottom face (mirrored, so it reads with the model upside down)."""
    shape = text_shape(lines, height_mm, mirror=bottom)
    if shape.is_empty:
        return EMPTY
    minx, _miny, maxx, _maxy = shape.bounds
    if maxx - minx > max_width > 0:
        k = max_width / (maxx - minx)
        shape = shapely.affinity.scale(shape, k, k, origin=(0, 0))
    shape = shapely.affinity.translate(shape, centre[0], centre[1])
    return prism(shape, -1.0, depth)


# -- tiles and pins -------------------------------------------------------------------------------------------------------------
def tile_boxes(bounds: tuple[float, float, float, float], nx: int, ny: int) -> list[dict[str, Any]]:
    """Tiles of the bounding box; row letters from the north (A = top), column numbers from the west."""
    minx, miny, maxx, maxy = bounds
    out = []
    for r in range(ny):
        for c in range(nx):
            x0 = minx + (maxx - minx) * c / nx
            x1 = minx + (maxx - minx) * (c + 1) / nx
            y1 = maxy - (maxy - miny) * r / ny
            y0 = maxy - (maxy - miny) * (r + 1) / ny
            out.append({"id": f"{chr(65 + r)}{c + 1}", "row": r, "col": c, "box": (x0, y0, x1, y1)})
    return out


def pin_holes(footprint: Any, bounds: tuple[float, float, float, float], nx: int, ny: int, *, diameter: float, depth: float,
              z: float, spacing: float = 45.0) -> tuple[Manifold, list[dict[str, Any]]]:
    """Horizontal holes across each seam between tiles (a pin of length 2 x depth joins two tiles)."""
    minx, miny, maxx, maxy = bounds
    r = diameter / 2.0
    safe = footprint.buffer(-(r + 1.5)) if footprint is not None else None
    holes, where = [], []

    def fits(x0: float, y0: float, x1: float, y1: float) -> bool:
        return safe is None or safe.contains(shapely.LineString([(x0, y0), (x1, y1)]))

    def along(a0: float, a1: float) -> list[float]:
        length = a1 - a0
        n = max(2, int(length // spacing) + 1)
        return [a0 + length * (k + 0.5) / n for k in range(n)]

    for c in range(1, nx):
        x = minx + (maxx - minx) * c / nx
        for r_ in range(ny):
            y0 = maxy - (maxy - miny) * (r_ + 1) / ny
            y1 = maxy - (maxy - miny) * r_ / ny
            for y in along(y0 + 4, y1 - 4):
                if not fits(x - depth - 1, y, x + depth + 1, y):
                    continue
                cyl = Manifold.cylinder(2 * depth, r, r, 24, True).rotate((0.0, 90.0, 0.0)).translate((x, y, z))
                holes.append(cyl)
                where.append({"x": round(x, 2), "y": round(y, 2), "axis": "x"})
    for r_ in range(1, ny):
        y = maxy - (maxy - miny) * r_ / ny
        for c in range(nx):
            x0 = minx + (maxx - minx) * c / nx
            x1 = minx + (maxx - minx) * (c + 1) / nx
            for x in along(x0 + 4, x1 - 4):
                if not fits(x, y - depth - 1, x, y + depth + 1):
                    continue
                cyl = Manifold.cylinder(2 * depth, r, r, 24, True).rotate((90.0, 0.0, 0.0)).translate((x, y, z))
                holes.append(cyl)
                where.append({"x": round(x, 2), "y": round(y, 2), "axis": "y"})
    return union(holes), where


def mesh_arrays(solid: Manifold, weld: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """Vertices (float32) and triangles of a solid. ``weld`` merges vertices that land on the same float32 position (manifold
    keeps them apart in double precision, which a slicer would read as a pinched edge) and drops the triangles that collapse."""
    mesh = solid.to_mesh()
    verts = np.asarray(mesh.vert_properties, dtype=np.float32)[:, :3]
    tris = np.asarray(mesh.tri_verts, dtype=np.int64)
    if not weld or len(verts) == 0:
        return verts, tris.astype(np.uint32)
    uniq, inverse = np.unique(verts, axis=0, return_inverse=True)
    if len(uniq) == len(verts):
        return verts, tris.astype(np.uint32)
    t = inverse.reshape(-1)[tris]
    keep = (t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])
    t = t[keep]
    # a collapsed sliver can leave two copies of a face with opposite orientation: drop both
    key = np.sort(t, axis=1)
    _u, first, counts = np.unique(key, axis=0, return_index=True, return_counts=True)
    if (counts > 1).any():
        dup_keys = _u[counts > 1]
        dup = (key[:, None, :] == dup_keys[None, :, :]).all(axis=2).any(axis=1) if len(dup_keys) < 2000 else np.isin(
            key.view([("", key.dtype)] * 3).ravel(), dup_keys.view([("", dup_keys.dtype)] * 3).ravel())
        t = t[~dup]
    used = np.unique(t)
    remap = np.full(len(uniq), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    return np.ascontiguousarray(uniq[used]), remap[t].astype(np.uint32)


def translate(solid: Manifold, dx: float, dy: float, dz: float = 0.0) -> Manifold:
    return solid if solid.is_empty() else solid.translate((dx, dy, dz))


def clean(solid: Manifold, min_volume: float = 1e-3) -> Manifold:
    """Drop slivers (components with no real volume) that booleans can leave on touching faces."""
    if solid is None or solid.is_empty():
        return EMPTY
    comps = solid.decompose()
    if len(comps) <= 1:
        return solid if solid.volume() > min_volume else EMPTY
    kept = [c for c in comps if c.volume() > min_volume]
    if len(kept) == len(comps):
        return solid
    return Manifold.compose(kept) if kept else EMPTY
