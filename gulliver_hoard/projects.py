"""Projects on disk: ``<data>/projects/<id>/project.json`` plus ``builds/<build id>/`` (the files of each finished build).

A project is a named place with its parameters; building it makes a new build folder, and the project points at the newest
finished one. Older builds are pruned to ``keep_builds``."""

from __future__ import annotations

import re
import secrets
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .errors import GulliverError
from .hoard_link.atomic import read_json, write_json_atomic
from .params import ModelParams

ID_RE = re.compile(r"^[a-z0-9]{6,32}$")


def new_id() -> str:
    return time.strftime("%y%m%d") + secrets.token_hex(3)


class Projects:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # -- paths ----------------------------------------------------------------------------------------------------------------
    def dir(self, pid: str) -> Path:
        if not ID_RE.match(pid or ""):
            raise GulliverError("not_found", f"No model with id {pid!r}.", "List them with model_list.")
        return self.folder / pid

    def build_dir(self, pid: str, build_id: str) -> Path:
        if not ID_RE.match(build_id or ""):
            raise GulliverError("not_found", f"No build {build_id!r}.")
        return self.dir(pid) / "builds" / build_id

    # -- records --------------------------------------------------------------------------------------------------------------
    def _read(self, pid: str) -> dict[str, Any]:
        data = read_json(self.dir(pid) / "project.json", None)
        if not isinstance(data, dict):
            raise GulliverError("not_found", f"No model with id {pid!r}.", "List them with model_list.")
        return data

    def _write(self, rec: dict[str, Any]) -> None:
        rec["updated"] = time.time()
        write_json_atomic(self.dir(rec["id"]) / "project.json", rec)

    def get(self, pid: str) -> dict[str, Any]:
        with self._lock:
            return self._read(pid)

    def list(self) -> list[dict[str, Any]]:
        out = []
        with self._lock:
            for child in self.folder.iterdir() if self.folder.is_dir() else []:
                if child.is_dir() and ID_RE.match(child.name):
                    rec = read_json(child / "project.json", None)
                    if isinstance(rec, dict):
                        out.append(rec)
        out.sort(key=lambda r: r.get("updated", 0), reverse=True)
        return out

    def create(self, name: str, params: ModelParams, *, place: str = "", preset: str = "") -> dict[str, Any]:
        with self._lock:
            pid = new_id()
            while (self.folder / pid).exists():
                pid = new_id()
            (self.folder / pid).mkdir(parents=True)
            rec = {"id": pid, "name": name.strip() or "Maqueta", "place": place, "preset": preset, "created": time.time(),
                   "params": params.model_dump(), "status": "draft", "build": None, "error": "", "job": None, "builds": []}
            self._write(rec)
            return rec

    def update(self, pid: str, **changes: Any) -> dict[str, Any]:
        with self._lock:
            rec = self._read(pid)
            for key, value in changes.items():
                if key == "params" and isinstance(value, ModelParams):
                    value = value.model_dump()
                rec[key] = value
            self._write(rec)
            return rec

    def delete(self, pid: str) -> dict[str, Any]:
        with self._lock:
            rec = self._read(pid)
            shutil.rmtree(self.dir(pid), ignore_errors=True)
            return {"deleted": pid, "name": rec.get("name", "")}

    def duplicate(self, pid: str, name: str = "") -> dict[str, Any]:
        rec = self.get(pid)
        return self.create(name or f"{rec['name']} (copia)", ModelParams(**rec["params"]), place=rec.get("place", ""),
                           preset=rec.get("preset", ""))

    def finish_build(self, pid: str, build_id: str, report: dict[str, Any], keep: int) -> dict[str, Any]:
        with self._lock:
            rec = self._read(pid)
            builds = [b for b in rec.get("builds", []) if b != build_id]
            builds.insert(0, build_id)
            for old in builds[keep:]:
                shutil.rmtree(self.build_dir(pid, old), ignore_errors=True)
            rec["builds"] = builds[:keep]
            rec["build"] = {"id": build_id, "finished": time.time(), "report": summary(report)}
            rec["status"] = "ready"
            rec["error"] = ""
            self._write(rec)
            return rec

    def current_build_dir(self, pid: str) -> Optional[Path]:
        rec = self.get(pid)
        b = rec.get("build")
        return self.build_dir(pid, b["id"]) if b else None

    def report(self, pid: str) -> Optional[dict[str, Any]]:
        folder = self.current_build_dir(pid)
        return read_json(folder / "report.json", None) if folder else None

    def file(self, pid: str, name: str) -> Path:
        """A file of the current build, by its name relative to the build folder (no path tricks)."""
        folder = self.current_build_dir(pid)
        if folder is None:
            raise GulliverError("not_found", "This model has not been built yet.", "Build it with model_build.")
        parts = Path(name.replace("\\", "/")).parts
        if not parts or any(p in ("..", "") or ":" in p for p in parts) or Path(name).is_absolute():
            raise GulliverError("invalid", "Bad file name.")
        path = folder.joinpath(*parts)
        if not path.is_file():
            raise GulliverError("not_found", f"No file {name!r} in the current build.")
        return path


def summary(report: dict[str, Any]) -> dict[str, Any]:
    keys = ("scale", "size_mm", "volume_cm3", "weight_g_pla", "triangles", "buildings", "fits_bed", "files", "check")
    out = {k: report.get(k) for k in keys}
    out["tiles"] = len(report.get("tiles") or [])
    out["parts"] = sorted((report.get("parts") or {}).keys())
    return out
