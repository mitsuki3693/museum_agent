import copy
import json
import math

import pytest

from app.museum.search_fields import load_search_fields, field_documents
from app.retrieval.bm25 import BM25Index
from app.retrieval.bm25f import BM25FIndex


def test_single_field_equals_existing_bm25_and_rebuild_clears_state():
    docs = [dict(_id='a', content='bronze bronze sculpture'), dict(_id='b', content='glass vase')]
    old = BM25Index(); old.index(docs)
    new = BM25FIndex({'original': 1})
    new.index([dict(_id=d['_id'], search_fields={'original': d['content']}) for d in docs])
    assert new.search('bronze')[0]['score'] == pytest.approx(old.search('bronze')[0]['score'])
    new.index([dict(_id='c', search_fields={})])
    assert new.search('bronze') == []
    assert new.search('') == []
    assert new.search('glass', top_k=0) == []


def test_cross_field_tf_saturates_once_and_df_is_per_work():
    index = BM25FIndex({'title': 5, 'body': 1}, b=0)
    index.index([dict(_id='a',search_fields={'title':'bronze','body':'bronze bronze'}),
                 dict(_id='b',search_fields={'title':'glass','body':'glass'})])
    hit = index.search('bronze')[0]
    assert hit['score'] == pytest.approx(math.log(2) * 7 * 2.5 / 8.5)
    assert hit['matched_fields'] == ['body','title']
    assert len(index.postings['bronze']) == 1


def test_field_weight_changes_tie_without_losing_empty_fields():
    index = BM25FIndex({'title':5,'body':1}, b=0)
    index.index([dict(_id='a',search_fields={'body':'bronze'}),
                 dict(_id='b',search_fields={'title':'bronze'}),
                 dict(_id='c',search_fields={})])
    assert [r['id'] for r in index.search('bronze')] == ['b','a']
    with pytest.raises(ValueError):
        index.index([dict(_id='a'),dict(_id='a')])
    with pytest.raises(ValueError):
        BM25FIndex({'body':float('nan')})


def fixture_manifest():
    records={'a':dict(_id='a',title='Test vase',content='A bronze vase.',source_hash='hash',
                      source_url='https://example.org/a',status='active')}
    manifest=dict(schema_version=1,version='test-v1',records=[dict(source_id='a',source_hash='hash',
        source_url='https://example.org/a',fields={'material_zh':dict(text='青铜',evidence_quotes=['bronze'],
            derivation='agent_translation',review_status='draft',reviewer=None)})])
    return records,manifest


def test_draft_requires_opt_in_and_original_sources_do_not_change(tmp_path):
    records,manifest=fixture_manifest();before=copy.deepcopy(records)
    p=tmp_path/'fields.json';p.write_text(json.dumps(manifest))
    fields,meta=load_search_fields(p,records)
    assert meta['accepted_fields']==0
    fields,meta=load_search_fields(p,records,allow_drafts=True)
    docs=field_documents(records,fields)
    assert docs[0]['search_fields']['material_zh']=='青铜'
    assert docs[0]['search_fields']['original']=='Test vase\nA bronze vase.'
    assert records==before


def test_title_evidence_does_not_require_repetition_in_body(tmp_path):
    records,manifest=fixture_manifest()
    fields=manifest['records'][0]['fields']
    fields['title_zh']=dict(text='测试花瓶',evidence_quotes=['Test vase'],evidence_scope='title',
                           derivation='glossary',review_status='draft')
    p=tmp_path/'fields.json';p.write_text(json.dumps(manifest))
    assert load_search_fields(p,records,allow_drafts=True)[0]['a']['title_zh']=='测试花瓶'
    fields['title_zh']['evidence_scope']='content'
    p.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):load_search_fields(p,records,allow_drafts=True)


@pytest.mark.parametrize('corruption',['hash','url','quote','unknown','duplicate','field','review','status','empty'])
def test_manifest_fails_closed(tmp_path,corruption):
    records,manifest=fixture_manifest()
    e=manifest['records'][0];f=e['fields']['material_zh']
    if corruption=='hash':e['source_hash']='old'
    elif corruption=='url':e['source_url']='https://wrong.example/'
    elif corruption=='quote':f['evidence_quotes']=['not in original']
    elif corruption=='unknown':e['source_id']='unknown'
    elif corruption=='duplicate':manifest['records'].append(copy.deepcopy(e))
    elif corruption=='field':e['fields']['original']=e['fields'].pop('material_zh')
    elif corruption=='review':f['review_status']='human_reviewed'
    elif corruption=='status':records['a']['status']='archived'
    elif corruption=='empty':f['evidence_quotes']=['']
    p=tmp_path/'fields.json';p.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):load_search_fields(p,records,allow_drafts=True)
