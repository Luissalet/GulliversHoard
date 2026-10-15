"""Personal touches and finishing: the owner's place, a route, the plaque and frame styles, squares, street seams,
surface-model heights, thickening and the printability report."""

import numpy as np
import pytest

from gulliver_hoard.mesh.build import build
from gulliver_hoard.outputs import _check
from gulliver_hoard.params import ModelParams
from gulliver_hoard.sources import dsm as dsm_mod
from gulliver_hoard.sources.fake import FakeSources

from conftest import DEMO

GPX = """<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>
<trkpt lat="40.4170" lon="-3.7080"/><trkpt lat="40.4185" lon="-3.7040"/><trkpt lat="40.4210" lon="-3.7010"/>
<trkpt lat="40.4230" lon="-3.6960"/></trkseg></trk></gpx>"""


def make(**kw):
    return ModelParams(**{**DEMO, "size_mm": 120, "resolution_mm": 0.5, "color": "color", **kw})


def all_closed(res):
    bad = [n for n, m in res.parts.items() if not _check(m)["watertight"]]
    for t in res.tiles:
        bad += [f"{t['id']}/{n}" for n, m in t["parts"].items() if not _check(m)["watertight"]]
    return bad


def test_highlight_a_building_and_a_marker():
    src = FakeSources()
    p = make()
    proj = p.area.projector
    lon, lat = proj.inv(320.0, -300.0)                    # the tower
    res = build(make(highlight_lon=float(lon), highlight_lat=float(lat)), src)
    assert res.stats["highlight"] == "building" and "highlight" in res.parts and not all_closed(res)
    lon, lat = proj.inv(-900.0, -700.0)                   # open ground
    res = build(make(highlight_lon=float(lon), highlight_lat=float(lat), color="mono"), src)
    assert res.stats["highlight"] == "marker" and not all_closed(res)


def test_route_ribbon_plaque_and_frames():
    res = build(make(track=GPX, plaque=True, frame_style="bevel", plaque_text="Mi barrio"), FakeSources())
    st = res.stats
    assert st["track"] and st["plaque"] and "track" in res.parts and "plaque" in res.parts
    assert any("widened" in n for n in res.notes)
    assert not all_closed(res)
    res = build(make(frame_mm=14, frame_style="step", plaque=True, color="mono"), FakeSources())
    assert res.stats["plaque"] and not all_closed(res)


def test_squares_have_their_colour():
    res = build(make(), FakeSources())
    assert res.stats["plazas"] and "plazas" in res.parts


def test_street_seams_and_thickening():
    res = build(make(tiles_x=2, tiles_y=2, tile_cuts="streets", thicken=True), FakeSources())
    assert len(res.tiles) == 4 and not all_closed(res)
    assert res.stats["printability"]["score"] >= 0
    assert res.stats["printability"]["buildings"]["thin_walls"] == 0      # thin buildings are widened to two passes
    W, H = res.params.model_size_mm
    bb = res.parts["buildings"].bounding_box()
    assert bb[0] >= -0.01 and bb[1] >= -0.01 and bb[3] <= W + 0.01 and bb[4] <= H + 0.01   # and stay inside the model


def test_surface_model_heights(monkeypatch):
    src = FakeSources()
    p = make()
    from gulliver_hoard.sources import osm
    feats = osm.parse(src.osm(p.area.bbox_padded(100)), p.area.projector, p.area.width_m, p.area.height_m, pad_m=100)
    proj = p.area.projector
    bbox = p.area.bbox_padded(30.0)
    res_lat = 1.0 / 111320.0
    lon_res = res_lat / np.cos(np.radians(p.lat))
    w, h = int((bbox[2] - bbox[0]) / lon_res), int((bbox[3] - bbox[1]) / res_lat)
    lons = bbox[0] + (np.arange(w) + 0.5) * lon_res
    lats = bbox[3] - (np.arange(h) + 0.5) * res_lat
    LON, LAT = np.meshgrid(lons, lats)
    X, Y = proj.fwd(LON, LAT)
    import shapely
    grid = np.zeros((h, w), dtype=np.float32)
    plain = [b for b in feats.buildings if not b.part]
    union = shapely.union_all([b.shape for b in plain])
    grid[shapely.contains_xy(union, X, Y)] = 18.0
    grid[(abs(X - 320) < 5) & (abs(Y + 300) < 5)] = 60.0
    monkeypatch.setattr(src, "dsm", lambda bbox, res_m=1.0, progress=None: (grid, (bbox[0], bbox[3] - h * res_lat,
                                                                                   bbox[0] + w * lon_res, bbox[3]), "Test DSM", True), raising=False)
    monkeypatch.setattr(dsm_mod, "provider_for", lambda bbox, keys=None: object())
    res = build(make(), src)
    assert res.stats["dsm_buildings"] > 50
    assert "Test DSM" in res.attribution and not all_closed(res)


def test_fields_with_rows():
    res = build(make(), FakeSources())
    assert res.stats["fields"] >= 4 and res.stats["field_rows"] and "fields" in res.parts
    assert not all_closed(res)
    res = build(make(color="mono"), FakeSources())
    assert res.stats["fields"] >= 4 and not all_closed(res)


def test_cadastre_parcels_parse():
    from gulliver_hoard.sources import catastro
    gml = b"""<?xml version="1.0" encoding="ISO-8859-1"?><FeatureCollection xmlns:gml="http://www.opengis.net/gml/3.2"
    xmlns:cp="http://inspire.ec.europa.eu/schemas/cp/4.0" xmlns="http://www.opengis.net/wfs/2.0"><member>
    <cp:CadastralParcel gml:id="ES.SDGC.CP.34048A01300009"><cp:geometry><gml:MultiSurface><gml:surfaceMember><gml:Surface>
    <gml:patches><gml:PolygonPatch><gml:exterior><gml:LinearRing><gml:posList srsDimension="2" count="5">41.95 -4.958 41.95 -4.955
    41.952 -4.955 41.952 -4.958 41.95 -4.958</gml:posList></gml:LinearRing></gml:exterior></gml:PolygonPatch></gml:patches>
    </gml:Surface></gml:surfaceMember></gml:MultiSurface></cp:geometry></cp:CadastralParcel></member><member>
    <cp:CadastralParcel gml:id="ES.SDGC.CP.0812345DF3801E"><cp:geometry><gml:MultiSurface><gml:surfaceMember><gml:Surface>
    <gml:patches><gml:PolygonPatch><gml:exterior><gml:LinearRing><gml:posList srsDimension="2" count="4">41.95 -4.958 41.95 -4.957
    41.951 -4.957 41.95 -4.958</gml:posList></gml:LinearRing></gml:exterior></gml:PolygonPatch></gml:patches></gml:Surface>
    </gml:surfaceMember></gml:MultiSurface></cp:geometry></cp:CadastralParcel></member></FeatureCollection>"""
    items = catastro.parse_parcels(gml)
    assert len(items) == 2 and items[0]["rustic"] and not items[1]["rustic"]
    from gulliver_hoard.geo import Projector
    polys = catastro.to_parcels(items, Projector(-4.9565, 41.951))
    assert len(polys) == 1 and 50000 < polys[0].area < 60000


def test_framed_panels():
    res = build(make(panels_x=2, plaque=True), FakeSources(), name="Lilliput")
    assert len(res.tiles) == 2 and all(t.get("panel") for t in res.tiles)
    assert all("frame" in t["parts"] and "plaque" in t["parts"] for t in res.tiles)
    assert not all_closed(res)
    res = build(make(panels_x=2, panels_y=2, color="mono", frame_style="bevel"), FakeSources())
    assert len(res.tiles) == 4 and not all_closed(res)


def test_null_clears_highlight_and_track_frames_the_model(services):
    from gulliver_hoard.agent_tools import _around_track
    base = {**DEMO, "highlight_lon": -3.7, "highlight_lat": 40.42}
    p = services.make_params({"highlight_lon": None, "highlight_lat": None}, base=base)
    assert p.highlight_lon is None and p.highlight_lat is None
    v = _around_track({"track": GPX})
    assert abs(v["lon"] + 3.702) < 0.01 and abs(v["lat"] - 40.42) < 0.01 and v["width_m"] >= 300


def test_radar_pits_are_filled_but_valleys_kept():
    from gulliver_hoard.mesh.build import _fill_pits
    z = np.fromfunction(lambda i, j: 20 + 0.05 * i, (120, 100))
    z[60:63, 40:43] -= 25                     # a void of the radar: a cone a few cells across
    z[20:24, :] -= 3                          # a valley open to the border
    big = z.copy(); big[70:110, 10:90] -= 6   # a wide closed basin (a real one): left alone
    out, n = _fill_pits(big, 3.0, max_area_m2=20000.0)
    assert n == 1 and out[61, 41] > 20 and out[22, 50] == big[22, 50] and out[90, 50] == big[90, 50]


def test_rows_follow_the_direction_on_the_photo():
    from gulliver_hoard.sources import aerial as aerial_mod

    class Photo(FakeSources):
        def aerial_photo(self, bbox, m_per_px, progress=None):
            size = 600
            yy, xx = np.mgrid[0:size, 0:size].astype(float)
            a = np.radians(60.0)
            v = 128 + 60 * np.sign(np.sin(2 * np.pi * (-xx * np.sin(a) - yy * np.cos(a)) / 10.0))
            return np.repeat(v[:, :, None], 3, axis=2).astype(np.uint8), bbox

    res = build(make(), Photo())
    assert res.stats["rows_from_photo"] >= 1 and aerial_mod.ROWS_ATTRIBUTION in res.attribution
    plain = build(make(rows_from_photo=False), Photo())
    assert plain.stats["rows_from_photo"] == 0 and aerial_mod.ROWS_ATTRIBUTION not in plain.attribution


def test_plaque_and_label_carry_the_model_name_in_colour(monkeypatch):
    from gulliver_hoard.mesh import build as build_mod
    seen = []
    real = build_mod.plaque_mod.plaque_layout
    monkeypatch.setattr(build_mod.plaque_mod, "plaque_layout", lambda *a, **kw: seen.append(kw["title"]) or real(*a, **kw))
    res = build(make(plaque=True, color="color"), FakeSources(), name="Lilliput")
    assert res.stats["plaque"] and seen and set(seen) == {"Lilliput"}


def test_hedges_become_rows_of_bushes():
    from shapely.geometry import LineString
    from gulliver_hoard.mesh.build import _to_mm
    res = build(make(tree_size_mm=0.7), FakeSources())         # fine enough for single trees
    p = res.params
    hedge = _to_mm(LineString([(850, -650), (1000, -640)]), p.scale_mm_per_m, p.area.width_m, p.area.height_m)
    P, _R = res.footprints["trees"]
    near = [pt for pt in np.asarray(P) if hedge.distance(__import__("shapely").Point(pt)) < 0.3]
    assert len(near) >= 3
