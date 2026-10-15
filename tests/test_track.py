import json

import pytest

from gulliver_hoard import track
from gulliver_hoard.errors import GulliverError
from gulliver_hoard.geo import Projector

GPX = """﻿<?xml version="1.0"?>
<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
  <wpt lat="40.0" lon="-3.0"><name>ignored</name></wpt>
  <trk><trkseg>
    <trkpt lat="40.0" lon="-3.0"><ele>600</ele></trkpt>
    <trkpt lat="40.001" lon="-3.0"/>
    <trkpt lat="40.002" lon="-3.001"/>
  </trkseg><trkseg>
    <trkpt lat="40.01" lon="-3.01"/>
    <trkpt lat="40.011" lon="-3.011"/>
  </trkseg></trk>
  <rte><rtept lat="41.0" lon="-4.0"/><rtept lat="41.1" lon="-4.1"/></rte>
</gpx>"""

KML = """<?xml version="1.0"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><LineString>
<coordinates>-3.0,40.0,0 -3.001,40.001,0
 -3.002,40.002,0</coordinates></LineString></Placemark></Document></kml>"""

KML_GX = """<?xml version="1.0"?>
<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2"><Document><Placemark>
<gx:Track><when>2020-01-01T00:00:00Z</when><when>2020-01-01T00:01:00Z</when>
<gx:coord>-3.0 40.0 600</gx:coord><gx:coord>-3.01 40.01 610</gx:coord></gx:Track>
</Placemark></Document></kml>"""

GEOJSON = json.dumps({
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "properties": {}, "geometry": {"type": "LineString", "coordinates": [[-3, 40], [-3.1, 40.1]]}},
        {"type": "Feature", "properties": {}, "geometry": {"type": "MultiLineString", "coordinates": [
            [[-4, 41], [-4.1, 41.1], [-4.2, 41.2]], [[-5, 42], [-5.1, 42.1]]]}},
        {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [-3, 40]}},
    ],
})


def test_gpx_segments_and_route():
    lines = track.parse(GPX)
    assert len(lines) == 3
    assert lines[0] == [(-3.0, 40.0), (-3.0, 40.001), (-3.001, 40.002)]
    assert lines[2] == [(-4.0, 41.0), (-4.1, 41.1)]


def test_kml_linestring():
    lines = track.parse(KML)
    assert lines == [[(-3.0, 40.0), (-3.001, 40.001), (-3.002, 40.002)]]


def test_kml_gx_track():
    assert track.parse(KML_GX) == [[(-3.0, 40.0), (-3.01, 40.01)]]


def test_geojson_feature_collection():
    lines = track.parse(GEOJSON)
    assert len(lines) == 3
    assert lines[1] == [(-4, 41), (-4.1, 41.1), (-4.2, 41.2)]


def test_geojson_bare_linestring():
    text = json.dumps({"type": "LineString", "coordinates": [[1, 2], [3, 4]]})
    assert track.parse(text) == [[(1.0, 2.0), (3.0, 4.0)]]


def test_duplicates_and_short_lines_dropped():
    text = json.dumps({"type": "MultiLineString", "coordinates": [
        [[1, 2], [1, 2], [1, 2], [3, 4], [3, 4]], [[5, 6], [5, 6]], [[7, 8]]]})
    assert track.parse(text) == [[(1.0, 2.0), (3.0, 4.0)]]


def test_out_of_range_points_dropped():
    text = json.dumps({"type": "LineString", "coordinates": [[1, 2], [200, 4], [3, 95], [3, 4]]})
    assert track.parse(text) == [[(1.0, 2.0), (3.0, 4.0)]]


@pytest.mark.parametrize("text", ["", "hello world", "<gpx><nope></gpx>", "{not json", "<html><body/></html>",
                                  json.dumps({"type": "Point", "coordinates": [1, 2]}),
                                  '<gpx version="1.1"><trk><trkseg><trkpt lat="1" lon="1"/></trkseg></trk></gpx>'])
def test_garbage_raises_bad_track(text):
    with pytest.raises(GulliverError) as e:
        track.parse(text)
    assert e.value.code == "bad_track"
    assert e.value.hint


def test_too_many_points(monkeypatch):
    monkeypatch.setattr(track, "MAX_POINTS", 5)
    text = json.dumps({"type": "LineString", "coordinates": [[i / 10, i / 10] for i in range(10)]})
    with pytest.raises(GulliverError) as e:
        track.parse(text)
    assert e.value.code == "bad_track"


def test_bbox():
    assert track.bbox(track.parse(GEOJSON)) == (-5.1, 40.0, -3.0, 42.1)


def test_to_metres():
    lines = [[(-3.0, 40.0), (-3.0, 40.001), (-3.0, 40.002)]]
    p = Projector(-3.0, 40.0)
    out = track.to_metres(lines, p)
    assert len(out) == 1
    xs, ys = zip(*out[0].coords)
    assert max(abs(x) for x in xs) < 1e-6
    assert ys[0] == pytest.approx(0)
    assert ys[-1] == pytest.approx(0.002 * p.ky, rel=1e-6)
    assert len(out[0].coords) == 2  # collinear middle point simplified away


def test_centre_and_size():
    lines = [[(-3.0, 40.0), (-2.99, 40.01)]]
    lon, lat, w, h = track.centre_and_size(lines)
    assert lon == pytest.approx(-2.995)
    assert lat == pytest.approx(40.005)
    assert h == pytest.approx(0.01 * 111320 * 1.3)
    assert w == pytest.approx(0.01 * 111320 * 0.766044 * 1.3, rel=1e-3)


def test_centre_and_size_minimum():
    _, _, w, h = track.centre_and_size([[(-3.0, 40.0), (-3.0001, 40.0001)]])
    assert w == 300 and h == 300
