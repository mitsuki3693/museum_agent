"""Check literal field availability in the frozen corpus; not QA accuracy."""
import argparse
import json
from pathlib import Path
from app.museum.config import MuseumSettings
from app.museum.catalogue_answers import literal_answer, VERSION
from evaluate_bilingual_retrieval import private_path, versions


def main(output):
    cfg=MuseumSettings()
    records=json.loads(cfg.museum_corpus.read_bytes())
    if cfg.museum_private_corpus:records+=json.loads(cfg.museum_private_corpus.read_bytes())
    report=dict(version=VERSION,versions=versions(cfg),records=len(records),rows=[])
    for record in records:
        available=[]
        for field,query in [('author','作者是谁？'),('date','它是什么年代的？'),('material','它是什么材质？')]:
            answer=literal_answer(query,record,record)
            if answer:
                assert answer['quote'] in record['content'] and answer['text'].endswith(answer['quote'].partition(':')[2].strip())
                available.append(field)
        report['rows'].append(dict(id=record['_id'],fields=available))
    report['counts']={f:sum(f in r['fields'] for r in report['rows']) for f in ['author','date','material']}
    with private_path(output).open('x',encoding='utf-8') as out:json.dump(report,out,ensure_ascii=False,indent=2)
    print(json.dumps(report['counts']))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    main(p.parse_args().output)
