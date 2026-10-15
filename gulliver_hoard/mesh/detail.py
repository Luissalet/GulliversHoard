"""The fine detail that sits on the base model: roofs (gabled, hipped, pyramids and spires, domes, barrels, lean-tos),
3D trees (mapped trees, rows, woods filled densely and parks scattered), bridges standing on arches and piers, and the
snow and bare-rock zones of a mountain. Everything works in model millimetres and returns ``manifold3d`` solids."""

from __future__ import annotations

import math
from typing import Any, Callable, Iterable, Optional

import manifold3d as mf
import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import LineString, Polygon, box

from . import solids as S

Manifold = mf.Manifold

POINTED = {"pyramidal", "cone"}
ROUNDED = {"dome", "onion"}
HOUSE_KINDS = {"house", "detached", "semidetached_house", "terrace", "bungalow", "farm", "farmhouse", "cabin", "hut", "barn",
               "chalet", "villa"}
CHURCH_KINDS = {"church", "chapel", "cathedral", "basilica"}
MAX_TREES = 30000


# -- roofs ----------------------------------------------------------------------------------------------------------------------
def _frame(poly: Any) -> tuple[float, float, float, float, float]:
    """Centre, angle of the long side, and length and width of the smallest rectangle around the footprint."""
    rect = poly.minimum_rotated_rectangle
    if rect.geom_type != "Polygon":
        c = poly.centroid
        return c.x, c.y, 0.0, 0.0, 0.0
    pts = np.asarray(rect.exterior.coords)[:4]
    e1, e2 = pts[1] - pts[0], pts[2] - pts[1]
    l1, l2 = float(np.hypot(*e1)), float(np.hypot(*e2))
    along = e1 if l1 >= l2 else e2
    c = rect.centroid
    return c.x, c.y, math.atan2(along[1], along[0]), max(l1, l2), min(l1, l2)


def default_roof(kind: str, tagged: str, style: str) -> str:
    """Which roof a building gets: the mapped one, or under ``auto`` a pitched roof on houses and churches."""
    if style == "flat":
        return "flat"
    if tagged:
        return tagged
    if style == "auto" and kind in HOUSE_KINDS:
        return "hipped"
    if style == "auto" and kind in CHURCH_KINDS:
        return "gabled"
    return "flat"


def roof_height(shape: str, width_mm: float, pitch_deg: float = 35.0) -> float:
    """A believable roof height for a footprint ``width_mm`` wide when none is mapped."""
    if shape in POINTED:
        return width_mm * 0.9
    if shape in ROUNDED:
        return width_mm * 0.5
    if shape == "skillion":
        return width_mm * 0.25
    return width_mm / 2.0 * math.tan(math.radians(pitch_deg))


def roof_solid(poly: Any, z0: float, height: float, shape: str, across: bool = False) -> Optional[Manifold]:
    """A roof of ``shape`` over ``poly`` from ``z0`` up ``height`` mm, clipped to the footprint (an L-shaped house gets the
    roof of its hull, cut to its walls)."""
    if poly is None or poly.is_empty or height <= 0.05 or shape in ("", "flat"):
        return None
    cx, cy, ang, length, width = _frame(poly)
    if width <= 0.05:
        return None
    if across:
        ang += math.pi / 2.0
        length, width = width, length
    hull = poly.convex_hull
    local = affinity.rotate(affinity.translate(hull, -cx, -cy), -ang, origin=(0, 0), use_radians=True)
    if shape in ROUNDED or shape == "round":
        if shape == "round":
            body = Manifold.cylinder(length + 0.2, 1.0, 1.0, 20).rotate((0.0, 90.0, 0.0)).translate((-(length + 0.2) / 2, 0, 0))
            body = body.scale((1.0, width / 2.0, height))
        else:
            body = Manifold.sphere(1.0, 20).scale((length / 2.0, width / 2.0, height * (1.25 if shape == "onion" else 1.0)))
        roof = S.intersect(body, S.prism(local, 0.0, height * 1.3 + 1.0))
    else:
        cs = S.cross_section(local)
        if shape in POINTED:
            roof = cs.extrude(height, 0, 0.0, (0.0, 0.0))
        elif shape in ("hipped", "half-hipped", "mansard"):
            ridge = max(0.0, 1.0 - width / max(length, 1e-6))
            roof = cs.extrude(height, 0, 0.0, (ridge, 0.0 if shape != "mansard" else 0.35))
        elif shape == "skillion":
            roof = cs.translate((0.0, width / 2.0)).extrude(height, 0, 0.0, (1.0, 0.0)).translate((0.0, -width / 2.0, 0.0))
        else:                                   # gabled, gambrel, saltbox and the like
            roof = cs.extrude(height, 0, 0.0, (1.0, 0.0))
    roof = roof.rotate((0.0, 0.0, math.degrees(ang))).translate((cx, cy, z0))
    if hull.area - poly.area > 0.02 * poly.area:
        roof = S.intersect(roof, S.prism(poly, z0 - 1.0, z0 + height * 1.3 + 1.0))
    return None if roof.is_empty() else roof


def building_solid(poly: Any, z0: float, top: float, roof: str, roof_h: Optional[float], across: bool, k_vert: float,
                   min_roof_mm: float = 0.25) -> Manifold:
    """Walls from ``z0`` to the eaves and the roof up to ``top`` (``roof_h`` in model mm, None = a believable one)."""
    if roof in ("", "flat"):
        return S.prism(poly, z0, top)
    _cx, _cy, _ang, length, width = _frame(poly)
    rh = roof_h if roof_h is not None else roof_height(roof, width)
    rh = min(rh, (top - z0) * (0.95 if roof in POINTED else 0.6))
    if rh < min_roof_mm:
        return S.prism(poly, z0, top)
    eaves = top - rh
    walls = S.prism(poly, z0, eaves) if eaves - z0 > 0.05 else S.EMPTY
    inner = poly.buffer(-0.01, join_style="mitre")
    hat = roof_solid(inner if not inner.is_empty else poly, eaves - 0.02, rh + 0.02, roof, across)
    if hat is None:
        return S.prism(poly, z0, top)
    return S.union([walls, hat]) if not walls.is_empty() else hat


# -- trees -----------------------------------------------------------------------------------------------------------------------
def scatter(area: Any, spacing: float, rng: np.random.Generator, keep: float = 1.0, jitter: float = 0.42) -> np.ndarray:
    """Points over ``area`` on a jittered hexagonal grid ``spacing`` apart (``keep``: the share kept, at random)."""
    if area is None or area.is_empty or spacing <= 0:
        return np.zeros((0, 2))
    minx, miny, maxx, maxy = area.bounds
    ys = np.arange(miny, maxy + spacing, spacing * 0.866)
    xs = np.arange(minx, maxx + spacing, spacing)
    if len(xs) * len(ys) > 4_000_000:
        return np.zeros((0, 2))
    X, Y = np.meshgrid(xs, ys)
    X = X + (np.arange(len(ys))[:, None] % 2) * spacing / 2.0
    pts = np.column_stack([X.ravel(), Y.ravel()])
    pts += rng.uniform(-jitter, jitter, pts.shape) * spacing
    if keep < 1.0:
        pts = pts[rng.random(len(pts)) < keep]
    shapely.prepare(area)
    return pts[shapely.contains_xy(area, pts[:, 0], pts[:, 1])]


def along(lines: Iterable[Any], spacing: float) -> np.ndarray:
    out = []
    for line in lines:
        if line is None or line.is_empty or line.length <= 0:
            continue
        n = max(1, int(line.length / spacing))
        for t in np.linspace(0.0, line.length, n + 1):
            p = line.interpolate(t)
            out.append((p.x, p.y))
    return np.asarray(out, dtype=float).reshape(-1, 2)


def trees_solid(points: np.ndarray, radii: np.ndarray, ground: Callable[[np.ndarray, np.ndarray], np.ndarray],
                sink: float = 0.3, segments: int = 10) -> Manifold:
    """A rounded crown per point (radius ``radii``), standing on the ground with its foot ``sink`` mm under it."""
    if not len(points):
        return S.EMPTY
    z = ground(points[:, 0], points[:, 1])
    unit = Manifold.sphere(1.0, segments)
    crowns = []
    for (x, y), r, g in zip(points, radii, z):
        hz = r * 0.9
        crowns.append(unit.scale((r, r, hz)).translate((float(x), float(y), float(g) + hz - min(sink, hz * 0.6))))
    return S.union(crowns)


def plant(*, woods: Any, parks: Any, mapped: list[tuple[float, float, float]], rows: list[Any], blocked: Any, inside: Any,
          crown_mm: float, density: float, rng: np.random.Generator, blocked_light: Any = None,
          scatter_parks: bool = True, scatter_woods: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """Where the trees go and how big they are: trees known one by one (mapped, or seen on the aerial photo, with their own
    crowns) and rows of trees, then woods filled densely and parks scattered. Everything stays ``inside`` (the model, a crown
    in from its edge); scattered trees keep off ``blocked`` (roads, paths, buildings, water), known ones only off
    ``blocked_light`` (buildings, water: a street tree may lean over the road)."""
    known_p, known_r, fill_p, fill_r = [], [], [], []
    if mapped:
        m = np.asarray(mapped, dtype=float)
        known_p.append(m[:, :2])
        known_r.append(np.maximum(m[:, 2], crown_mm) / 2.0)
    if rows:
        r = along(rows, crown_mm * 0.9)
        known_p.append(r)
        known_r.append(np.full(len(r), crown_mm / 2.0))
    if density > 0:
        spacing = crown_mm * 0.82 / math.sqrt(density)
        expected = (woods.area if woods is not None and scatter_woods else 0.0) / (spacing * spacing * 0.866)
        expected += (parks.area if parks is not None and scatter_parks else 0.0) / (spacing * spacing * 0.866 * 6.0)
        if expected > MAX_TREES:
            spacing *= math.sqrt(expected / MAX_TREES)
        if scatter_woods:
            w = scatter(woods, spacing, rng)
            fill_p.append(w)
            fill_r.append(crown_mm / 2.0 * rng.uniform(0.85, 1.2, len(w)))
        if scatter_parks:
            pk = scatter(parks, spacing * 1.6, rng, keep=0.4)
            fill_p.append(pk)
            fill_r.append(crown_mm / 2.0 * rng.uniform(0.9, 1.3, len(pk)))

    def stack(ps: list, rs: list) -> tuple[np.ndarray, np.ndarray]:
        ps = [p for p in ps if len(p)]
        rs = [r for r in rs if len(r)]
        return (np.vstack(ps), np.concatenate(rs)) if ps else (np.zeros((0, 2)), np.zeros(0))

    def keep_off(P: np.ndarray, R: np.ndarray, geom: Any, inside_ok: bool = False) -> tuple[np.ndarray, np.ndarray]:
        if not len(P) or geom is None or geom.is_empty:
            return P, R
        shapely.prepare(geom)
        hit = shapely.contains_xy(geom, P[:, 0], P[:, 1])
        ok = hit if inside_ok else ~hit
        return P[ok], R[ok]

    KP, KR = stack(known_p, known_r)
    FP, FR = stack(fill_p, fill_r)
    KP, KR = keep_off(KP, KR, inside, inside_ok=True)
    FP, FR = keep_off(FP, FR, inside, inside_ok=True)
    KP, KR = keep_off(KP, KR, blocked_light if blocked_light is not None else blocked)
    FP, FR = keep_off(FP, FR, blocked)
    P, R = np.vstack([KP, FP]), np.concatenate([KR, FR])
    if len(P) > MAX_TREES:
        sel = rng.choice(len(P), MAX_TREES, replace=False)
        P, R = P[sel], R[sel]
    return P, R


# -- bridges ------------------------------------------------------------------------------------------------------------------------
def _arch_profile(s0: float, s1: float, z_floor: float, z_spring: float, z_soffit: float, span: float, pier: float) -> Any:
    """In the (along, up) plane: the solid under a deck from ``s0`` to ``s1`` — piers ``pier`` wide and round arches between
    them whose crowns reach ``z_soffit`` and whose feet stand at ``z_spring``."""
    length = s1 - s0
    n = max(1, int(round(length / span)))
    step = length / n
    body = box(s0, z_floor, s1, z_soffit)
    holes = []
    rise = max(0.05, z_soffit - z_spring)
    for i in range(n):
        a = s0 + i * step + pier / 2.0
        b = s0 + (i + 1) * step - pier / 2.0
        if b - a <= 0.3:
            continue
        half = (b - a) / 2.0
        mid = (a + b) / 2.0
        top = min(rise, half * 1.1)
        arc = [(mid - half * math.cos(t), z_spring + top * math.sin(t)) for t in np.linspace(0, math.pi, 17)]
        holes.append(Polygon([(a, z_floor - 1.0)] + arc + [(b, z_floor - 1.0)]))
    if holes:
        body = body.difference(shapely.union_all(np.array(holes, dtype=object)))
    return body


def arch_bridge(line: Any, width: float, deck: float, thickness: float, water_z: float, floor_z: float, span: float,
                pier: float = 0.6) -> Manifold:
    """A bridge along ``line`` (model mm): a deck ``thickness`` thick under the road at ``deck``, carried by piers and
    arches whose feet stand at the water (``water_z``) and whose piers go down to ``floor_z``."""
    deck_shape = line.buffer(width / 2.0, cap_style="flat")
    parts = [S.prism(deck_shape, deck - thickness, deck)]
    soffit = deck - thickness + 0.02
    if soffit - water_z < 0.3:             # too low for an arch: a solid causeway
        return S.prism(deck_shape, floor_z, deck)
    coords = np.asarray(line.coords)
    seg_len = np.hypot(*(coords[1:] - coords[:-1]).T)
    total = float(seg_len.sum())
    profile = _arch_profile(0.0, total, floor_z, water_z, soffit, span, pier)
    inner = max(0.2, width - 0.1)
    s = 0.0
    for (ax, ay), (bx, by), L in zip(coords[:-1], coords[1:], seg_len):
        if L < 1e-6:
            continue
        piece = profile.intersection(box(s - 0.01, floor_z - 2.0, s + L + 0.01, soffit + 1.0))
        s += L
        if piece.is_empty:
            continue
        ux, uy = (bx - ax) / L, (by - ay) / L
        nx, ny = uy, -ux
        solid = S.cross_section(piece).extrude(inner).translate((0.0, 0.0, -inner / 2.0))
        start = s - L
        m = [[ux, 0.0, nx, ax - start * ux], [uy, 0.0, ny, ay - start * uy], [0.0, 1.0, 0.0, 0.0]]
        parts.append(solid.transform(m))
    return S.union(parts)


# -- zones ----------------------------------------------------------------------------------------------------------------------------
def snow_line(elev: np.ndarray, snow_m: float) -> Optional[float]:
    """Height above which the ground is snow: the given one, or (automatic) the top fifth of the relief on mountains that
    reach 2.000 m."""
    if snow_m == 0:
        return None
    hi, lo = float(np.nanmax(elev)), float(np.nanmin(elev))
    if snow_m > 0:
        return snow_m if hi > snow_m else None
    if hi < 2000.0:
        return None
    return max(1800.0, hi - 0.2 * (hi - lo))


def slope_deg(elev: np.ndarray, cell_m: float) -> np.ndarray:
    gy, gx = np.gradient(elev, cell_m)
    return np.degrees(np.arctan(np.hypot(gx, gy)))
