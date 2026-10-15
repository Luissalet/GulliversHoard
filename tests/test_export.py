import io
import struct
import zipfile

import numpy as np
import trimesh

from gulliver_hoard.mesh import export as E
from gulliver_hoard.mesh.build import build
from gulliver_hoard.params import ModelParams
from gulliver_hoard.sources.fake import FakeSources

from conftest import DEMO


def result(**kw):
    return build(ModelParams(**{**DEMO, "size_mm": 100, "resolution_mm": 0.6, **kw}), FakeSources(), name="Lilliput")


def test_stl_is_binary_and_closed():
    r = result(color="mono")
    data = E.stl_bytes(r.single, "x")
    n = struct.unpack("<I", data[80:84])[0]
    assert len(data) == 84 + 50 * n
    mesh = trimesh.load(io.BytesIO(data), file_type="stl")
    assert mesh.is_watertight and abs(mesh.volume - r.single.volume()) < 1.0


def test_3mf_has_one_coloured_part_per_layer():
    r = result(color="color")
    data = E.threemf_bytes(r.parts, title="Lilliput", thumbnail=b"\x89PNG\r\n\x1a\n")
    z = zipfile.ZipFile(io.BytesIO(data))
    model = z.read("3D/3dmodel.model").decode()
    assert model.count("<object ") == len(r.parts) + 1 and "<components>" in model and 'unit="millimeter"' in model
    assert "displaycolor=\"#5E9FD1FF\"" in model and 'name="Agua"' in model
    assert "Metadata/thumbnail.png" in z.namelist()
    scene = trimesh.load(io.BytesIO(data), file_type="3mf")
    assert set(scene.geometry.keys()) >= {"Terreno", "Edificios", "Agua"}


def test_glb_names_groups_and_parts():
    r = result(color="color", tiles_x=2)
    glb = E.glb_bytes([(t["id"], t["parts"]) for t in r.tiles])
    scene = trimesh.load(io.BytesIO(glb), file_type="glb")
    names = set(scene.graph.nodes_geometry)
    assert any(n.startswith("A1__terrain") for n in names) and any(n.startswith("A2__") for n in names)


def test_thumbnail_is_a_png_cut_to_the_outline():
    r = result(color="color", shape="circle", frame_mm=3)
    png = E.thumbnail_png(r, size=200)
    from PIL import Image
    img = Image.open(io.BytesIO(png))
    assert img.size[0] == 200 and img.mode == "RGBA"
    a = np.asarray(img)[..., 3]
    assert a[0, 0] == 0 and a[100, 100] == 255           # corner outside the circle is transparent


def test_3mf_keeps_every_vertex_apart_so_slicers_read_closed_parts():
    import re
    r = result(color="color")
    data = E.threemf_bytes(r.parts, title="Lilliput")
    model = zipfile.ZipFile(io.BytesIO(data)).read("3D/3dmodel.model").decode()
    for chunk in re.findall(r"<mesh>(.*?)</mesh>", model):
        coords = re.findall(r'<vertex x="([^"]+)" y="([^"]+)" z="([^"]+)"', chunk)
        tris = np.array(re.findall(r'<triangle v1="(\d+)" v2="(\d+)" v3="(\d+)"', chunk), dtype=np.int64)
        assert len(set(coords)) == len(coords)                        # no two vertices written at the same place
        edges = np.sort(np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]]), axis=1)
        _u, counts = np.unique(edges, axis=0, return_counts=True)
        assert (counts == 2).all()


def test_vertex_format_falls_back_to_full_precision():
    near = np.array([[4.0, 10.0, 1.0], [np.nextafter(np.float32(4.0), np.float32(5.0)), 10.0, 1.0]], dtype=np.float32)
    assert near[0, 0] != near[1, 0]
    assert E._vertex_format(near) == ".9g"
    assert E._vertex_format(np.array([[1.0, 2.0, 3.0], [1.5, 2.0, 3.0]], dtype=np.float32)) == ".6f"


def test_thumbnail_shades_the_relief_under_rock_and_snow():
    from PIL import Image
    from gulliver_hoard.sources.dem import Composite, DemGrid

    class Hills(FakeSources):
        def dem(self, bbox, want_res_m, **_):
            lon_min, lat_min, lon_max, lat_max = bbox
            u = np.linspace(-1, 1, 200)
            U, V = np.meshgrid(u, u)
            data = (900.0 + 2400.0 * np.exp(-(U ** 2 + V ** 2) / 0.2) + 300 * np.sin(U * 9) * np.cos(V * 7)).astype(np.float32)
            step = ((lon_max - lon_min) / 199, (lat_max - lat_min) / 199)
            return Composite(DemGrid(data, "lonlat", (lon_min, lat_max), step, source="hills", res_m=25), None)

        def osm(self, bbox, **_):
            return {"elements": []}

    p = ModelParams(lon=7.0, lat=46.0, width_m=8000, height_m=8000, size_mm=100, resolution_mm=0.8, buildings=False,
                    roads="none", color="color", z_exag=3.0, rock_slope_deg=12)
    r = build(p, Hills(), name="Pico")
    assert "rock" in r.stats["colour_layers"]
    img = np.asarray(Image.open(io.BytesIO(E.thumbnail_png(r, size=200))).convert("L")).astype(float)
    assert np.percentile(img, 95) - np.percentile(img, 5) > 60        # the slopes facing the sun and away from it differ
