import hashlib
import re

import pytest
from shapely.geometry import Polygon, box

from gulliver_hoard.assembly_guide import base_outline
from gulliver_hoard.mesh import solids as S
from gulliver_hoard.mesh.export import stl_bytes
from conftest import DEMO


def test_base_outline_keeps_concave_edges_and_fills_only_engraving(tmp_path):
    outline = Polygon([(0,0),(30,0),(30,10),(10,10),(10,30),(0,30)])
    solid = S.subtract(S.prism(outline,0,3), [S.prism(box(2,2,5,5),-.1,.5)])
    path = tmp_path/'concave.stl';path.write_bytes(stl_bytes(solid))
    actual = base_outline(path)
    assert actual.symmetric_difference(outline).area < .0001
    assert not actual.contains(box(15,15,20,20))


def test_assembly_generation_paging_files_export_and_snapshot(call,client,services,tmp_path):
    m=call("model_create",{"name":"Montaje <guardado>","params":{**DEMO,"size_mm":60,"resolution_mm":2,
        "tiles_x":2,"tiles_y":2,"shape":"rounded","trees":False,"tile_cuts":"streets",
        "print_profile":{"name":"Original","filament":{"name":"PLA cobre","color":"#ac782d"}}}})
    pid=m['id'];rec=services.projects.get(pid);bid=rec['build']['id'];folder=services.projects.build_folder(pid,bid)
    before={str(p.relative_to(folder)):hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.rglob('*') if p.is_file()}
    page=call("model_assembly",{"model_id":pid,"limit":1})
    assert page['build_id']==bid and page['total']==4 and page['next_offset']==1
    pieces=[]
    while True:
        pieces.extend(page['pieces'])
        if page['next_offset'] is None: break
        page=call("model_assembly",{"model_id":pid,"offset":page['next_offset'],"limit":1})
    assert [p['number'] for p in pieces]==[1,2,3,4]
    report=services.projects.report(pid,bid)
    assert [p['id'] for p in pieces]==[p['id'] for p in report['tiles']]
    assert all(m['material']=='PLA cobre' for m in page['materials'])
    saved=call("model_assembly",{"model_id":pid,"build_id":bid})
    es=call("model_assembly_export",{"model_id":pid,"build_id":bid,"lang":"es"})
    html=client.get(es['url']);assert html.status_code==200
    assert 'Guía de montaje' in html.text and '&lt;guardado&gt;' in html.text and '<guardado>' not in html.text
    assert 'data:image/png;base64,' in html.text and 'font-size:10pt' in html.text
    assert client.get(es['map_url']).headers['content-type'].startswith('image/svg+xml')
    for p in pieces:
        assert p['stl'] in html.text and p['3mf'] in html.text
        assert len(re.findall(fr'>{p["number"]} · {p["id"]}</text>',client.get(es['map_url']).text))==1
    original_html=html.content
    call("model_update",{"model_id":pid,"name":"Changed","params":{"size_mm":90,"pin_depth_mm":7,"tile_labels":False},"build":True})
    assert services.projects.get(pid)['build']['id']!=bid
    assert call("model_assembly",{"model_id":pid})['pins']['cut_length_mm']==13.5
    assert call("model_assembly",{"model_id":pid,"build_id":bid})==saved
    call("model_assembly_export",{"model_id":pid,"build_id":bid})
    assert client.get(es['url']).content==original_html
    assert before=={name:hashlib.sha256((folder/name).read_bytes()).hexdigest() for name in before}
    en=call("model_assembly_export",{"model_id":pid,"build_id":bid,"lang":"en"})
    assert 'Assembly guide' in client.get(en['url']).text and 'PLA cobre' in client.get(en['url']).text
    files=call("model_files",{"model_id":pid,"build_id":bid})['files']
    assert {'assembly-es.html','assembly-en.html','assembly-map.svg'} <= {f['name'] for f in files}
    exported=call("model_export",{"model_id":pid,"build_id":bid,"folder":str(tmp_path/'export')})
    assert list((tmp_path/'export').rglob('assembly-es.html'))


def test_single_piece_legacy_and_framed_panel_truth(call,client,services):
    m=call("model_create",{"name":"Paneles","params":{**DEMO,"size_mm":40,"resolution_mm":2,"trees":False,
        "panels_x":2,"panels_y":1}})
    data=call("model_assembly",{"model_id":m['id']});assert data['panels'] and data['total']==2
    out=call("model_assembly_export",{"model_id":m['id'],"lang":"en"})
    assert 'separate framed panels' in client.get(out['url']).text
    folder=services.projects.current_build_dir(m['id']);(folder/'params.json').unlink()
    legacy=call("model_assembly",{"model_id":m['id']});assert not legacy['snapshot_known'] and legacy['pins'] is None
    out=call("model_assembly_export",{"model_id":m['id']})
    assert 'Generación antigua sin parámetros guardados' in client.get(out['url']).text
    single=call("model_create",{"name":"Una pieza","params":{**DEMO,"size_mm":30,"resolution_mm":2,"trees":False}})
    assert call("model_assembly",{"model_id":single['id']})['total']==1
    out=call("model_assembly_export",{"model_id":single['id']})
    assert client.get(out['url']).status_code==200
    draft=call("model_create",{"name":"Borrador","params":DEMO,"build":False})
    call("model_assembly",{"model_id":draft['id']},status=404)
