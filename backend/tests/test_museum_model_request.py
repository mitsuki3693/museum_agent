import json
import httpx
import pytest
from app.config import Settings
from app.llm.deepseek import DeepSeekClient
from app.museum.config import MuseumSettings
from app.museum.engine import MuseumEngine


@pytest.mark.asyncio
async def test_museum_disables_thinking_on_actual_http_payload(monkeypatch):
    """Regression: default reasoning exhausted the 1800-token output budget."""
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '{"ok": true}'}}],
            "usage": {"total_tokens": 20},
        })

    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(
        transport=httpx.MockTransport(respond), **kw))
    config = MuseumSettings(_env_file=None, deepseek_api_key="test-only")
    museum = MuseumEngine(config, None, None)._client()
    assert await museum.complete_json([{"role": "user", "content": "JSON please"}]) == {"ok": True}
    assert requests[-1]["thinking"] == {"type": "disabled"}
    assert requests[-1]["max_tokens"] == 1800
    assert museum.usage_records == [{"model": config.deepseek_model, "total_tokens": 20}]

    legacy = DeepSeekClient(Settings(_env_file=None, deepseek_api_key="test-only"))
    await legacy.complete([{"role": "user", "content": "hello"}])
    assert "thinking" not in requests[-1]
