"""Where the data comes from. :class:`Sources` talks to the real services (with the disk cache); :class:`~.fake.FakeSources`
invents a small town for the demo mode and the tests, with the same interface."""

from __future__ import annotations

from typing import Any, Callable, Optional

from . import dem as _dem
from . import geocode as _geocode
from . import osm as _osm
from .http import Fetcher


class Sources:
    fake = False

    def __init__(self, fetcher: Fetcher, *, overpass_servers: Optional[list[str]] = None):
        self.fetcher = fetcher
        self.overpass_servers = overpass_servers or list(_osm.DEFAULT_SERVERS)

    def dem(self, bbox, want_res_m: float, *, prefer: str = "auto", progress: Optional[Callable[[str], None]] = None) -> Any:
        return _dem.fetch_dem(self.fetcher, bbox, want_res_m, prefer=prefer, progress=progress)

    def osm(self, bbox, *, progress: Optional[Callable[[str], None]] = None, cancelled: Optional[Callable[[], bool]] = None) -> dict[str, Any]:
        return _osm.fetch_raw(self.fetcher, bbox, self.overpass_servers, progress=progress, cancelled=cancelled)

    def search(self, query: str, limit: int = 6, language: str = "es") -> list[dict[str, Any]]:
        return _geocode.search(self.fetcher, query, limit, language)
