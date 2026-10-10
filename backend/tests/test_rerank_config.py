import pytest
from pydantic import ValidationError

from app.museum.config import MuseumSettings


@pytest.mark.parametrize('budget', [160, 192, 256])
def test_evidence_budget_loads_from_env_file(tmp_path, budget):
    env = tmp_path / 'test.env'
    env.write_text(f'MUSEUM_RERANK_CONTEXT_BUDGET={budget}\nMUSEUM_RERANK_BACKEND=int8\n')
    settings = MuseumSettings(_env_file=env)
    assert settings.museum_rerank_context_budget == budget
    assert settings.museum_rerank_backend == 'int8'


def test_unsupported_evidence_budget_stays_rejected(tmp_path):
    env = tmp_path / 'test.env'
    env.write_text('MUSEUM_RERANK_CONTEXT_BUDGET=512\n')
    with pytest.raises(ValidationError):
        MuseumSettings(_env_file=env)
