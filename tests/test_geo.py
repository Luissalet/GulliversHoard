import math

import pytest

from gulliver_hoard.geo import Area, Projector, format_coord, metres_per_degree, nice_scale


def test_projector_round_trip_and_scale():
    p = Projector(-3.7, 40.42)
    x, y = p.fwd(-3.69, 40.43)
    lon, lat = p.inv(x, y)
    assert lon == pytest.approx(-3.69) and lat == pytest.approx(40.43)
    kx, ky = metres_per_degree(40.42)
    assert 84000 < kx < 85500 and 110900 < ky < 111100     # WGS84 radii at Madrid's latitude
    assert metres_per_degree(0)[0] == pytest.approx(111319.5, abs=1)


def test_area_box_and_back():
    a = Area(-3.7, 40.42, 2000, 1000)
    lon_min, lat_min, lon_max, lat_max = a.bbox
    b = Area.from_bbox(lon_min, lat_min, lon_max, lat_max)
    assert b.width_m == pytest.approx(2000, rel=1e-3) and b.height_m == pytest.approx(1000, rel=1e-3)
    assert a.km2 == pytest.approx(2.0)
    with pytest.raises(ValueError):
        Area.from_bbox(1, 1, 0, 2)


def test_scale_text_and_coordinates():
    assert nice_scale(5432) == "1:5.400"
    assert nice_scale(123456) == "1:120.000"
    assert nice_scale(850) == "1:850"
    assert format_coord(-3.7, 40.42) == "40.4200° N  3.7000° W"
    assert math.isfinite(Area(0, 0, 100, 100).bbox[0])
