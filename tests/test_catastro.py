import numpy as np

from gulliver_hoard.geo import Projector
from gulliver_hoard.mesh.build import build
from gulliver_hoard.outputs import _check
from gulliver_hoard.params import ModelParams
from gulliver_hoard.sources import catastro
from gulliver_hoard.sources.fake import FakeSources

GML = b"""<?xml version="1.0" encoding="ISO-8859-1"?>
<gml:FeatureCollection gml:id="ES.SDGC.BU" xmlns:gml="http://www.opengis.net/gml/3.2"
  xmlns:bu-ext2d="http://inspire.jrc.ec.europa.eu/schemas/bu-ext2d/2.0" xmlns:bu-core2d="http://inspire.jrc.ec.europa.eu/schemas/bu-core2d/2.0">
  <gml:featureMember>
    <bu-ext2d:BuildingPart gml:id="ES.SDGC.BU.1_part1">
      <bu-ext2d:geometry><bu-core2d:BuildingGeometry><bu-core2d:geometry>
        <gml:Surface><gml:patches><gml:PolygonPatch>
          <gml:exterior><gml:LinearRing><gml:posList srsDimension="2" count="5">40.4200 -3.7000 40.4200 -3.6996 40.4203 -3.6996 40.4203 -3.7000 40.4200 -3.7000</gml:posList></gml:LinearRing></gml:exterior>
          <gml:interior><gml:LinearRing><gml:posList srsDimension="2" count="5">40.42010 -3.69990 40.42010 -3.69970 40.42020 -3.69970 40.42020 -3.69990 40.42010 -3.69990</gml:posList></gml:LinearRing></gml:interior>
        </gml:PolygonPatch></gml:patches></gml:Surface>
      </bu-core2d:geometry></bu-core2d:BuildingGeometry></bu-ext2d:geometry>
      <bu-ext2d:numberOfFloorsAboveGround>6</bu-ext2d:numberOfFloorsAboveGround>
    </bu-ext2d:BuildingPart>
  </gml:featureMember>
  <gml:featureMember>
    <bu-ext2d:BuildingPart gml:id="ES.SDGC.BU.1_part2">
      <bu-ext2d:geometry><bu-core2d:BuildingGeometry><bu-core2d:geometry>
        <gml:Surface><gml:patches><gml:PolygonPatch>
          <gml:exterior><gml:LinearRing><gml:posList srsDimension="2" count="5">40.4190 -3.7000 40.4190 -3.6998 40.4192 -3.6998 40.4192 -3.7000 40.4190 -3.7000</gml:posList></gml:LinearRing></gml:exterior>
        </gml:PolygonPatch></gml:patches></gml:Surface>
      </bu-core2d:geometry></bu-core2d:BuildingGeometry></bu-ext2d:geometry>
      <bu-ext2d:numberOfFloorsAboveGround>0</bu-ext2d:numberOfFloorsAboveGround>
    </bu-ext2d:BuildingPart>
  </gml:featureMember>
</gml:FeatureCollection>"""


def test_parse_parts_with_courtyards_and_floors():
    parts = catastro.parse(GML)
    assert [p["floors"] for p in parts] == [6, 0]
    assert len(parts[0]["inners"]) == 1 and parts[0]["outer"][0] == (-3.7, 40.42)
    proj = Projector(-3.7, 40.42)
    from shapely.geometry import box
    b = catastro.to_buildings(parts, proj, box(-500, -500, 500, 500))
    assert len(b) == 1 and b[0].height_m == 6 * catastro.FLOOR_M + catastro.GROUND_EXTRA_M
    assert len(b[0].shape.interiors) == 1
    assert catastro.covers((-3.71, 40.41, -3.69, 40.43)) and not catastro.covers((2.33, 48.85, 2.36, 48.87))


def test_cells_cover_the_box_and_full_answers_split():
    cells = catastro._cells((0.0, 0.0, 0.012, 0.007), 0.005)
    assert len(cells) == 6 and cells[-1][2] == 0.012 and cells[-1][3] == 0.007


class Cadastre(FakeSources):
    """The demo town with its buildings replaced by cadastre blocks: towers of 9 floors and a low row of houses."""

    def catastro(self, bbox, **_):
        lon0, lat0 = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
        proj = Projector(lon0, lat0)
        parts = []
        for i, (x, y, f) in enumerate([(300, -250, 9), (330, -250, 4), (-200, -600, 2), (-185, -600, 2), (-170, -600, 2)]):
            ring = [(x - 12, y - 8), (x + 12, y - 8), (x + 12, y + 8), (x - 12, y + 8), (x - 12, y - 8)]
            lonlat = [proj.inv(px, py) for px, py in ring]
            parts.append({"outer": lonlat, "inners": [], "floors": f, "id": str(i)})
        return parts


def test_cadastre_buildings_replace_osm_but_keep_the_church_parts():
    p = ModelParams(lon=-3.70, lat=40.42, width_m=2400, height_m=1800, size_mm=120, resolution_mm=0.5, color="color")
    r = build(p, Cadastre(), name="Catastro")
    assert r.stats["building_source"] == "cadastre"
    assert r.stats["buildings"] >= 5 + 3            # the five parts and the church's three
    assert _check(r.single)["watertight"]
    assert any("Catastro" in a for a in r.attribution)
