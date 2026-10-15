"""Where the data comes from. :class:`Sources` talks to the real services (with the disk cache); :class:`~.fake.FakeSources`
invents a small town for the demo mode and the tests, with the same interface."""

from __future__ import annotations

from typing import Any, Callable, Optional

from . import aerial as _aerial
from . import catastro as _catastro
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

    def osm(self, bbox, *, progress: Optional[Callable[[str], None]] = None, cancelled: Optional[Callable[[], bool]] = None,
            rounds: int = 3) -> dict[str, Any]:
        return _osm.fetch_raw(self.fetcher, bbox, self.overpass_servers, progress=progress, cancelled=cancelled, rounds=rounds)

    def catastro(self, bbox, *, progress: Optional[Callable[[str], None]] = None,
                 cancelled: Optional[Callable[[], bool]] = None) -> Optional[list[dict[str, Any]]]:
        """Building parts from the Spanish cadastre, or None where it has no data."""
        if not _catastro.covers(bbox):
            return None
        return _catastro.fetch_parts(self.fetcher, bbox, progress=progress, cancelled=cancelled)

    def aerial_trees(self, bbox, projector, *, progress: Optional[Callable[[str], None]] = None) -> Optional[list]:
        """Trees found on the aerial photo (Spain), or None where there is no photo to read."""
        if not _aerial.covers(bbox):
            return None
        rgb, box = _aerial.fetch(self.fetcher, bbox, progress=progress)
        return _aerial.trees(rgb, box, projector)

    def aerial_cover(self, bbox, m_per_px: float, *, progress: Optional[Callable[[str], None]] = None) -> Optional[tuple]:
        """Ground cover read from the aerial photo (Spain) at about ``m_per_px``: (masks, photo bbox), or None."""
        if not _aerial.covers(bbox):
            return None
        rgb, box = _aerial.fetch(self.fetcher, bbox, m_per_px=m_per_px, progress=progress)
        return _aerial.cover(rgb), box

    def search(self, query: str, limit: int = 6, language: str = "es") -> list[dict[str, Any]]:
        return _geocode.search(self.fetcher, query, limit, language)
