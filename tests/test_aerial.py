import numpy as np

from gulliver_hoard.geo import Projector
from gulliver_hoard.sources import aerial


def picture(centres, radius_px=10, size=400):
    """Grey ground, a pale lawn and dark green crowns."""
    img = np.full((size, size, 3), (128, 124, 118), dtype=np.uint8)
    yy, xx = np.mgrid[0:size, 0:size]
    img[(xx > 300) & (yy > 300)] = (120, 150, 95)            # a bright lawn: not trees
    for cx, cy in centres:
        img[(xx - cx) ** 2 + (yy - cy) ** 2 <= radius_px ** 2] = (58, 82, 50)
    return img


def test_crowns_on_the_photo_become_trees_and_lawns_do_not():
    centres = [(60, 60), (120, 60), (180, 200), (240, 120)]
    img = picture(centres)
    lat0, lon0 = 40.0, -3.0
    dlat = 400 * 0.5 / 111320.0
    dlon = 400 * 0.5 / (111320.0 * np.cos(np.radians(lat0 + dlat / 2)))
    bbox = (lon0, lat0, lon0 + dlon, lat0 + dlat)
    proj = Projector(lon0 + dlon / 2, lat0 + dlat / 2)
    found = aerial.trees(img, bbox, proj)
    assert len(found) == len(centres)
    for x, y, _h, crown in found:
        assert 7.0 <= crown <= 14.0                               # 10 px * 0.5 m radius -> about 10 m across
    xs = sorted(round(x) for x, *_ in found)
    assert xs[0] < -60 and xs[-1] > 0
    assert aerial.covers((-3.71, 40.41, -3.69, 40.43)) and not aerial.covers((2.33, 48.85, 2.36, 48.87))


def stripes(angle_deg, size=400, period_px=8):
    """Bright and dark stripes running at ``angle_deg`` from east (rows north to south in the picture)."""
    yy, xx = np.mgrid[0:size, 0:size].astype(float)
    a = np.radians(angle_deg)
    across = -xx * np.sin(a) - yy * np.cos(a)            # distance across the stripes (picture y points south)
    v = 128 + 60 * np.sign(np.sin(2 * np.pi * across / period_px))
    return np.repeat(v[:, :, None], 3, axis=2).astype(np.uint8)


def test_row_direction_read_from_the_photo():
    from shapely.geometry import box as sbox
    lat0, lon0 = 41.9, -4.95
    dlat = 400 * 1.0 / 111320.0
    dlon = 400 * 1.0 / (111320.0 * np.cos(np.radians(lat0 + dlat / 2)))
    bbox = (lon0, lat0, lon0 + dlon, lat0 + dlat)
    proj = Projector(lon0 + dlon / 2, lat0 + dlat / 2)
    field = [sbox(-120, -120, 120, 120)]
    for want in (30.0, 115.0):
        got = aerial.row_angles(stripes(want), bbox, proj.inv, proj.fwd, field, erode=3.0)
        assert got[0] is not None and min(abs(got[0] - want), 180 - abs(got[0] - want)) < 4.0, (want, got)
    flat = np.full((400, 400, 3), 120, dtype=np.uint8)
    flat += np.random.default_rng(1).integers(0, 6, flat.shape, dtype=np.uint8)
    assert aerial.row_angles(flat, bbox, proj.inv, proj.fwd, field) == [None]
