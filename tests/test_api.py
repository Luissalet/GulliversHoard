import re
import time
from pathlib import Path

import pytest

from gulliver_hoard.agent_tools import TOOLS, tool_catalog

from conftest import DEMO

SMALL = {**DEMO, "size_mm": 100, "resolution_mm": 0.6}


def create(call, **kw):
    args = {"name": "Lilliput", "place": "Lilliput (demo)", "preset": "city", "params": {**SMALL, **kw}}
    return call("model_create", args)


def test_health_and_catalogue(client):
    h = client.get("/api/health").json()
    assert h["service"] == "gulliver-hoard" and h["demo"] is True
    names = {t["name"] for t in tool_catalog()}
    assert {"place_search", "area_check", "model_create", "model_files", "model_send_to_vulcan"} <= names
    for t in tool_catalog():
        assert len(t["description"].split("\n")[0]) <= 110


def test_every_tool_is_tested():
    text = "".join(p.read_text(encoding="utf-8") for p in Path(__file__).parent.glob("test_*.py"))
    missing = [t.name for t in TOOLS if f'"{t.name}"' not in text]
    assert not missing, missing


def test_search_presets_and_check(call):
    hits = call("place_search", {"query": "lilliput"})["results"]
    assert hits and hits[0]["bbox"]
    p = call("presets_get")
    assert {"city", "landscape", "relief"} <= set(p["presets"]) and p["parts"]["water"]["color"].startswith("#")
    c = call("area_check", {"params": {**DEMO}, "preset": "city", "fetch_features": True})
    assert c["scale"] == "1:13.000" and c["fits_bed"] and c["features"]["buildings"] > 100 and c["road_rank"] == 4
    big = call("area_check", {"params": {**DEMO, "size_mm": 600}})
    assert not big["fits_bed"] and big["suggested_tiles"] == [3, 2] and any(w["code"] == "bed" for w in big["warnings"])
    bad = call("area_check", {"params": {"lat": 40}}, status=400)
    assert "lon" in bad["error"]


def test_create_build_files_and_download(call, client):
    m = create(call, tiles_x=2, frame_mm=3)
    assert m["status"] == "ready" and m["ref"] == f"hoard://gulliver/model/{m['id']}"
    full = call("model_get", {"model_id": m["id"]})
    rep = full["report"]
    assert rep["check"]["watertight"] and len(rep["tiles"]) == 2 and all(t["watertight"] for t in rep["tiles"])
    assert set(rep["parts"]) >= {"terrain", "buildings", "water", "frame"}
    files = call("model_files", {"model_id": m["id"]})["files"]
    kinds = {f["kind"] for f in files}
    assert {"stl", "3mf", "tiles_zip", "glb", "thumb", "tile-stl", "tile-3mf"} <= kinds
    stl = next(f for f in files if f["kind"] == "stl")
    r = client.get(stl["url"] + "?download=1")
    assert r.status_code == 200 and r.headers["content-type"] == "model/stl" and "attachment" in r.headers["content-disposition"]
    assert client.get(f"/api/models/{m['id']}/files/..%2F..%2Fproject.json").status_code in (400, 404)
    assert client.get("/api/models").json()["models"][0]["id"] == m["id"]
    lst = call("model_list")["models"]
    assert lst[0]["report"]["scale"] and lst[0]["thumb_url"]


def test_update_rebuild_duplicate_and_jobs(call):
    m = create(call)
    first = call("model_get", {"model_id": m["id"]})["build"]["id"]
    u = call("model_update", {"model_id": m["id"], "name": "Otra", "params": {"shape": "circle"}, "build": True})
    assert u["name"] == "Otra" and u["params"]["height_m"] == u["params"]["width_m"]
    again = call("model_get", {"model_id": m["id"]})
    assert again["build"]["id"] != first and again["report"]["size_mm"][0] == pytest.approx(100, abs=0.5)
    jobs = call("jobs_list", {"model_id": m["id"]})["jobs"]
    assert len(jobs) == 2 and all(j["state"] == "done" for j in jobs)
    job = call("job_get", {"job_id": jobs[0]["id"]})
    assert job["log"] and job["progress"] == 1.0
    b = call("model_build", {"model_id": m["id"]})
    assert b["state"] == "done"
    d = call("model_duplicate", {"model_id": m["id"], "name": "Copia"})
    assert d["name"] == "Copia" and d["status"] == "draft"
    nb = call("job_cancel", {"job_id": "zzzzzzzz"}, status=404)
    assert nb["code"] == "not_found"


def test_changing_a_model_while_it_builds_queues_a_rebuild(services):
    from gulliver_hoard.jobs import Job
    m = services.create("Lilliput", {**SMALL}, preset="city", place="Lilliput (demo)", build_now=False)
    running = Job("build", m["id"], "Lilliput", lambda j: None)
    running.state, running.inputs = "running", services.projects.get(m["id"])["params"]
    services.jobs._jobs[running.id] = running
    services.jobs._order.append(running.id)
    assert services.start_build(m["id"])["id"] == running.id
    u = services.update(m["id"], values={"palette": "white"}, build_now=True)
    assert u["params"]["palette"] == "white"
    rebuilt = [j for j in services.jobs.list(m["id"]) if j["id"] != running.id]
    assert len(rebuilt) == 1 and rebuilt[0]["state"] == "done"
    from gulliver_hoard.mesh.build import palette_colors
    assert services.projects.report(m["id"])["parts"]["terrain"]["color"] == palette_colors("white")["terrain"]
    running.state = "done"


def test_cancel_a_queued_job(services):
    from gulliver_hoard.jobs import Jobs
    jobs = Jobs(services.config.data_dir / "j.json", background=True)
    started = []
    job = jobs.submit("build", "p1", "x", lambda j: started.append(1))
    jobs.cancel(job.id)
    assert jobs.get(job.id)["state"] == "cancelled" and not started


def test_export_vulcan_delete(call, tmp_path):
    m = create(call, tiles_x=2)
    out = call("model_export", {"model_id": m["id"], "folder": str(tmp_path / "out"), "what": "tiles"})
    names = sorted(Path(p).name for p in out["copied"])
    assert any(n.endswith("-tiles.zip") for n in names) and any(n.endswith("-A1.stl") for n in names)
    assert not any(n == "lilliput.stl" for n in names)
    v = call("model_send_to_vulcan", {"model_id": m["id"]}, status=503)
    assert v["code"] == "unavailable"
    assert call("model_delete", {"model_id": m["id"]}, status=400)["code"] == "confirm_required"
    assert call("model_delete", {"model_id": m["id"], "confirm": True})["deleted"] == m["id"]
    assert call("model_get", {"model_id": m["id"]}, status=404)["code"] == "not_found"


def test_settings_and_cache(call):
    s = call("settings_get")
    assert s["bed_x_mm"] == 256 and s["dem"] == "auto"
    s = call("settings_set", {"bed_x_mm": 180, "bed_y_mm": 180, "language": "en"})
    assert s["bed_x_mm"] == 180 and s["language"] == "en"
    assert call("settings_set", {"bed_x_mm": 5}, status=400)["code"] in ("invalid", "invalid_arguments")
    big = call("area_check", {"params": {**DEMO, "size_mm": 200}})
    assert not big["fits_bed"] and big["bed_mm"] == [180, 180]
    info = call("cache_info")
    assert "bytes" in info
    assert call("cache_clear", {}, status=400)["code"] == "confirm_required"
    assert "removed" in call("cache_clear", {"confirm": True, "bucket": "overpass"})


def test_failed_build_is_reported(call, services):
    class Broken:
        fake = True
        def dem(self, *a, **k):
            from gulliver_hoard.errors import GulliverError
            raise GulliverError("source_failed", "elevation server down", "try later")
        def osm(self, *a, **k):
            return {"elements": []}
        def search(self, *a, **k):
            return []
    services.sources = Broken()
    m = create(call)
    assert m["status"] == "failed" and "elevation" in m["error"]


def test_agent_route_needs_the_token(client, services):
    assert client.post("/api/agent/call", json={"name": "model_list", "arguments": {}}).status_code in (401, 403)
    r = client.post("/api/agent/call", json={"name": "model_list", "arguments": {}}, headers={"Authorization": f"Bearer {services.token}"})
    assert r.status_code == 200


def test_a_build_in_its_own_process_reports_progress_and_finishes(tmp_path):
    """The app builds in a child process so the web server stays responsive; the same path with the demo town."""
    from gulliver_hoard.config import Config
    from gulliver_hoard.services import Services

    cfg = Config(data_dir=tmp_path / "iso", fake=True, background=False, isolate=True)
    s = Services(cfg)
    try:
        m = s.create("Aislada", {"lon": -3.70, "lat": 40.42, "width_m": 1200, "height_m": 900, "size_mm": 60,
                                 "resolution_mm": 0.6})
        rec = s.projects.get(m["id"])
        assert rec["status"] == "ready", rec
        report = s.projects.report(m["id"])
        assert report["check"]["watertight"] and report["files"]["3mf"]
        job = s.jobs.get(s.jobs.list()[0]["id"])
        assert job["state"] == "done" and any("Terrain solid" in line for line in job["log"])
    finally:
        s.stop()


def test_secret_token_is_never_shown_and_reaches_the_sources(call, services):
    assert call("settings_get")["dataforsyningen_token"] == ""
    s = call("settings_set", {"dataforsyningen_token": "abc123"})
    assert s["dataforsyningen_token"] == "••••••" and "abc123" not in str(s)
    assert "abc123" not in str(call("settings_get"))
    assert services.source_keys() == {"dataforsyningen": "abc123"}
    call("settings_set", {"dataforsyningen_token": "••••••", "nozzle_mm": 0.6})   # the mask means "unchanged"
    assert services.source_keys()["dataforsyningen"] == "abc123"
    call("settings_set", {"dataforsyningen_token": ""})
    assert services.source_keys()["dataforsyningen"] == ""


def test_bed_check_counts_the_frame_the_build_will_draw(call):
    # a plaque widens the frame to 14 mm: 240 mm of map + 2 x 14 no longer fits a 256 mm bed in one piece
    c = call("area_check", {"params": {**DEMO, "size_mm": 240, "plaque": True}})
    assert c["frame_mm"] == 14 and c["tile_mm"][0] == 268 and not c["fits_bed"] and c["suggested_tiles"] == [2, 1]
    # framed panels get 8 mm each even with no frame asked, and the advice is in panels
    p = call("area_check", {"params": {**DEMO, "size_mm": 500, "panels_x": 2, "panels_y": 1}})
    assert p["frame_mm"] == 8 and p["tile_mm"][0] == 266 and not p["fits_bed"]
    assert p["suggested_tiles"] is None and p["suggested_panels"][0] * p["suggested_panels"][1] >= 3
    assert "cuadros" in next(w["es"] for w in p["warnings"] if w["code"] == "bed")


def test_builds_are_kept_compared_restored_and_fixed(call, client):
    m = create(call, water_depth_mm=0.2)                             # water too shallow to see: printability has a fix
    first = call("model_get", {"model_id": m["id"]})["build"]["id"]
    adv = call("model_advice", {"model_id": m["id"]})
    assert not adv["applied"] and any(f["param"] == "water_depth_mm" and f["to"] >= 0.4 for f in adv["fixes"])
    assert adv["preview"]["before"]["model_mm"] == adv["preview"]["after"]["model_mm"]
    assert call("model_advice", {"model_id": m["id"], "only": ["nope"]}, status=400)["code"] == "invalid"
    done = call("model_advice", {"model_id": m["id"], "only": ["water_depth_mm"], "apply": True})
    assert done["applied"] and done["job"]
    rec = call("model_get", {"model_id": m["id"]})
    assert rec["status"] == "ready" and rec["params"]["water_depth_mm"] >= 0.4 and rec["build"]["id"] != first
    builds = call("model_builds", {"model_id": m["id"]})["builds"]
    assert [b["current"] for b in builds] == [True, False] and builds[1]["id"] == first
    assert builds[0]["params"]["water_depth_mm"] >= 0.4 and builds[1]["params"]["water_depth_mm"] == 0.2
    cmp = call("model_compare", {"model_id": m["id"]})
    assert cmp["a"]["id"] == first and cmp["b"]["current"]
    assert [c["param"] for c in cmp["params_changed"]] == ["water_depth_mm"]
    vol = next(r for r in cmp["metrics"] if r["key"] == "volume_cm3")
    assert vol["a"] and vol["b"] and vol["delta"] is not None and cmp["printability_better"] in ("a", "b", "same")
    # the files of the earlier build are still served, and can be exported
    old = call("model_files", {"model_id": m["id"], "build_id": first})
    assert not old["current"] and all("build=" + first in f["url"] for f in old["files"])
    assert client.get(next(f["url"] for f in old["files"] if f["kind"] == "stl")).status_code == 200
    assert client.get(f"/api/models/{m['id']}/files/model.stl?build=zzzzzz9").status_code == 404
    # back to the first build: it becomes current with its parameters
    back = call("model_build_use", {"model_id": m["id"], "build_id": first})
    assert back["build"]["id"] == first and back["params"]["water_depth_mm"] == 0.2
    assert call("model_build_use", {"model_id": m["id"], "build_id": "zzzzzz9"}, status=404)["code"] == "not_found"
