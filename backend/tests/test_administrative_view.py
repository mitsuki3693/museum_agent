import copy

from app.museum.semantic_chunks import administrative_view, dense_views


def test_descriptive_fields_and_unknown_labels_survive_without_source_edits():
    record=dict(_id='a',title='Vase',content='Title: Vase\nMaker: Factory\nDate: 1700\n'
        'Place: Delft\nMaterials and techniques: Ceramic\n分类 Classification: Vases\n'
        'Museum number: 123\n馆藏部门 Department: Ceramics\n'
        'Unknown label: preserve this\nphysicalDescription: A tall object.')
    before=copy.deepcopy(record)
    original=dense_views([record])['original']
    view=administrative_view([record])
    assert view['chunks']==[c for c in original if c['_id'] not in {'a::6','a::7'}]
    assert record==before


def test_wrapped_admin_line_is_excluded_without_dropping_narrative_dimensions():
    record=dict(_id='a',title='Work',content='尺寸 Dimensions: '+('123 mm '*100)+
                '\nphysicalDescription: The height is 123 mm.\nDimensions discussed: important.')
    view=administrative_view([record])
    assert len(view['excluded'])>1
    assert len(view['chunks'])==2
    assert 'height is 123 mm' in view['chunks'][0]['content']
    assert 'Dimensions discussed' in view['chunks'][1]['content']


def test_metadata_only_work_retains_descriptive_vectors():
    view=administrative_view([dict(_id='a',title='Work',content='Museum number: 1\nMaker: Alice\nDate: 2009')])
    assert [c['_id'] for c in view['chunks']]==['a::1','a::2']
