"""Prepare a private collection checklist, not completed evaluation evidence.

No downloads, model calls or configuration changes. Images and labels still need
independent collection and human review before any evaluation can be frozen.
"""
from collections import defaultdict, deque, Counter
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sample_works(records, reference_ids, excluded_ids, count=40):
    groups = defaultdict(list)
    for row in records:
        if row['_id'] in reference_ids and row['_id'] not in excluded_ids and row.get('status') == 'active':
            groups[row['title']].append(row)
    queues = [deque(sorted(rows, key=lambda r: r['_id'])) for _, rows in sorted(groups.items())]
    selected = []
    while len(selected) < count and any(queues):
        for group in queues:
            if group and len(selected) < count:
                selected.append(group.popleft())
    if len(selected) < count:
        raise ValueError('Not enough independent works to prepare the requested checklist')
    return selected


def main():
    p = ROOT / 'data/private'
    corpus = p / 'va-pilot-100-v1-corpus.json'
    references = p / 'va-pilot-100-v1-references.json'
    development = p / 'MUSE-test-pack-12-20261006/manifest.json'
    out = ROOT / 'eval/private/photo-scale-v1.collection-plan.json'
    if out.exists():
        raise FileExistsError('Preserve existing collection work; edit the draft, do not overwrite it')
    records = json.loads(corpus.read_bytes())
    refs = json.loads(references.read_bytes())['references']
    old = json.loads(development.read_bytes())
    excluded = {case['expected_source_id'] for case in old if case.get('expected_source_id')}
    selected = sample_works(records, {r['source_id'] for r in refs}, excluded)
    cases = []
    # Every fourth sampled work is reserved; all images of a work stay in one split.
    for n, row in enumerate(selected):
        for category in ('clear', 'detail', 'angle_or_screen'):
            cases.append(dict(id=f'work-{n+1:02d}-{category}', category=category,
                split='holdout' if n % 4 == 3 else 'development',
                target_source_id=row['_id'], target_title=row['title'], catalogue_url=row.get('source_url'),
                path=None, source_url=None, source_image_id=None, sha256=None,
                gold_source_ids=[], identity_reviewed=False, reviewer=None,
                reference_overlap_reviewed=False, near_duplicate_reviewed=False))
    for n in range(30):
        cases.append(dict(id=f'outside-{n+1:02d}', category='outside_lookalike',
            split='holdout' if n % 3 == 2 else 'development', target_source_id=None,
            path=None, source_url=None, source_image_id=None, sha256=None,
            gold_source_ids=[], identity_reviewed=False, reviewer=None,
            reference_overlap_reviewed=False, near_duplicate_reviewed=False))
    draft = dict(version='photo-scale-v1-collection-plan', frozen=False, ready_for_evaluation=False,
        note='Empty collection slots, not 150 collected or reviewed photos. Targets are not gold labels.',
        sampling='Round-robin by catalogue title; deliberate category coverage, not random population sampling.',
        corpus_sha256=hashlib.sha256(corpus.read_bytes()).hexdigest(),
        references_sha256=hashlib.sha256(references.read_bytes()).hexdigest(),
        excluded_development_ids=sorted(excluded),
        reference_exclusions=[{k: r.get(k) for k in ('source_id', 'source_url', 'sha256')} for r in refs],
        cases=cases)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('x', encoding='utf-8') as handle:
        json.dump(draft, handle, ensure_ascii=False, indent=2)
    print(json.dumps(dict(works=len(selected), slots=len(cases), collected=0, reviewed=0,
                         splits=dict(Counter(c['split'] for c in cases)), ready=False)))


if __name__ == '__main__':
    main()
