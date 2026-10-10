"""Local-only, one-reference ablation on the frozen 1,012-record photo set.

No provider client, source downloads or live config changes. This measures
retrieval, never identity. All images/vectors and full rankings stay private.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path

import numpy as np

from evaluate_visual_aggregation import ROOT, sha, write_new, rank, legacy_scores, summary

VERSION = 'neptune-scale-reference-v1'
TARGET = 'va-neptune-triton'
REFERENCE = 'va-neptune-archival-back-v1'


def check_addition(before, after):
    old = {r['id']: r for r in before['references']}
    new = {r['id']: r for r in after['references']}
    if len(old) != len(before['references']) or len(new) != len(after['references']):
        raise ValueError('Duplicate reference ID')
    if set(new) - set(old) != {REFERENCE} or any(new.get(k) != v for k, v in old.items()):
        raise ValueError('Only one additional reference may change')
    if new[REFERENCE]['source_id'] != TARGET:
        raise ValueError('Wrong reference attribution')


def retrieval_gate(rows):
    regressions = [r['id'] for r in rows if r['in_corpus'] and r['baseline_rank'] is not None
                   and (r['candidate_rank'] is None or r['candidate_rank'] > r['baseline_rank'])]
    targets = [r for r in rows if r['id'] == 'legacy-08']
    target_pass = len(targets) == 1 and targets[0]['candidate_rank'] is not None and targets[0]['candidate_rank'] <= 3
    return dict(passed=bool(rows) and target_pass and not regressions,
                target_top3=target_pass, rank_regressions=regressions,
                identity_tested=False, deployment_approved=False)


def capture(folder):
    import torch
    from app.museum.visual_index import MuseumVisualIndex, MODEL_REVISION, image_views
    from app.museum.vision import prepare_image

    torch.set_num_threads(4)
    frozen_dir = ROOT/'eval/private/visual-aggregation-v1'
    frozen = json.loads((frozen_dir/'capture.json').read_bytes())
    plan = json.loads((ROOT/'eval/private/surface-controls-v1/plan.json').read_bytes())
    gallery = ROOT/plan['visual_manifest']
    corpora = [Path(plan['index_config'][k]) for k in ['museum_corpus', 'museum_private_corpus']]
    assert sha(gallery) == frozen['gallery_sha256']
    assert sha(corpora[0]) == frozen['public_corpus_sha256'] and sha(corpora[1]) == frozen['corpus_sha256']
    assert frozen['model_revision'] == MODEL_REVISION and frozen['complete'] and len(frozen['rows']) == 42
    records = {r['_id']: r for p in corpora for r in json.loads(p.read_bytes())}
    original = json.loads(gallery.read_bytes())
    old_study = ROOT/'data/private/va-pilot-100-neptune-back-v1-references.json'
    added_ref = next(r for r in json.loads(old_study.read_bytes())['references'] if r['id'] == REFERENCE)
    added_path = gallery.parent/added_ref['path']
    assert old_study.parent == gallery.parent and sha(added_path) == added_ref['sha256']
    provenance_path = added_path.parent/'selected-reference.json'
    provenance = json.loads(provenance_path.read_bytes())
    assert provenance['derived_sha256'] == sha(added_path)
    assert provenance['source_sha256'] == sha(added_path.parent/'2018KT6385-2000.jpg')
    expanded = copy.deepcopy(original)
    expanded['references'].append(added_ref)
    check_addition(original, expanded)
    candidate_manifest = gallery.parent/(VERSION+'-references.json')
    folder.mkdir(parents=True, exist_ok=False)
    if candidate_manifest.exists():
        assert json.loads(candidate_manifest.read_bytes()) == expanded
    else:
        write_new(candidate_manifest, expanded)
    write_new(folder/'plan.json', dict(version=VERSION, variable='one independent rear reference only',
        base_manifest_hash=sha(gallery), candidate_manifest_hash=sha(candidate_manifest),
        frozen_capture_hash=sha(frozen_dir/'capture.json'), reference_hash=sha(added_path),
        provenance_hash=sha(provenance_path), code_hash=sha(Path(__file__)),
        query_count=42, api_calls=0, identity_tested=False, live_enabled=False))
    base = MuseumVisualIndex(gallery, Path(plan['visual_model']), records,
        cache_dir=Path(plan['visual_cache']))
    base._start()
    assert base.index_hash == frozen['index_hash']
    assert base.entries == frozen['entries']
    print('Baseline loaded:', len(base.images), 'references', flush=True)
    candidate = MuseumVisualIndex(candidate_manifest, Path(plan['visual_model']), records,
        encoder=base.encoder, cache_dir=base.cache_dir, cache_namespace=base.cache_namespace)
    candidate._start()
    assert candidate.label_required_ids == base.label_required_ids
    assert candidate.entries[:len(base.entries)] == base.entries
    assert np.array_equal(candidate.vectors[:len(base.entries)], base.vectors)
    print('Candidate loaded:', len(candidate.images), 'references; old vectors unchanged', flush=True)
    added_hash = hashlib.sha256(prepare_image(added_path.read_bytes())).hexdigest()
    rows = []
    for case in frozen['rows']:
        path = ROOT/case['path']
        assert sha(path) == case['sha256']
        clean = prepare_image(path.read_bytes())
        assert hashlib.sha256(clean).hexdigest() != added_hash, 'New reference duplicates query'
        matrix = frozen_dir/case['matrix']
        assert sha(matrix) == case['matrix_sha256']
        before = legacy_scores(np.load(matrix, allow_pickle=False), base.entries)
        assert before == case['baseline_hits']
        # Exercise the actual production scoring path, not a hand-picked target pool.
        before_live = base._search(clean, len(records))
        after = candidate._search(clean, len(records))
        assert before_live == before, f'Frozen baseline drift: {case["id"]}'
        assert [h for h in before if h['source_id'] != TARGET] == [h for h in after if h['source_id'] != TARGET]
        query = base.encoder.encode(image_views(clean))
        similarities = query @ candidate.vectors.T
        details = []
        for ref in candidate.references_by_source[TARGET]:
            columns = [i for i, entry in enumerate(candidate.entries) if entry['reference_id'] == ref['reference_id']]
            scores = similarities[:, columns]
            qi, ri = np.unravel_index(scores.argmax(), scores.shape)
            details.append(dict(reference_id=ref['reference_id'], score=round(float(scores[qi, ri]), 6),
                                query_crop=int(qi), reference_crop=int(ri)))
        row = {k: case[k] for k in ['id', 'expected', 'group', 'in_corpus', 'exact_reference_overlap', 'baseline_rank']}
        row.update(candidate_rank=rank(after, case['expected']), baseline_top20=before[:20],
            candidate_top20=after[:20], neptune_before_rank=rank(before, TARGET),
            neptune_after_rank=rank(after, TARGET), neptune_references=details)
        rows.append(row)
        print(case['id'], row['baseline_rank'], '->', row['candidate_rank'], flush=True)
    report = dict(version=VERSION, complete=True, api_calls=0, identity_tested=False, live_enabled=False,
        corpus_count=len(records), work_count=len({e['source_id'] for e in base.entries}),
        before_reference_count=len(base.images), after_reference_count=len(candidate.images),
        baseline_index_hash=base.index_hash, candidate_index_hash=candidate.index_hash,
        plan_hash=sha(folder/'plan.json'), baseline=summary(rows, 'baseline_rank'),
        candidate=summary(rows, 'candidate_rank'), gate=retrieval_gate(rows), rows=rows,
        scope='Known developer set; exact-overlap controls included; retrieval not identification')
    write_new(folder/'results.json', report)
    print(json.dumps({k: report[k] for k in ['baseline', 'candidate', 'gate']}), flush=True)


def check(folder, arm):
    report = json.loads((folder/'results.json').read_bytes())
    assert report['complete'] and report['plan_hash'] == sha(folder/'plan.json')
    assert retrieval_gate(report['rows']) == report['gate']
    row = next(r for r in report['rows'] if r['id'] == 'legacy-08')
    value = row[arm+'_rank']
    print(json.dumps(dict(case='legacy-08', arm=arm, target_rank=value, api_calls=0)))
    assert value is not None and value <= 3, 'Neptune back missing from visual Top3'
    if arm == 'candidate':
        assert report['gate']['passed'], 'Candidate caused a rank regression'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['capture', 'check'])
    parser.add_argument('--folder', type=Path, default=ROOT/'eval/private'/VERSION)
    parser.add_argument('--arm', choices=['baseline', 'candidate'], default='candidate')
    args = parser.parse_args()
    if not args.folder.resolve().is_relative_to((ROOT/'eval/private').resolve()):
        raise ValueError('Private output folder required')
    if args.mode == 'capture': capture(args.folder)
    else: check(args.folder, args.arm)
