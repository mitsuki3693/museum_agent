"""A sparse category must not prevent later available works filling the pilot."""
import importlib.util
import io
import json
from pathlib import Path
import sys
from PIL import Image

spec=importlib.util.spec_from_file_location('pilot_import_quota',Path(__file__).resolve().parents[2]/'scripts/import_va_pilot.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_empty_categories_redistribute_to_available_final_category(tmp_path, monkeypatch):
    monkeypatch.setattr(module,'PRIVATE',tmp_path)
    (tmp_path/'base.json').write_text(json.dumps([{'_id':'va-o0','source_url':'https://collections.vam.ac.uk/item/O0/'}]))
    (tmp_path/'refs.json').write_text(json.dumps({'references':[]}))
    def download(client,url,path):
        if 'objects/search' in url:
            return json.dumps({'records':[{'systemNumber':f'O{i}'} for i in range(1,7)] if 'q_object_type=bust' in url else []}).encode()
        if 'museumobject' in url:
            oid=url.rsplit('/',1)[-1]
            return json.dumps({'record':{'objectType':'Bust','accessionNumber':oid,'images':[oid]}}).encode()
        n=int(path.stem[1:]);output=io.BytesIO()
        Image.new('RGB',(8,8),(n*30,50,10)).save(output,format='PNG')
        return output.getvalue()
    monkeypatch.setattr(module,'download',download)
    monkeypatch.setattr(sys,'argv',['import_va_pilot.py','--target','7','--folder','regression','--base-corpus','base.json','--base-references','refs.json'])
    module.main()
    corpus=json.loads((tmp_path/'regression-corpus.json').read_bytes())
    report=json.loads((tmp_path/'regression/import-report.json').read_bytes())
    assert len(corpus)==7 and len({r['_id'] for r in corpus})==7
    assert report['actual']==report['target']==7
    assert not report['active_configuration_changed']
