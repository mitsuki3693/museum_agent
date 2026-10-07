import copy

from app.museum.recall_fields import extend_manifest, lexical_query, named_title_ids,order_named_ids


def test_homonym_order_preserves_existing_candidates_then_keeps_other_names():
    assert order_named_ids(['a','b','c'],['unrelated','b','a'])==['b','a','c']


def test_full_specific_names_keep_collisions_but_not_negations_or_categories():
    annotations={'a':{'title_zh':'参孙击杀非利士人'},'b':{'title_zh':'参孙击杀非利士人'},
                 'c':{'title_zh':'花瓶 花器'},'d':{'aliases_zh':'参孙与非利士人'}}
    assert named_title_ids('找参孙击杀非利士人的雕塑',annotations)==['a','b']
    assert named_title_ids('参孙与非利士人',annotations)==['d']
    assert named_title_ids('不是参孙击杀非利士人',annotations)==[]
    assert named_title_ids('不要参孙击杀非利士人',annotations)==[]
    assert named_title_ids('找类似参孙击杀非利士人的雕塑',annotations)==[]
    assert named_title_ids('找一个花瓶',annotations)==[]


def test_chinese_query_filter_keeps_negation_and_discriminating_terms():
    assert '的' not in lexical_query('找参孙击杀非利士人的雕塑').split()
    assert '参孙' in lexical_query('找参孙击杀非利士人的雕塑').split()
    assert '不是' in lexical_query('不是蓝色的花瓶').split()
    assert lexical_query('not a blue vase')=='not a blue vase'


def source(title='Water Lilies', artist='Claude Monet', material='Oil on canvas'):
    return dict(_id='sample', title=title, source_hash='h', source_url='https://example.org/1',
                status='active', content=f'Artist: {artist}\nMedium: {material}',
                fields={'artist_display': artist, 'medium_display': material})


def test_translation_is_source_anchored_and_does_not_mutate_original():
    records={'sample':source()}
    original=copy.deepcopy(records)
    manifest={'schema_version':1,'version':'old','records':[]}
    output=extend_manifest(manifest,records)
    fields=output['records'][0]['fields']
    assert '睡莲' in fields['title_zh']['text']
    assert '莫奈' in fields['subject_zh']['text']
    assert fields['title_zh']['evidence_scope']=='title'
    assert all(f['review_status']=='draft' for f in fields.values())
    assert records==original and manifest['records']==[]


def test_author_mention_in_description_is_not_author_credit():
    record=source(artist='Anonymous')
    record['content']+='\nInspired by Claude Monet.'
    output=extend_manifest({'schema_version':1,'version':'old','records':[]},{'sample':record})
    assert 'subject_zh' not in output['records'][0]['fields']


def test_gilding_uses_material_field_not_incidental_or_negative_mentions():
    for material, expected in [('Tin-glazed earthenware with gilding',True),
                               ('Porcelain without gilding',False),('Ungilded silver',False)]:
        record=source(title='Plate',material=material)
        record['content']+='\nA different object has gilding.'
        result=extend_manifest({'schema_version':1,'version':'old','records':[]},{'sample':record})
        fields=result['records'][0]['fields']
        assert ('material_zh' in fields)==expected


def test_existing_reviewed_field_is_not_silently_rewritten():
    field=dict(text='人工名',evidence_quotes=['Water Lilies'],evidence_scope='title',
               review_status='human_reviewed',reviewer='reviewer',derivation='reviewed')
    entry=dict(source_id='sample',source_hash='h',source_url='https://example.org/1',fields={'title_zh':field})
    result=extend_manifest({'schema_version':1,'version':'old','records':[entry]},{'sample':source()})
    assert result['records'][0]['fields']['title_zh']==field
