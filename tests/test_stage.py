"""Map data is gathered once per area: a rebuild with other print settings reuses it."""

from gulliver_hoard.mesh.build import build
from gulliver_hoard.params import ModelParams
from gulliver_hoard.sources.fake import FakeSources
from gulliver_hoard.sources.http import Fetcher

from conftest import DEMO


class Counting(FakeSources):
    def __init__(self, fetcher):
        super().__init__()
        self.fetcher = fetcher
        self.calls = 0

    def osm(self, *a, **kw):
        self.calls += 1
        return super().osm(*a, **kw)


def test_rebuild_reuses_the_map_data(tmp_path):
    src = Counting(Fetcher(tmp_path / "cache"))
    p = ModelParams(**{**DEMO, "size_mm": 100, "resolution_mm": 0.6})
    a = build(p, src)
    b = build(p.model_copy(update={"frame_mm": 5.0, "plaque": True, "base_mm": 5.0}), src)
    assert src.calls == 1
    assert a.stats["buildings"] == b.stats["buildings"] and a.stats["trees"] == b.stats["trees"]
    build(p.model_copy(update={"trees": False}), src)
    assert src.calls == 2
