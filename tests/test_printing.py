from types import SimpleNamespace

import numpy as np
import pytest
from shapely.geometry import box

from gulliver_hoard.printing import PrintProfile, estimate
from gulliver_hoard.printability import locations
from conftest import DEMO


def test_material_units_per_part_prices_and_unpriced_total():
    report = {"volume_cm3":100.,"surface_mm2":10000.,"parts":{"terrain":{"volume_cm3":80.},"water":{"volume_cm3":20.}}}
    profile = PrintProfile(infill_pct=0, shell_mm=1,
        filament={"density_g_cm3":1.,"price_per_kg":10.},
        filaments={"water":{"density_g_cm3":2.,"price_per_kg":20.,"color":"#2244ff"}})
    data = estimate(report, profile)
    assert data["estimated_g"] == 12 and data["estimated_cost"] == .16
    assert [p["estimated_g"] for p in data["parts"]] == [8,4]
    assert data["sliced"] is False and not data["includes_purge_supports_energy_time"]
    profile.filaments["water"].price_per_kg = None
    unknown = estimate(report, profile)
    assert unknown["estimated_cost"] is None and unknown["unpriced_parts"] == ["water"]
    assert unknown["priced_parts_cost"] == .08


def test_profile_snapshot_bed_and_nozzle_and_reversible_archive(call, services):
    profile = call("print_profile_save", {"profile":{"name":"Small printer","bed_x_mm":180,"bed_y_mm":180,"nozzle_mm":.6}})
    original = call("model_create", {"name":"Original","params":{**DEMO,"size_mm":240},"build":False})
    variant = call("model_profile_apply", {"model_id":original["id"],"profile_id":profile["id"],"build":False})
    assert variant["id"] != original["id"] and variant["params"]["nozzle_mm"] == .6
    assert not call("area_check", {"params":variant["params"]})["fits_bed"]
    assert call("area_check", {"params":original["params"]})["fits_bed"]
    call("print_profile_save", {"profile":{**profile,"bed_x_mm":300,"bed_y_mm":300}})
    unchanged = services.projects.get(variant["id"])
    assert unchanged["params"]["print_profile"]["bed_x_mm"] == 180
    assert services.projects.get(original["id"])["params"]["print_profile"] is None
    call("print_profile_archive", {"profile_id":profile["id"]})
    assert not call("print_profiles")["profiles"]
    call("print_profile_archive", {"profile_id":profile["id"],"archived":False})
    assert call("print_profiles")["profiles"][0]["id"] == profile["id"]


def test_issue_locations_are_on_the_actual_features_in_preview_coordinates():
    result = SimpleNamespace(footprints={"buildings":[box(10,0,10.2,4)],"trees":(np.array([[20.,3.]]),np.array([.2]))}, Z=None)
    points = locations(result, .8)
    assert {p["layer"] for p in points} == {"buildings","trees"}
    building = next(p for p in points if p["layer"] == "buildings")
    x,y,z = building["position_mm"]
    assert 10 < x < 10.2 and 0 < y < 4 and z == .2
    assert building["width_mm"] == pytest.approx(.2)


def test_finished_build_exposes_materials_and_locations_through_normal_tools(call, client):
    profile = call("print_profile_save", {"profile":{"name":"PLA quote","filament":{"price_per_kg":20,"color":"#ffaa33"}}})
    m = call("model_create", {"name":"Printing QA","params":{**DEMO,"size_mm":60,"resolution_mm":1.5,"trees":False,
                                                                 "print_profile":profile}})
    report = call("model_get", {"model_id":m["id"]})["report"]
    assert report["check"]["watertight"] and report["material_estimate"]["estimated_cost"] > 0
    assert report["bed_mm"] == [256,256]
    assert all(part["color"] == "#ffaa33" for part in report["parts"].values())
    quote = call("model_materials", {"model_id":m["id"]})
    assert quote["estimated_g"] == report["material_estimate"]["estimated_g"]
    markers = call("model_issues", {"model_id":m["id"]})
    assert isinstance(markers["locations"], list)
    issues = call("model_issues", {"model_id":m["id"],"layer":"buildings"})["locations"]
    assert all(item["layer"] == "buildings" for item in issues)
