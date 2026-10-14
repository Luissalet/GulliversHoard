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
