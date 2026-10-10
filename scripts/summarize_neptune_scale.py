"""Archive sanitized local retrieval evidence; no photo/provider payloads."""
import hashlib
import time
import xml.etree.ElementTree as ET

import httpx
from pymongo import MongoClient

from evaluate_bottom_verification import read, sha, write_new
from evaluate_neptune_scale import ROOT, VERSION, retrieval_gate
from app.museum.config import MuseumSettings
from app.museum.eval_dashboard import EvalDisplay, ratio


def main():
    folder = ROOT/'eval/private'/VERSION
    plan, report = read(folder/'plan.json'), read(folder/'results.json')
    frozen = read(ROOT/'eval/private/visual-aggregation-v1/capture.json')
    assert report['complete'] and report['plan_hash'] == sha(folder/'plan.json')
    assert plan['frozen_capture_hash'] == sha(ROOT/'eval/private/visual-aggregation-v1/capture.json')
    assert [r['id'] for r in report['rows']] == [r['id'] for r in frozen['rows']]
    assert len(report['rows']) == 42 and retrieval_gate(report['rows']) == report['gate']
    suite = ET.parse(folder/'tests.xml').getroot().find('testsuite')
    assert suite.get('failures') == suite.get('errors') == '0'
    rows = [{k: r[k] for k in ['id', 'expected', 'group', 'in_corpus', 'exact_reference_overlap',
                             'baseline_rank', 'candidate_rank', 'neptune_before_rank', 'neptune_after_rank']}
            for r in report['rows']]
    ood = [r for r in report['rows'] if not r['in_corpus']]
    ood_unchanged = all(r['baseline_top20'][:3] == r['candidate_top20'][:3] for r in ood)
    metrics = []
    for arm, label in [('baseline', '原图库'), ('candidate', '增加一张背面参考')]:
        for group, values in report[arm].items():
            cohort = '旧10张库内图（含2张参考重合）' if group == 'legacy12' else '20张局部与角度开发图'
            for top in [1, 3, 5]:
                metrics.append(ratio(cohort, label, f'Top{top}召回', values[f'top{top}'], values['in_corpus']))
    summary = {k: report[k] for k in ['corpus_count', 'work_count', 'before_reference_count',
        'after_reference_count', 'baseline', 'candidate', 'gate', 'api_calls', 'identity_tested', 'live_enabled']}
    summary.update(unique_development_photos=42, ood_photos=len(ood),
        ood_top3_unchanged=ood_unchanged, decision='retrieval_pass_identity_pending',
        scope=report['scope'], relevant_tests=int(suite.get('tests')))
    old_plan = read(ROOT/'eval/private/surface-controls-v1/plan.json')
    public_raw = ROOT/old_plan['index_config']['museum_corpus']
    private_raw = ROOT/old_plan['index_config']['museum_private_corpus']
    corpus_hash = hashlib.sha256(public_raw.read_bytes()+b'\n'+private_raw.read_bytes()).hexdigest()
    payload = dict(_id=VERSION, created_at=time.time(), kind='photo', status='completed',
        dataset_version=VERSION, dataset_hash=sha(folder/'plan.json'), corpus_hash=corpus_hash,
        model='facebook/dinov2-small', prompt_version='no-model-api-retrieval-only',
        visual_index_hash=report['candidate_index_hash'], human_reviewed=False, online_ab=False,
        summary=summary, results=rows, display=dict(track='photo', decision='pending', metrics=metrics,
            scope='42张已知开发图，仅增加一张背面参考。海神背面6→1，其他29张库内图目标名次不退化；12张库外Top3不变不代表正确拒识。旧图含2张参考重合。未运行身份核对，未启用网站。'))
    cfg = MuseumSettings()
    with httpx.Client(base_url='http://127.0.0.1:3000', trust_env=False, timeout=20,
                      headers={'Authorization': 'Bearer '+cfg.museum_admin_token}) as client:
        with MongoClient(cfg.mongodb_uri) as mongo:
            collection = mongo[cfg.mongodb_db].eval_runs
            if collection.find_one({'_id': VERSION}) is None:
                response = client.post('/api/museum/admin/eval-runs', json=payload)
                response.raise_for_status()
            saved = collection.find_one({'_id': VERSION})
            assert saved['results'] == rows and saved['summary'] == summary
        response = client.get('/api/museum/admin/evaluations'); response.raise_for_status()
        shown = next(r for r in response.json()['runs'] if r['_id'] == VERSION)
        assert shown['decision'] == 'pending'
        assert shown['metrics'] == EvalDisplay.model_validate(payload['display']).model_dump()['metrics']
        response = client.get('/api/museum/health'); response.raise_for_status()
        assert response.json()['photo_verification'] == 'legacy'
        assert str(cfg.museum_visual_manifest) == old_plan['visual_manifest']
    archive = dict(run_id=VERSION, mongo_readback=True, dashboard_readback=True,
                   default_policy='legacy', default_manifest_unchanged=True)
    public = dict(date='2026-10-10', **summary, rows=rows, archive=archive,
        baseline_index_hash=report['baseline_index_hash'], candidate_index_hash=report['candidate_index_hash'],
        corpus_hash=corpus_hash, evidence_hashes={n: sha(folder/n) for n in ['plan.json', 'results.json', 'tests.xml']})
    for path, value in [(folder/'archive.json', archive), (ROOT/'docs/neptune-scale-results.json', public)]:
        if path.exists(): assert read(path) == value
        else: write_new(path, value)
    print({'archived': VERSION, 'retrieval_gate': report['gate'], 'ood_top3_unchanged': ood_unchanged})


if __name__ == '__main__':
    main()
