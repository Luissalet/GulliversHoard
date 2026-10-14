from gulliver_hoard.geo import Projector
from gulliver_hoard.sources import osm
from gulliver_hoard.sources.fake import town


def test_stitch_rings_joins_reversed_and_open_segments():
    a = [(0, 0), (1, 0), (1, 1)]
    b = [(0, 1), (1, 1)]          # reversed end
    c = [(0, 1), (0, 0)]
    rings = osm.stitch_rings([a, b, c])
    assert len(rings) == 1 and rings[0][0] == rings[0][-1] and len(rings[0]) == 5


def test_parse_demo_town():
    proj = Projector(-3.7, 40.42)
    f = osm.parse(town(proj), proj, 2400, 1800)
    s = f.summary()
    assert s["buildings"] > 150 and s["buildings_with_height"] > 10
    assert s["bridges"] == 1 and s["rails"] == 1 and s["waterways"] == 2
    # the lake (a multipolygon with its outer ring split in two ways) keeps the island as a hole
    lake = [g for g in osm.as_polygons(f.water) if abs(g.centroid.x + 700) < 50]
    assert lake and len(lake[0].interiors) == 1
    # the school (a building relation) keeps its courtyard
    school = [b for b in f.buildings if b.name == "Colegio"]
    assert school and len(osm.as_polygons(school[0].shape)[0].interiors) == 1
    assert "Torre Lilliput" in s["landmarks"]
    # green areas never overlap water
    assert f.green.intersection(f.water).area < 1.0


def test_building_heights():
    assert osm.building_height({"height": "12 m"})[0] == 12
    assert osm.building_height({"height": "40'"})[0] == round(40 * 0.3048, 4) or abs(osm.building_height({"height": "40'"})[0] - 12.19) < 0.01
    h, mh, est = osm.building_height({"building:levels": "4", "roof:levels": "1"})
    assert not est and abs(h - (4.5 * osm.LEVEL_M + 1)) < 1e-6
    h, mh, est = osm.building_height({"building": "garage"})
    assert est and h == 3.0
    assert osm.building_height({"height": "nonsense", "building": "church"})[0] == 18.0


def test_query_names_every_layer():
    q = osm.build_query((-3.71, 40.41, -3.69, 40.43))
    for word in ("building", "natural\"=\"water", "highway", "railway", "leisure", "riverbank", "40.410000,-3.710000"):
        assert word in q


def test_overpass_race_takes_the_first_good_answer(tmp_path):
    import json
    import time

    import httpx

    from gulliver_hoard.sources.http import Fetcher

    def handler(request):
        host = request.url.host
        if host == "slow.example":
            time.sleep(1.5)
            return httpx.Response(200, content=b'{"elements": [{"type": "node", "id": 1}]}')
        if host == "broken.example":
            return httpx.Response(504, text="busy")
        return httpx.Response(200, content=json.dumps({"elements": [], "remark": "ok"}).encode())

    f = Fetcher(tmp_path / "cache", transport=httpx.MockTransport(handler))
    seen = []
    t0 = time.monotonic()
    data = osm.fetch_raw(f, (-3.71, 40.41, -3.69, 40.43), ["https://slow.example/api", "https://broken.example/api", "https://good.example/api"],
                         progress=seen.append, stagger_s=0.3)
    assert data == {"elements": [], "remark": "ok"} and time.monotonic() - t0 < 1.4
    assert any("broken.example failed" in m for m in seen)
    again = osm.fetch_raw(f, (-3.71, 40.41, -3.69, 40.43), ["https://none.example/api"], progress=seen.append)
    assert again == data and seen[-1] == "OpenStreetMap: from the cache"


def test_overpass_all_fail(tmp_path):
    import httpx
    import pytest

    from gulliver_hoard.errors import GulliverError
    from gulliver_hoard.sources.http import Fetcher

    f = Fetcher(tmp_path / "cache", transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>rate limited</html>")))
    with pytest.raises(GulliverError) as err:
        osm.fetch_raw(f, (-3.71, 40.41, -3.69, 40.43), ["https://a.example/api", "https://b.example/api"], stagger_s=0.1)
    assert "a.example" in err.value.message and "b.example" in err.value.message
