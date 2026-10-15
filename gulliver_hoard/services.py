"""The app's services: settings, sources (real or demo), projects, the build queue, and what the tools and routes call."""

from __future__ import annotations

import logging
import math
import shutil
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Optional

from . import APP_ID, __version__
from .config import Config, Settings
from .errors import GulliverError
from .geo import format_coord, nice_scale
from .hoard_link import family
from .hoard_link.tokens import read_or_create_token
from .jobs import Job, Jobs
from .mesh.build import PART_COLORS, PART_LABELS, build
from .worker import run_isolated
from .outputs import write_outputs
from .params import MAX_CELLS, PRESETS, ModelParams, suggest_tiles
from .projects import Projects, new_id
from .sources import Sources
from .sources import dem as dem_mod
from .sources import osm as osm_mod
from .sources.http import Fetcher

log = logging.getLogger("gulliver")

RANK_NAMES = {0: "none", 1: "motorways", 2: "main roads", 3: "+ local through roads", 4: "+ residential streets",
              5: "+ service roads and tracks", 6: "every street and path"}


def model_ref(pid: str) -> str:
    return f"hoard://{APP_ID}/model/{pid}"


class Services:
    def __init__(self, config: Config, *, sources: Any = None):
        self.config = config
        config.data_dir.mkdir(parents=True, exist_ok=True)
        self.token = read_or_create_token(config.token_path)
        self.settings = Settings(config.settings_path)
        self.fetcher = Fetcher(config.cache_dir)
        if sources is not None:
            self.sources = sources
        elif config.fake:
            from .sources.fake import FakeSources
            self.sources = FakeSources(self.fetcher)
        else:
            self.sources = Sources(self.fetcher, overpass_servers=self.settings["overpass_servers"])
        self.projects = Projects(config.projects_dir)
        self.jobs = Jobs(config.jobs_path, background=config.background, on_change=self._job_changed)
        self._events: deque[dict[str, Any]] = deque(maxlen=300)
        self._lock = threading.RLock()

    def start(self) -> None:
        self.jobs.start()

    def stop(self) -> None:
        self.jobs.stop()
        self.fetcher.close()

    # -- events ----------------------------------------------------------------------------------------------------------
    def emit(self, kind: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._events.append({"t": time.time(), "kind": kind, **data})

    def recent_events(self, after: float = 0.0) -> list[dict[str, Any]]:
        with self._lock:
            return [e for e in self._events if e["t"] > after]

    def _job_changed(self, job: Job) -> None:
        self.emit("job", {"job": job.id, "project_id": job.project_id, "state": job.state, "progress": round(job.progress, 3),
                          "step": job.step})
        if job.state in ("failed", "cancelled", "lost") and job.project_id:
            try:
                built = bool(self.projects.get(job.project_id).get("build"))
                status = "failed" if job.state == "failed" else ("ready" if built else "draft")
                self.projects.update(job.project_id, status=status, error=job.error, job=None)
            except GulliverError:
                pass

    def health(self) -> dict[str, Any]:
        return {"models": len(self.projects.list()), "busy": self.jobs.busy(), "demo": bool(self.config.fake)}

    # -- parameters ---------------------------------------------------------------------------------------------------------
    def make_params(self, values: dict[str, Any], preset: str = "", base: Optional[dict[str, Any]] = None) -> ModelParams:
        merged: dict[str, Any] = {}
        if base:
            merged.update(base)
        if preset:
            if preset not in PRESETS:
                raise GulliverError("invalid", f"Unknown preset {preset!r}.", "Use city, landscape or relief.")
            merged.update(PRESETS[preset])
        merged.update({k: v for k, v in (values or {}).items() if v is not None})
        if "dem" not in (values or {}) and not base:
            merged["dem"] = self.settings["dem"]
        return ModelParams(**merged)

    def area_check(self, params: ModelParams, *, fetch: bool = False) -> dict[str, Any]:
        """What a build would make, without building: scale, size, mesh, tiles for the bed, sources, warnings."""
        s = self.settings.get()
        w, h = params.model_size_mm
        frame = 2 * params.frame_mm
        tiles_needed = suggest_tiles(w + frame, h + frame, s["bed_x_mm"], s["bed_y_mm"])
        tw, th = params.tile_size_mm()
        fits = (tw <= s["bed_x_mm"] and th <= s["bed_y_mm"]) or (th <= s["bed_x_mm"] and tw <= s["bed_y_mm"])
        bbox = params.area.bbox
        if params.dem in ("auto", "ign") and dem_mod.ign_covers(bbox) and not getattr(self.sources, "fake", False):
            res, cov = dem_mod.ign_coverage(bbox, params.cell_ground_m)
            dem_text = f"IGN MDT {res:g} m"
        elif getattr(self.sources, "fake", False):
            dem_text = "demo"
        else:
            z = dem_mod.terrarium_zoom(bbox, params.cell_ground_m)
            dem_text = f"Terrarium z{z} (~{dem_mod.terrarium_res(params.lat, z):.0f} m)"
        cells = params.estimated_cells()
        warnings = []
        if cells > MAX_CELLS:
            warnings.append({"code": "too_many_cells", "es": "La malla es demasiado fina para este tamaño: sube la resolución.",
                             "en": "The mesh is too fine for this size: raise the resolution."})
        if params.buildings and params.scale_denominator > 25000:
            warnings.append({"code": "tiny_buildings", "es": "A esta escala los edificios salen diminutos; mejor el preajuste Paisaje.",
                             "en": "At this scale buildings come out tiny; the Landscape preset suits better."})
        if params.resolution_mm < s["nozzle_mm"] * 0.5:
            warnings.append({"code": "fine_resolution", "es": "Celdas mucho más finas que la boquilla: más tiempo y archivos grandes sin ganar detalle.",
                             "en": "Cells much finer than the nozzle: more time and bigger files for no detail."})
        if not fits:
            warnings.append({"code": "bed", "es": f"No cabe en la cama: usa al menos {tiles_needed[0]}×{tiles_needed[1]} piezas.",
                             "en": f"It does not fit the bed: use at least {tiles_needed[0]}×{tiles_needed[1]} tiles."})
        if params.area.km2 > 400 and params.buildings:
            warnings.append({"code": "big_area", "es": "Zona muy grande para descargar edificios; puede tardar o fallar.",
                             "en": "Very large area to download buildings; it may take long or fail."})
        out = {
            "scale": nice_scale(params.scale_denominator),
            "scale_denominator": round(params.scale_denominator, 1),
            "model_mm": [round(w, 1), round(h, 1)],
            "ground_m": [round(params.width_m), round(params.height_m)],
            "area_km2": round(params.area.km2, 3),
            "bbox": [round(v, 6) for v in bbox],
            "cells": cells,
            "triangles_estimate": int(cells * 2.1),
            "cell_ground_m": round(params.cell_ground_m, 2),
            "tiles": [params.tiles_x, params.tiles_y],
            "tile_mm": [round(tw, 1), round(th, 1)],
            "fits_bed": fits,
            "suggested_tiles": list(tiles_needed),
            "bed_mm": [s["bed_x_mm"], s["bed_y_mm"]],
            "dem": dem_text,
            "roads": RANK_NAMES[params.effective_road_rank()],
            "road_rank": params.effective_road_rank(),
            "warnings": warnings,
            "coord": format_coord(params.lon, params.lat),
        }
        if fetch:
            pad = 3 * params.cell_ground_m + 30.0
            raw = self.sources.osm(params.area.bbox_padded(pad), rounds=1)
            feats = osm_mod.parse(raw, params.area.projector, params.width_m, params.height_m, pad_m=pad)
            out["features"] = feats.summary()
        return out

    # -- projects ----------------------------------------------------------------------------------------------------------
    def describe(self, rec: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
        out = dict(rec)
        job = self.jobs.active_for(rec["id"])
        if job:
            out["job"] = job.to_dict(log=False)
            out["status"] = "building" if job.state == "running" else "queued"
        out["ref"] = model_ref(rec["id"])
        if rec.get("build"):
            out["thumb_url"] = f"/api/models/{rec['id']}/files/thumb.png?b={rec['build']['id']}"
        if full:
            out["report"] = self.projects.report(rec["id"])
            if out["report"]:
                folder = self.projects.current_build_dir(rec["id"])
                out["folder"] = str(folder)
        return out

    def create(self, name: str, values: dict[str, Any], *, preset: str = "", place: str = "", build_now: bool = True) -> dict[str, Any]:
        params = self.make_params(values, preset or "")
        if not name.strip():
            name = place.split(",")[0].strip() if place else f"Maqueta {format_coord(params.lon, params.lat)}"
        rec = self.projects.create(name, params, place=place, preset=preset)
        family.emit("gulliver.model.created", {"model_id": rec["id"], "ref": model_ref(rec["id"]), "name": rec["name"]})
        if build_now:
            self.start_build(rec["id"])
        return self.describe(self.projects.get(rec["id"]))

    def update(self, pid: str, *, name: Optional[str] = None, values: Optional[dict[str, Any]] = None, preset: str = "",
               place: Optional[str] = None, build_now: bool = False) -> dict[str, Any]:
        rec = self.projects.get(pid)
        changes: dict[str, Any] = {}
        if name is not None and name.strip():
            changes["name"] = name.strip()[:120]
        if place is not None:
            changes["place"] = place
        if values or preset:
            changes["params"] = self.make_params(values or {}, preset, base=rec["params"]).model_dump()
            if preset:
                changes["preset"] = preset
        if changes:
            rec = self.projects.update(pid, **changes)
        if build_now:
            self.start_build(pid)
        return self.describe(self.projects.get(pid))

    def start_build(self, pid: str) -> dict[str, Any]:
        rec = self.projects.get(pid)
        active = self.jobs.active_for(pid)
        if active:
            return active.to_dict(log=False)
        params = ModelParams(**rec["params"])
        if params.estimated_cells() > MAX_CELLS:
            need = params.resolution_mm * math.sqrt(params.estimated_cells() / MAX_CELLS) * 1.02
            raise GulliverError("too_large", "The terrain mesh would be too fine for this size.",
                                f"Raise resolution_mm to at least {need:.2f} or make the model smaller.")
        self.projects.update(pid, status="queued", error="")

        def work(job: Job) -> dict[str, Any]:
            self.projects.update(pid, status="building", job=job.id)
            current = self.projects.get(pid)
            p = ModelParams(**current["params"])
            settings = self.settings.get()
            build_id = new_id()
            folder = self.projects.build_dir(pid, build_id)
            lang = 0 if settings["language"] == "es" else 1
            if self.config.isolate:
                report = run_isolated(job, {"params": p.model_dump(), "name": current["name"], "folder": str(folder),
                                            "bed": [settings["bed_x_mm"], settings["bed_y_mm"]], "lang": lang,
                                            "cache_dir": str(self.config.cache_dir), "servers": settings["overpass_servers"],
                                            "fake": self.config.fake})
            else:
                result = build(p, self.sources, progress=job.report, cancelled=job.cancelled, name=current["name"])
                report = write_outputs(result, folder, name=current["name"], bed=(settings["bed_x_mm"], settings["bed_y_mm"]),
                                       progress=job.report, lang=lang)
            report["build_id"] = build_id
            self.projects.finish_build(pid, build_id, report, settings["keep_builds"])
            self.projects.update(pid, job=None)
            self._announce(pid, current["name"], folder, report, settings)
            job.report(1.0, f"Ready: {report['scale']}, {report['size_mm'][0]:.0f} × {report['size_mm'][1]:.0f} mm")
            return {"build_id": build_id, "scale": report["scale"], "size_mm": report["size_mm"]}

        job = self.jobs.submit("build", pid, rec["name"], work)
        return job.to_dict(log=False)

    def _announce(self, pid: str, name: str, folder: Path, report: dict[str, Any], settings: dict[str, Any]) -> None:
        if self.config.fake:
            return
        ref = model_ref(pid)
        path = str(folder / report["files"]["3mf"])
        family.emit("gulliver.model.ready", {"model_id": pid, "ref": ref, "name": name, "path": path, "scale": report["scale"],
                                             "tiles": len(report.get("tiles") or [])})
        if settings.get("vulcan_auto"):
            family.emit("gulliver.export.done", {"model_id": pid, "ref": ref, "name": name, "path": path, "format": "3mf"})

    def delete(self, pid: str, confirm: bool) -> dict[str, Any]:
        if not confirm:
            raise GulliverError("confirm_required", "Deleting a model removes its files.", "Pass confirm=true.")
        active = self.jobs.active_for(pid)
        if active:
            self.jobs.cancel(active.id)
        return self.projects.delete(pid)

    def files(self, pid: str) -> dict[str, Any]:
        rec = self.projects.get(pid)
        report = self.projects.report(pid)
        if not report:
            raise GulliverError("not_found", "This model has not been built yet.", "Build it with model_build.")
        folder = self.projects.current_build_dir(pid)
        base = f"/api/models/{pid}/files/"
        files = []
        for kind, name in report["files"].items():
            files.append({"kind": kind, "name": name, "path": str(folder / name), "url": base + name, "bytes": report["bytes"].get(kind)})
        for t in report.get("tiles") or []:
            for kind in ("stl", "3mf"):
                files.append({"kind": f"tile-{kind}", "tile": t["id"], "name": t[kind], "path": str(folder / t[kind]), "url": base + t[kind]})
        return {"model_id": pid, "name": rec["name"], "folder": str(folder), "files": files}

    def export_copy(self, pid: str, folder: str = "", what: str = "all") -> dict[str, Any]:
        info = self.files(pid)
        target = Path(folder or self.settings["export_dir"] or (Path.home() / "Desktop" / "Gulliver")).expanduser()
        if not target.is_absolute():
            raise GulliverError("invalid", "The folder must be an absolute path.")
        safe_name = "".join(c if c.isalnum() or c in " -_.,()" else "_" for c in info["name"]).strip() or pid
        target = target / safe_name
        target.mkdir(parents=True, exist_ok=True)
        kinds = {"all": None, "model": {"stl", "3mf"}, "tiles": {"tiles_zip", "tile-stl", "tile-3mf"}, "stl": {"stl", "tile-stl"},
                 "3mf": {"3mf", "tile-3mf"}}
        if what not in kinds:
            raise GulliverError("invalid", "what must be all, model, tiles, stl or 3mf.")
        copied = []
        for f in info["files"]:
            if f["kind"] in ("glb", "thumb"):
                continue
            if kinds[what] is not None and f["kind"] not in kinds[what]:
                continue
            dest = target / Path(f["name"]).name
            shutil.copy2(f["path"], dest)
            copied.append(str(dest))
        return {"folder": str(target), "copied": copied}

    def send_to_vulcan(self, pid: str, tiles: bool = False) -> dict[str, Any]:
        info = self.files(pid)
        wanted = [f for f in info["files"] if f["kind"] == "3mf"] + ([f for f in info["files"] if f["kind"] == "tile-3mf"] if tiles else [])
        results = []
        for f in wanted:
            answer = family.call("vulcan", "model_import_file", {"path": f["path"], "source_ref": model_ref(pid)}, timeout=120)
            results.append({"file": f["name"], "ok": bool(answer.get("ok")), "error": answer.get("error", ""),
                            "vulcan_id": (answer.get("result") or {}).get("id") if isinstance(answer.get("result"), dict) else None})
        ok = all(r["ok"] for r in results) if results else False
        if not ok and results and all("not reachable" in (r["error"] or "") for r in results):
            raise GulliverError("unavailable", "The family hub is not running, so Vulcan's Hoard cannot be reached.",
                                "Start Hoard Hub and try again.")
        return {"ok": ok, "sent": results}

    # -- misc --------------------------------------------------------------------------------------------------------------
    def search(self, query: str, limit: int = 6) -> dict[str, Any]:
        return {"results": self.sources.search(query, limit, self.settings["language"])}

    def presets(self) -> dict[str, Any]:
        return {"presets": PRESETS, "defaults": ModelParams(lon=0, lat=0).model_dump(exclude={"lon", "lat"}),
                "parts": {n: {"color": PART_COLORS[n], "label_es": PART_LABELS[n][0], "label_en": PART_LABELS[n][1]} for n in PART_COLORS}}

    def update_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        out = self.settings.update(changes)
        if "overpass_servers" in changes and hasattr(self.sources, "overpass_servers"):
            self.sources.overpass_servers = list(out["overpass_servers"])
        return out

    def cache_info(self) -> dict[str, Any]:
        return self.fetcher.cache_size()

    def cache_clear(self, bucket: str = "", confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            raise GulliverError("confirm_required", "Clearing the cache means downloading elevation and maps again.", "Pass confirm=true.")
        if bucket and bucket not in ("terrarium", "ign", "overpass", "nominatim"):
            raise GulliverError("invalid", "bucket must be terrarium, ign, overpass, nominatim or empty for all.")
        return self.fetcher.clear(bucket)
