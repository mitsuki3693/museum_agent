import copy
import textwrap
from app.museum.semantic_chunks import dense_views


def test_original_ids_and_sources_are_preserved_and_long_metadata_not_leaked():
    source=dict(_id='a',title='Vase',content='Museum number: C.123-1900\n尺寸 Dimensions: '+('120 mm '*70)+'\nphysicalDescription: Blue vase with birds.')
    before=copy.deepcopy(source);views=dense_views([source])
    pieces=[p for line in source['content'].splitlines() for p in textwrap.wrap(line,width=220)]
    assert views['original']==[dict(_id=f'a::{i}',source_id='a',content='Vase\n'+p) for i,p in enumerate(pieces)]
    assert len(views['filtered'])==len(views['normalized'])==1
    assert views['normalized'][0]['_id']==views['original'][-1]['_id']
    assert views['normalized'][0]['content']=='Vase\nBlue vase with birds.'
    assert source==before


def test_only_known_labels_are_removed_not_narrative_colons_or_later_sentences():
    source=dict(_id='a',title='Story',content='She said: a blue vase.\nsummaryDescription: First sentence.\nThe next paragraph: remains intact.')
    view=dense_views([source])['normalized']
    assert [r['content'] for r in view]==['Story\nShe said: a blue vase.','Story\nFirst sentence.','Story\nThe next paragraph: remains intact.']


def test_chinese_labels_normalized_without_translating_or_dropping_values():
    source=dict(_id='a',title='Painting',content='馆藏部门 Department: Painting\n馆方介绍 Description: Flowers on water.')
    views=dense_views([source])
    assert views['filtered'][0]['content']=='Painting\n馆方介绍 Description: Flowers on water.'
    assert views['normalized'][0]['content']=='Painting\nFlowers on water.'


def test_metadata_only_record_has_no_synthetic_semantic_claim():
    view=dense_views([dict(_id='a',title='Unknown',content='Title: Unknown\nMuseum number: 1\nDate: ' )])
    assert len(view['original'])==3
    assert view['normalized']==[]
    assert view['no_body_source_ids']==['a']


def test_wrapped_narrative_keeps_later_colons_and_original_chunk_ids():
    source=dict(_id='a',title='Work',content='briefDescription: '+('wooden object '*40)+' inscription: hello')
    views=dense_views([source])
    assert len(views['normalized'])==len(views['original'])
    assert [r['_id'] for r in views['normalized']]==[r['_id'] for r in views['original']]
    assert views['normalized'][-1]['content']==views['original'][-1]['content']
