import pytest

from app.museum.rerank_evidence_controls import remove_repeated_title, prioritize_material


def source(sid, material, **extra):
    return dict(_id=sid, status='active', source_hash=sid, content='materialsAndTechniques: '+material, **extra)


def test_repeated_title_only_is_removed_without_rewriting_evidence():
    text='Vase\nMaker: Unknown\nTitle: Vase\nbriefDescription: Painted vase.\nTitle: Other title'
    assert remove_repeated_title(text)=='Vase\nMaker: Unknown\nbriefDescription: Painted vase.\nTitle: Other title'
    assert remove_repeated_title('Title: Vase\nVase')=='Title: Vase\nVase'


def test_actual_material_overrides_model_order_without_dropping_candidates():
    records={s['_id']:s for s in [source('clay','Cast terracotta, painted.'),source('marble','Carved white marble'),source('unknown','Unknown')]}
    result,audit=prioritize_material('找大理石作品',['clay','marble','unknown'],records)
    assert result==['marble','clay','unknown']
    assert audit['matched_ids']==['marble']
    assert audit['evidence']['marble']=='Carved white marble'


@pytest.mark.parametrize('query',['不是大理石','大理石和青铜有什么区别','大理石雕塑是谁做的','《大理石》在哪里','找类似大理石的作品','find marble works'])
def test_negated_comparative_named_and_unsupported_queries_do_not_route(query):
    records={'a':source('a','Terracotta'),'b':source('b','Marble')}
    assert prioritize_material(query,['a','b'],records)[0]==['a','b']


@pytest.mark.parametrize('material',['Painted to resemble marble','Imitation marble','Marbled paper','Wood with marble base','Plaster after a marble original','Not marble','Marble effect resin'])
def test_imitation_negation_components_and_original_references_are_not_confirmed(material):
    records={'a':source('a','Unknown'),'b':source('b',material)}
    assert prioritize_material('找大理石作品',['a','b'],records)[0]==['a','b']


@pytest.mark.parametrize('query,material',[('找青铜雕塑','Cast bronze'),('石膏作品','Painted plaster'),('陶土雕塑','Fired terracotta'),('瓷器','Porcelain'),('木制作品','Carved wood')])
def test_controlled_material_vocabulary(query,material):
    records={'a':source('a','Unknown'),'b':source('b',material)}
    assert prioritize_material(query,['a','b'],records)[0]==['b','a']


def test_body_mentions_are_not_material_and_unknown_is_not_conflict():
    a=source('a','Unknown');a['content']+='\nsummaryDescription: Inspired by marble sculpture.'
    assert prioritize_material('找大理石作品',['a'],{'a':a})[1]['matched_ids']==[]


def test_matching_materials_preserve_model_order_and_source_objects():
    records={'a':source('a','Marble'),'b':source('b','White marble'),'c':source('c','Terracotta')}
    assert prioritize_material('找大理石作品',['c','b','a'],records)[0]==['b','a','c']
    assert records['a']['content']=='materialsAndTechniques: Marble'


def test_archived_source_fails_closed():
    record=source('a','Marble');record['status']='archived'
    with pytest.raises(ValueError):prioritize_material('找大理石作品',['a'],{'a':record})
