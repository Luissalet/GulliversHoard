import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from gulliver_hoard.config import Config  # noqa: E402
from gulliver_hoard.main import create_app  # noqa: E402
from gulliver_hoard.services import Services  # noqa: E402
from gulliver_hoard.sources.fake import FakeSources  # noqa: E402

DEMO = {"lon": -3.70, "lat": 40.42, "width_m": 2400, "height_m": 1800}


@pytest.fixture(autouse=True)
def hermetic(monkeypatch, tmp_path):
    for key in ("GULLIVER_DATA_DIR", "GULLIVER_FAKE", "GULLIVER_PORT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOARD_HUB_URL", "http://127.0.0.1:9")      # no hub: family calls fail fast
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))


@pytest.fixture
def services(tmp_path):
    cfg = Config(data_dir=tmp_path / "data", fake=True, background=False)
    s = Services(cfg, sources=FakeSources())
    yield s
    s.stop()


@pytest.fixture
def client(services):
    app = create_app(services.config, services=services)
    with TestClient(app, base_url="http://127.0.0.1:5206") as c:
        yield c


@pytest.fixture
def call(client):
    def _call(name, args=None, status=200):
        r = client.post("/api/ui/call", json={"name": name, "arguments": args or {}})
        assert r.status_code == status, r.text
        return r.json()
    return _call
