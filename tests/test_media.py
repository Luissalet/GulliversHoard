"""Photo / turntable uploads: validation, storage in the current build folder and serving."""

import pytest

from gulliver_hoard.errors import GulliverError
from gulliver_hoard.media import MAX_BYTES, save_media

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 64
BUILD = {"lon": -3.70, "lat": 40.42, "width_m": 600, "height_m": 450, "size_mm": 50, "resolution_mm": 0.8}


@pytest.fixture
def model(call):
    return call("model_create", {"name": "Foto", "params": BUILD})["id"]


@pytest.fixture
def unbuilt(call):
    return call("model_create", {"name": "Sin construir", "build": False, "params": BUILD})["id"]


def post(client, mid, kind, data, ctype):
    return client.post(f"/api/models/{mid}/media/{kind}", content=data, headers={"Content-Type": ctype})


def test_photo_and_video_roundtrip(client, model):
    r = post(client, model, "photo", PNG, "image/png")
    assert r.status_code == 200, r.text
    assert r.json() == {"name": "photo.png", "url": f"/api/models/{model}/media/photo.png", "bytes": len(PNG)}
    r = post(client, model, "video", WEBM, "video/webm;codecs=vp9")
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "turntable.webm"

    got = client.get(f"/api/models/{model}/media/photo.png")
    assert got.status_code == 200 and got.content == PNG and got.headers["content-type"] == "image/png"
    got = client.get(f"/api/models/{model}/media/turntable.webm?download=1")
    assert got.content == WEBM and got.headers["content-type"] == "video/webm"
    assert "attachment" in got.headers["content-disposition"]
    # the generic build-file route serves them too
    assert client.get(f"/api/models/{model}/files/photo.png").content == PNG

    listing = client.get(f"/api/models/{model}/media").json()
    assert [(m["kind"], m["name"], m["bytes"]) for m in listing["media"]] == [("photo", "photo.png", len(PNG)), ("video", "turntable.webm", len(WEBM))]


def test_replaces_previous(client, model):
    post(client, model, "photo", PNG, "image/png")
    newer = PNG + b"x" * 10
    assert post(client, model, "photo", newer, "image/png").json()["bytes"] == len(newer)
    assert client.get(f"/api/models/{model}/media/photo.png").content == newer


def test_empty_listing(client, model):
    assert client.get(f"/api/models/{model}/media").json()["media"] == []
    assert client.get(f"/api/models/{model}/media/photo.png").status_code == 404
    assert client.get(f"/api/models/{model}/media/other.png").status_code == 404


def test_wrong_magic_bytes(client, model):
    assert post(client, model, "photo", WEBM, "image/png").status_code == 400
    assert post(client, model, "video", PNG, "video/webm").status_code == 400
    assert post(client, model, "photo", b"", "image/png").status_code == 400


def test_wrong_content_type_and_unknown_kind(client, model):
    assert post(client, model, "photo", PNG, "application/json").status_code == 400
    r = post(client, model, "gif", PNG, "image/png")
    assert r.status_code == 400 and r.json()["code"] == "invalid" and r.json()["hint"]


def test_model_without_build_or_unknown(client, unbuilt):
    r = post(client, unbuilt, "photo", PNG, "image/png")
    assert r.status_code == 404 and r.json()["code"] == "not_found"
    assert client.get(f"/api/models/{unbuilt}/media").json()["media"] == []
    assert post(client, "nope", "photo", PNG, "image/png").status_code == 404


def test_too_large(services, model):
    with pytest.raises(GulliverError) as exc:
        save_media(services, model, "photo", PNG + b"\x00" * MAX_BYTES, "image/png")
    assert exc.value.code == "too_large" and exc.value.status == 413
