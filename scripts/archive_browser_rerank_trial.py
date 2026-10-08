"""Archive explicitly supplied local browser acceptance trace IDs.

Run locally with PYTHONPATH=backend. No API requests; no raw source text,
session tokens, pictures or credentials are included in the private report.
"""
import hashlib
import json
import argparse
from pathlib import Path

from pymongo import MongoClient
from app.museum.config import MuseumSettings

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace_ids', nargs='+')
    parser.add_argument('--corpus-count', type=int, required=True)
    parser.add_argument('--output', type=Path,
                        default=Path('eval/private/reranker-browser-trial-v1.json'))
    args = parser.parse_args()
    settings = MuseumSettings()
    output = args.output
    private_root = Path('eval/private').resolve()
    if not output.resolve().is_relative_to(private_root):
        parser.error('--output must stay within eval/private')
    with MongoClient(settings.mongodb_uri) as client:
        db = client[settings.mongodb_db]
        rows = []
        for trace_id in args.trace_ids:
            row = db.museum_traces.find_one({'_id': trace_id})
            if row is None:
                raise RuntimeError(f'Missing acceptance trace: {trace_id}')
            result = row.get('result', {})
            rows.append({
                'trace_id': trace_id,
                'query': row.get('query'),
                'created_at': row['created_at'],
                'status': result.get('status'),
                'action': row.get('action'),
                'object_id': row.get('object_id'),
                'corpus_hash': row.get('corpus_hash'),
                'rerank': row.get('rerank'),
                'latency_ms': result.get('latency_ms'),
                'usage': result.get('usage', []),
                'candidate_ids': [c['id'] for c in result.get('candidates', [])],
                'feedback': [f.get('kind') for f in db.museum_feedback.find({'trace_id': trace_id})],
            })
    report = {'scope': 'desktop-browser-small-trial-not-mobile-or-load-test',
              'corpus_count': args.corpus_count, 'rows': rows}
    with output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'rows': len(rows), 'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
                      'statuses': [r['status'] for r in rows],
                      'rerank_ms': [(r.get('rerank') or {}).get('ms') for r in rows],
                      'usage_record_count': sum(len(r['usage']) for r in rows),
                      'feedback': [r['feedback'] for r in rows]}))


if __name__ == '__main__':
    main()
