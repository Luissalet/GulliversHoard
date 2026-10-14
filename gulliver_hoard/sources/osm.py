"""What is on the ground, from OpenStreetMap through the Overpass API: buildings with their heights, water (lakes, reservoirs,
river areas, docks), rivers and streams as lines, roads by class (with bridges and tunnels), railways and green areas.

Relations (multipolygons) are assembled properly: their member ways are stitched into closed rings, outer rings are united
and inner rings cut out, so a lake with islands or a building with a courtyard keeps its shape. Everything is returned in
metres of the area's local projection, clipped to the area."""

from __future__ import annotations

import json
import math
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

import numpy as np
import shapely
from shapely.geometry import LineString, MultiPolygon, Polygon, box
from shapely.ops import unary_union

from ..errors import GulliverError
from ..geo import Projector
from .http import Fetcher

DEFAULT_SERVERS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
ATTRIBUTION = "© OpenStreetMap contributors (ODbL)"

# Real widths in metres by highway class (what the carriageway looks like from above).
ROAD_WIDTH_M = {
    "motorway": 24.0, "trunk": 20.0, "primary": 15.0, "secondary": 12.0, "tertiary": 10.0, "unclassified": 7.0,
    "residential": 7.0, "living_street": 6.0, "pedestrian": 6.0, "service": 4.5, "track": 3.5, "footway": 2.5,
    "path": 2.0, "cycleway": 2.5, "steps": 2.5,
}
ROAD_RANK = {"motorway": 1, "trunk": 1, "primary": 2, "secondary": 2, "tertiary": 3, "unclassified": 3, "residential": 4,
             "living_street": 4, "pedestrian": 4, "service": 5, "track": 5, "footway": 6, "path": 6, "cycleway": 6, "steps": 6}
LINK_OF = {"motorway_link": "motorway", "trunk_link": "trunk", "primary_link": "primary", "secondary_link": "secondary",
           "tertiary_link": "tertiary"}
RAIL_KINDS = {"rail", "light_rail", "tram", "subway", "narrow_gauge", "monorail", "funicular"}
WATERWAY_WIDTH_M = {"river": 20.0, "canal": 12.0, "stream": 3.0, "drain": 2.0, "ditch": 1.5}
GREEN_TAGS = {
    "leisure": {"park", "garden", "pitch", "golf_course", "nature_reserve", "playground", "recreation_ground"},
    "landuse": {"forest", "grass", "meadow", "recreation_ground", "village_green", "cemetery", "orchard", "vineyard", "allotments"},
    "natural": {"wood", "scrub", "grassland", "heath"},
}
BUILDING_HEIGHT_M = {
    "house": 7.0, "detached": 7.0, "semidetached_house": 7.0, "terrace": 8.0, "bungalow": 4.0, "garage": 3.0, "garages": 3.0,
    "shed": 3.0, "hut": 3.0, "roof": 4.0, "carport": 3.0, "apartments": 16.0, "residential": 12.0, "commercial": 12.0,
    "office": 18.0, "retail": 8.0, "industrial": 9.0, "warehouse": 9.0, "church": 18.0, "cathedral": 35.0, "chapel": 9.0,
    "school": 10.0, "university": 14.0, "hospital": 18.0, "hotel": 20.0, "train_station": 14.0, "stadium": 22.0,
    "greenhouse": 4.0, "kiosk": 3.0, "service": 4.0, "transformer_tower": 6.0,
}
LEVEL_M = 3.1
LANDMARK_TYPES = {"church", "cathedral", "chapel", "mosque", "synagogue", "temple", "castle", "palace", "tower", "stadium",
                  "train_station", "museum", "monastery", "bell_tower"}


@dataclass
class Building:
    shape: Any            # shapely Polygon/MultiPolygon in metres
    height_m: float
    min_height_m: float
    kind: str
    name: str = ""
    estimated: bool = False


@dataclass
class Road:
    line: Any             # LineString in metres
    kind: str
    width_m: float
    rank: int
    bridge: bool = False
    tunnel: bool = False
    name: str = ""


@dataclass
class Features:
    buildings: list[Building] = field(default_factory=list)
    water: Any = None             # Polygon/MultiPolygon (areas)
    waterways: list[tuple[Any, float, str]] = field(default_factory=list)   # (line, width_m, kind)
    roads: list[Road] = field(default_factory=list)
    rails: list[Any] = field(default_factory=list)      # lines
    green: Any = None             # Polygon/MultiPolygon
    landmarks: list[dict[str, Any]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "buildings": len(self.buildings),
            "buildings_with_height": sum(1 for b in self.buildings if not b.estimated),
            "water_km2": round((self.water.area if self.water is not None else 0.0) / 1e6, 4),
            "waterways": len(self.waterways),
            "roads": len(self.roads),
            "bridges": sum(1 for r in self.roads if r.bridge),
            "rails": len(self.rails),
            "green_km2": round((self.green.area if self.green is not None else 0.0) / 1e6, 4),
            "landmarks": [l["name"] for l in self.landmarks[:12]],
        }


def build_query(bbox: tuple[float, float, float, float], timeout: int = 180) -> str:
    lon_min, lat_min, lon_max, lat_max = bbox
    b = f"{lat_min:.6f},{lon_min:.6f},{lat_max:.6f},{lon_max:.6f}"
    green = "".join(f'way["{k}"~"^({"|".join(sorted(v))})$"];relation["{k}"~"^({"|".join(sorted(v))})$"];' for k, v in GREEN_TAGS.items())
    return (f"[out:json][timeout:{timeout}][maxsize:536870912][bbox:{b}];("
            'way["building"];relation["building"];'
            'way["natural"="water"];relation["natural"="water"];'
            'way["water"];relation["water"];'
            'way["waterway"~"^(riverbank|dock)$"];relation["waterway"="riverbank"];'
            'way["landuse"~"^(reservoir|basin)$"];relation["landuse"~"^(reservoir|basin)$"];'
            'way["waterway"~"^(river|stream|canal|drain|ditch)$"];'
            'way["highway"];'
            'way["railway"~"^(rail|light_rail|tram|subway|narrow_gauge|monorail|funicular)$"];'
            f"{green}"
            ");out geom;")


STAGGER_S = 6.0          # a slow server gets this long before the next one is asked too


def _answer(body: bytes) -> tuple[Optional[dict[str, Any]], str]:
    if not body.lstrip().startswith(b"{"):
        return None, "not JSON"
    data = json.loads(body)
    remark = str(data.get("remark") or "")
    if remark and ("error" in remark.lower() or "timed out" in remark.lower()) and not data.get("elements"):
        return None, remark[:160]
    return data, ""


def fetch_raw(fetcher: Fetcher, bbox: tuple[float, float, float, float], servers: Iterable[str] = DEFAULT_SERVERS,
              progress: Optional[Callable[[str], None]] = None, max_age_days: float = 30.0,
              cancelled: Optional[Callable[[], bool]] = None, stagger_s: float = STAGGER_S) -> dict[str, Any]:
    """The Overpass answer for the area. Public servers are often busy: the first one is asked at once and, while none
    has answered, another is added every ``stagger_s`` seconds (or as soon as one fails); the first good answer wins."""
    query = build_query(bbox)
    cache_key = "overpass\n" + query
    hit = fetcher.cached("overpass", cache_key, ".json", max_age_days * 86400)
    if hit is not None:
        if progress:
            progress("OpenStreetMap: from the cache")
        return json.loads(hit)
    pending = list(servers)
    if not pending:
        raise GulliverError("invalid", "No Overpass server is configured.", "Add one in the settings.")
    answers: "queue.Queue[tuple[str, Optional[bytes], str]]" = queue.Queue()

    def ask(url: str) -> None:
        try:
            answers.put((url, fetcher.post_form(url, {"data": query}, bucket="overpass", cache_key=cache_key + url, timeout=240), ""))
        except GulliverError as exc:
            answers.put((url, None, exc.message))
        except Exception as exc:  # noqa: BLE001 - any failure of one server just moves on to the next
            answers.put((url, None, str(exc)))

    running = 0
    errors: list[str] = []
    next_start = 0.0
    while True:
        now = time.monotonic()
        if pending and (running == 0 or now >= next_start):
            url = pending.pop(0)
            if progress:
                progress(f"OpenStreetMap: asking {url.split('/')[2]}")
            threading.Thread(target=ask, args=(url,), name="gulliver-overpass", daemon=True).start()
            running += 1
            next_start = now + stagger_s
        if cancelled and cancelled():
            from ..errors import Cancelled
            raise Cancelled()
        try:
            url, body, error = answers.get(timeout=max(0.2, min(1.0, next_start - time.monotonic())) if pending else 1.0)
        except queue.Empty:
            continue
        running -= 1
        host = url.split("/")[2]
        if body is not None:
            try:
                data, error = _answer(body)
            except ValueError as exc:
                data, error = None, f"bad JSON ({exc})"
            if data is not None:
                fetcher.store("overpass", cache_key, body, ".json")
                if progress:
                    progress(f"OpenStreetMap: {host} answered")
                return data
        errors.append(f"{host}: {error}")
        if progress:
            progress(f"OpenStreetMap: {host} failed ({error[:80]})")
        next_start = 0.0                       # ask the next one now
        if not pending and running == 0:
            raise GulliverError("source_failed", "No Overpass server answered: " + "; ".join(errors),
                                "The OpenStreetMap servers are busy or the area is too big; try again or pick a smaller area.")


# -- geometry assembly -------------------------------------------------------------------------------------------------------
def _coords(geometry: list[dict[str, float]] | None) -> list[tuple[float, float]]:
    return [(g["lon"], g["lat"]) for g in (geometry or []) if g and "lon" in g]


def stitch_rings(segments: list[list[tuple[float, float]]]) -> list[list[tuple[float, float]]]:
    """Join way segments that share end points into closed rings (open leftovers are closed if long enough)."""
    rings: list[list[tuple[float, float]]] = []
    pool = [list(s) for s in segments if len(s) >= 2]
    while pool:
        ring = pool.pop(0)
        changed = True
        while ring[0] != ring[-1] and changed:
            changed = False
            for k, seg in enumerate(pool):
                if seg[0] == ring[-1]:
                    ring.extend(seg[1:])
                elif seg[-1] == ring[-1]:
                    ring.extend(reversed(seg[:-1]))
                elif seg[-1] == ring[0]:
                    ring[:0] = seg[:-1]
                elif seg[0] == ring[0]:
                    ring[:0] = list(reversed(seg[1:]))
                else:
                    continue
                pool.pop(k)
                changed = True
                break
        if len(ring) >= 4:
            if ring[0] != ring[-1]:
                ring.append(ring[0])
            rings.append(ring)
    return rings


def _valid(geom: Any) -> Any:
    if geom is None or geom.is_empty:
        return None
    if not geom.is_valid:
        geom = shapely.make_valid(geom)
        polys = [g for g in getattr(geom, "geoms", [geom]) if g.geom_type in ("Polygon", "MultiPolygon")]
        geom = unary_union(polys) if polys else None
    return geom if geom is not None and not geom.is_empty else None


class _Proj:
    def __init__(self, projector: Projector):
        self.p = projector

    def ring(self, coords: list[tuple[float, float]]) -> np.ndarray:
        a = np.asarray(coords, dtype=np.float64)
        x, y = self.p.fwd(a[:, 0], a[:, 1])
        return np.column_stack([x, y])

    def polygon(self, outer: list, inners: list | None = None) -> Any:
        try:
            return _valid(Polygon(self.ring(outer), [self.ring(i) for i in (inners or []) if len(i) >= 4]))
        except (ValueError, shapely.errors.GEOSException):
            return None

    def line(self, coords: list) -> Any:
        if len(coords) < 2:
            return None
        return LineString(self.ring(coords))


def _multipolygon(proj: _Proj, element: dict[str, Any]) -> Any:
    outer, inner = [], []
    for m in element.get("members", []):
        if m.get("type") != "way":
            continue
        coords = _coords(m.get("geometry"))
        if len(coords) < 2:
            continue
        (inner if m.get("role") == "inner" else outer).append(coords)
    outers = [p for p in (proj.polygon(r) for r in stitch_rings(outer)) if p is not None]
    if not outers:
        return None
    shape = unary_union(outers)
    inners = [p for p in (proj.polygon(r) for r in stitch_rings(inner)) if p is not None]
    if inners:
        shape = shape.difference(unary_union(inners))
    return _valid(shape)


def _num(text: Any) -> Optional[float]:
    if text is None:
        return None
    s = str(text).strip().lower().replace(",", ".")
    for suffix in (" m", "m", " metres", " meters"):
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
    if s.endswith("'") or "ft" in s:
        try:
            return float(s.replace("ft", "").replace("'", "").strip()) * 0.3048
        except ValueError:
            return None
    try:
        v = float(s.split(";")[0].split()[0])
    except (ValueError, IndexError):
        return None
    return v if math.isfinite(v) else None


def building_height(tags: dict[str, str]) -> tuple[float, float, bool]:
    """(height, min_height, estimated) in metres from height / building:levels / roof:levels / the building type."""
    h = _num(tags.get("height")) or _num(tags.get("building:height"))
    levels = _num(tags.get("building:levels"))
    roof = _num(tags.get("roof:levels")) or 0.0
    min_h = _num(tags.get("min_height")) or ((_num(tags.get("building:min_level")) or 0.0) * LEVEL_M)
    estimated = False
    if h is None and levels is not None:
        h = (levels + roof * 0.5) * LEVEL_M + 1.0
    if h is None:
        h = BUILDING_HEIGHT_M.get(tags.get("building", "yes"), 7.0)
        estimated = True
    h = max(2.0, min(h, 900.0))
    return h, max(0.0, min(min_h, h - 1.0)), estimated


def _is_green(tags: dict[str, str]) -> bool:
    return any(tags.get(k) in v for k, v in GREEN_TAGS.items())


def _is_water_area(tags: dict[str, str]) -> bool:
    return (tags.get("natural") == "water" or "water" in tags or tags.get("waterway") in ("riverbank", "dock")
            or tags.get("landuse") in ("reservoir", "basin"))


def parse(data: dict[str, Any], projector: Projector, width_m: float, height_m: float, pad_m: float = 0.0) -> Features:
    """Overpass JSON -> :class:`Features` in metres, clipped to the area (plus ``pad_m`` around it)."""
    proj = _Proj(projector)
    clip = box(-width_m / 2 - pad_m, -height_m / 2 - pad_m, width_m / 2 + pad_m, height_m / 2 + pad_m)
    feats = Features()
    water_polys, green_polys = [], []
    seen_building_ways: set[int] = set()
    for el in data.get("elements", []):
        if el.get("type") == "relation":
            for m in el.get("members", []):
                if m.get("type") == "way" and "building" in el.get("tags", {}):
                    seen_building_ways.add(m.get("ref"))
    landmarks = []
    for el in data.get("elements", []):
        tags = el.get("tags") or {}
        kind = el.get("type")
        if kind == "relation":
            if tags.get("type") not in ("multipolygon", None, "building"):
                continue
            shape = _multipolygon(proj, el)
            if shape is None:
                continue
        elif kind == "way":
            coords = _coords(el.get("geometry"))
            if len(coords) < 2:
                continue
            closed = len(coords) >= 4 and coords[0] == coords[-1]
            shape = proj.polygon(coords) if closed else None
            line = proj.line(coords)
        else:
            continue

        if "building" in tags and tags.get("building") not in ("no",):
            if kind == "way" and (el.get("id") in seen_building_ways or shape is None):
                continue
            g = shape.intersection(clip) if shape is not None else None
            if g is None or g.is_empty or g.area < 1.0:
                continue
            h, mh, est = building_height(tags)
            name = tags.get("name", "")
            feats.buildings.append(Building(g, h, mh, tags.get("building", "yes"), name, est))
            notable = ("wikidata" in tags or "wikipedia" in tags or "historic" in tags or tags.get("tourism") in ("attraction", "museum")
                       or tags.get("building") in LANDMARK_TYPES or tags.get("amenity") == "place_of_worship" or h >= 40)
            if name and notable:
                rank = (0 if ("wikidata" in tags or "wikipedia" in tags) else 1 if ("historic" in tags or "tourism" in tags) else 2)
                landmarks.append({"name": name, "height_m": h, "area_m2": g.area, "rank": rank})
            continue
        if _is_water_area(tags) and (kind == "relation" or shape is not None):
            if shape is not None:
                water_polys.append(shape)
            continue
        if kind == "way" and tags.get("waterway") in WATERWAY_WIDTH_M:
            w = _num(tags.get("width")) or WATERWAY_WIDTH_M[tags["waterway"]]
            if tags.get("tunnel") in ("yes", "culvert"):
                continue
            feats.waterways.append((line, w, tags["waterway"]))
            continue
        if kind == "way" and "highway" in tags:
            hw = LINK_OF.get(tags["highway"], tags["highway"])
            if hw not in ROAD_WIDTH_M or tags.get("area") == "yes":
                continue
            lanes = _num(tags.get("lanes"))
            width = _num(tags.get("width")) or (max(ROAD_WIDTH_M[hw], lanes * 3.3) if lanes else ROAD_WIDTH_M[hw])
            feats.roads.append(Road(line, hw, width, ROAD_RANK[hw], bridge=tags.get("bridge") not in (None, "no"),
                                    tunnel=tags.get("tunnel") not in (None, "no") or tags.get("layer", "0").startswith("-"),
                                    name=tags.get("name", "")))
            continue
        if kind == "way" and tags.get("railway") in RAIL_KINDS:
            if tags.get("tunnel") not in (None, "no"):
                continue
            feats.rails.append(line)
            continue
        if _is_green(tags) and shape is not None:
            green_polys.append(shape)

    feats.water = _valid(unary_union(water_polys).intersection(clip)) if water_polys else None
    feats.green = _valid(unary_union(green_polys).intersection(clip)) if green_polys else None
    if feats.green is not None and feats.water is not None:
        feats.green = _valid(feats.green.difference(feats.water))
    feats.roads = [r for r in feats.roads if r.line is not None and r.line.intersects(clip)]
    feats.rails = [r for r in feats.rails if r is not None and r.intersects(clip)]
    feats.waterways = [w for w in feats.waterways if w[0] is not None and w[0].intersects(clip)]
    landmarks.sort(key=lambda l: (l["rank"], -l["height_m"], -l["area_m2"]))
    seen_names: set[str] = set()
    landmarks = [l for l in landmarks if not (l["name"] in seen_names or seen_names.add(l["name"]))]
    feats.landmarks = landmarks
    feats.counts = {"elements": len(data.get("elements", []))}
    return feats


def as_polygons(geom: Any) -> list[Polygon]:
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    return [g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon)]
