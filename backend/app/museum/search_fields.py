"""Versioned retrieval-only annotations. No mutation of authoritative sources.

Consumed by named-title lookup and the opt-in Chinese retrieval path.
Provenance validation checks traceability, not semantic translation correctness.
"""
import hashlib
import json
from pathlib import Path

FIELD_WEIGHTS = dict(title_zh=5, aliases_zh=4, subject_zh=3,
                     appearance_zh=3, material_zh=2, original=1)


def load_search_fields(path: Path, records: dict, *, allow_drafts=False):
    raw = path.read_bytes()
    manifest = json.loads(raw)
    if manifest.get('schema_version') != 1 or not manifest.get('version'):
        raise ValueError('Unsupported search-field manifest')
    accepted, seen = {}, set()
    for entry in manifest['records']:
        sid = entry['source_id']
        if sid in seen or sid not in records:
            raise ValueError('Duplicate or unknown annotated work')
        seen.add(sid)
        source = records[sid]
        if (source.get('status') != 'active' or entry['source_hash'] != source['source_hash']
                or entry['source_url'] != source['source_url']):
            raise ValueError('Stale or mismatched annotation source')
        accepted[sid] = {}
        for name, field in entry['fields'].items():
            if name not in FIELD_WEIGHTS or name == 'original':
                raise ValueError('Unknown or authoritative field override')
            if not isinstance(field['text'], str) or not field['text'].strip():
                raise ValueError('Empty search field')
            anchors = field.get('evidence_quotes', [])
            scope = field.get('evidence_scope', 'content')
            if scope not in ('content', 'title'):
                raise ValueError('Unknown evidence scope')
            if (not isinstance(anchors, list) or not anchors
                    or any(not isinstance(q, str) or not q.strip() or q not in source[scope] for q in anchors)):
                raise ValueError('Missing exact source evidence')
            if not field.get('derivation'):
                raise ValueError('Missing derivation')
            status = field.get('review_status')
            if status not in ('draft', 'human_reviewed'):
                raise ValueError('Unknown review status')
            if status == 'human_reviewed' and not field.get('reviewer'):
                raise ValueError('Human review needs a reviewer')
            if status == 'human_reviewed' or allow_drafts:
                accepted[sid][name] = field['text']
    return accepted, dict(version=manifest['version'], sha256=hashlib.sha256(raw).hexdigest(),
                         allow_drafts=allow_drafts, accepted_fields=sum(map(len, accepted.values())))


def field_documents(records, auxiliary=None):
    auxiliary = auxiliary or {}
    return [dict(_id=sid, source_id=sid, search_fields={
        **auxiliary.get(sid, {}), 'original': r['title'] + '\n' + r['content']})
        for sid, r in records.items()]


def flattened_documents(docs):
    """Ablation: same texts, same work unit, no field weighting."""
    return [dict(_id=d['_id'], source_id=d['source_id'],
                 content='\n'.join(d['search_fields'].values())) for d in docs]
