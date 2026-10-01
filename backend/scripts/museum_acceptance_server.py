"""Deterministic reliability-test server. No real model calls or private corpus."""
import json
import os
from app.museum.api import create_app
from app.museum.config import MuseumSettings


class FixtureModel:
    usage_records = []
    async def complete_json(self, messages):
        prompt = messages[0]['content']
        if '只描述照片' in prompt:
            return {'usable':True,'visible_text':'Test Vase','visual_description':'Blue vase'}
        if '只识别用户内容' in prompt:
            return {'candidate_ids':['vase']}
        if '将追问改写' in prompt:
            return {'query':json.loads(messages[-1]['content'])['query']}
        if '判断游客' in prompt:
            return {'intent':'question','candidate_ids':[]}
        if '独立事实审查员' in prompt:
            return {'passed':True,'issues':[]}
        return {'abstain':False,'claims':[{'text':'材质为青铜。','source_id':'vase','quote':'Material: bronze.'}]}


def make_app():
    path = os.environ.get('MUSEUM_ACCEPTANCE_CONFIG')
    if not path:
        raise RuntimeError('This fixture server requires an explicit acceptance config')
    config = MuseumSettings(_env_file=None, **json.loads(open(path, encoding='utf-8').read()))
    if not config.mongodb_db.startswith('museum_test_'):
        raise ValueError('Acceptance database name must begin museum_test_')
    return create_app(config, client_factory=FixtureModel)
