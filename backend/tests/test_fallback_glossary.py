from app.museum.fallback_glossary import expand_query
from app.museum.config import MuseumSettings


def test_expansion_is_retrieval_text_with_explicit_provenance():
    expanded,audit=expand_query('黑色石膏胸像')
    assert all(word in expanded.split() for word in ['black','plaster','bust'])
    assert audit['matched']=={'黑色':'black','石膏':'plaster','胸像':'bust'}
    assert 'va-' not in expanded and 'bust' in expanded


def test_no_matching_terms_and_overlapping_aliases():
    expanded,audit=expand_query('一个陌生作品')
    assert audit['matched']=={} and '陌生' in expanded
    expanded,_=expand_query('半身像和胸像')
    assert expanded.split().count('bust')==0
    assert MuseumSettings(_env_file=None).museum_fallback_glossary is False


def test_single_broad_cue_does_not_replace_precise_chinese_retrieval():
    expanded,audit=expand_query('画着蓝色郁金香和人物、手柄上有孔的壶')
    assert not audit['applied'] and 'blue' not in expanded.split()
    expanded,audit=expand_query('找带镀金装饰的作品')
    assert not audit['applied'] and 'gilt' not in expanded.split()


def test_catalogue_expansion_is_opt_in_and_preserves_distinct_concept_gate():
    query = '找红漆椅子，椅背中央有蝙蝠和云纹'
    assert not expand_query(query)[1]['applied']
    expanded, audit = expand_query(query, catalogue=True)
    assert {'chair', 'lacquer', 'bats', 'cloud'} <= set(expanded.split())
    assert audit['version'] == 'catalogue-bilingual-glossary-v1'
    assert MuseumSettings(_env_file=None).museum_catalogue_glossary is False
    assert not expand_query('椅子座椅', catalogue=True)[1]['applied']
    assert not expand_query('看看萨福', catalogue=True)[1]['applied']


def test_catalogue_negation_does_not_add_positive_excluded_motif():
    for query in ['找红漆椅子，不要蝙蝠纹', '不是萨福的黑色石膏胸像']:
        before, _ = expand_query(query)
        after, audit = expand_query(query, catalogue=True)
        assert after == before
        assert audit['catalogue_guard'] == 'negation'


def test_catalogue_vocabulary_does_not_map_homophonous_characters():
    expanded, audit = expand_query('演员红了，戏剧气氛很好', catalogue=True)
    assert not audit['applied'] and 'red' not in expanded.split()


def test_broad_type_alone_does_not_dilute_existing_distinctive_cues():
    query = '举着驴的下颚骨打人的大理石雕塑'
    before, _ = expand_query(query)
    after, audit = expand_query(query, catalogue=True)
    assert after == before and audit['suppressed_broad_only'] == ['雕塑']
    # A category can still unlock a formerly untranslated one-cue query.
    expanded, audit = expand_query('盘子形状的雕塑', catalogue=True)
    assert audit['applied'] and 'sculpture' in expanded.split()
