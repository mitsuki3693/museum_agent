"""Synthetic floor graph: functional checks, not real museum navigation accuracy."""
import json
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.museum.api import create_app
from app.museum.config import MuseumSettings
from app.museum.routes import RoutePlanner, RoutePreferences, RouteMap, is_route_question


@pytest.fixture
def route_data():
    def node(key, tag, dwell=8):
        return {"id": key, "title": key, "level": "1", "description": "Synthetic test stop.",
                "interests": [tag], "dwell_minutes": dwell, "source_ids": ["fixture"]}
    def edge(a, b, step_free=True):
        return {"a": a, "b": b, "minutes": 2, "instruction": f"{a} to {b}", "source_ids": ["fixture"], "step_free": step_free}
    return {"venue": "Synthetic museum", "scope": "Test galleries", "version": "test-1", "synthetic": True,
            "checked_at": str(date.today()), "map_url": "https://example.org/map", "default_start": "entrance",
            "notice": "Synthetic fixture, not a real visitor route.",
            "sources": [{"id": "fixture", "title": "Synthetic map", "url": "https://example.org/map"}],
            "interests": [{"id": "sculpture", "label": "雕塑"}, {"id": "design", "label": "设计"}, {"id": "painting", "label": "绘画"}],
            "nodes": [{"id": "entrance", "title": "入口", "level": "1", "kind": "entrance", "description": "Start", "dwell_minutes": 0, "source_ids": ["fixture"]},
                      node("g1", "sculpture"), node("g2", "design"), node("g3", "painting"), node("g4", "design", 20)],
            "edges": [edge("entrance", "g1"), edge("g1", "g2"), edge("g2", "g3"), edge("entrance", "g4", None)]}


def load(tmp_path, data):
    path = tmp_path / "route.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return RoutePlanner(path)


def preferences(**kwargs):
    return RoutePreferences(start_id="entrance", **kwargs)


def test_budget_includes_every_movement_and_viewing_stop(tmp_path, route_data):
    result = load(tmp_path, route_data).plan(preferences(minutes=30))["route"]
    assert [s["id"] for s in result["steps"]] == ["g1", "g2", "g3"]
    assert result["total_minutes"] == result["walk_minutes"] + result["view_minutes"] == 30
    assert result["remaining_minutes"] == 0
    assert result["steps"][-1]["elapsed_minutes"] == 30
    assert all(via["level"] == "1" for step in result["steps"] for via in step["via"])
    assert result["synthetic"] is True


def test_interests_change_stop_selection_without_inventing_shortcut(tmp_path, route_data):
    planner = load(tmp_path, route_data)
    sculpt = planner.plan(preferences(minutes=15, interests=["sculpture"]))["route"]
    paint = planner.plan(preferences(minutes=15, interests=["painting"]))["route"]
    assert [s["id"] for s in sculpt["steps"]] == ["g1"]
    assert [s["id"] for s in paint["steps"]] == ["g3"]
    assert [p["id"] for p in paint["steps"][0]["via"]] == ["g1", "g2", "g3"]
    assert paint["steps"][0]["walk_minutes"] == 6
    assert paint["total_minutes"] <= 15


def test_skipping_a_visit_still_counts_passing_through_the_gallery(tmp_path, route_data):
    route = load(tmp_path, route_data).plan(preferences(minutes=30, skip_ids=["g2"]))["route"]
    assert [s["id"] for s in route["steps"]] == ["g1", "g3"]
    assert route["total_minutes"] == 22
    assert [v["id"] for v in route["steps"][1]["via"]] == ["g2", "g3"]


def test_closed_transit_node_is_not_used_as_a_shortcut(tmp_path, route_data):
    route_data["nodes"][2]["closed"] = True  # g2
    route = load(tmp_path, route_data).plan(preferences(minutes=30, interests=["painting"]))["route"]
    assert "g3" not in [s["id"] for s in route["steps"]]
    assert all("g2" not in [v["id"] for v in s["via"]] for s in route["steps"])


def test_unknown_accessibility_is_never_treated_as_step_free(tmp_path, route_data):
    for edge in route_data["edges"]:
        edge["step_free"] = None
    result = load(tmp_path, route_data).plan(preferences(minutes=30, step_free=True))
    assert result["status"] == "route_unavailable" and "route" not in result
    assert "无台阶" in result["answer"]


def test_stairs_are_filtered_but_verified_step_free_connections_remain(tmp_path, route_data):
    route_data["edges"][-1]["step_free"] = False
    route = load(tmp_path, route_data).plan(preferences(minutes=30, interests=["design"], step_free=True))["route"]
    assert all(v["step_free"] is True for s in route["steps"] for v in s["via"])


def test_reverse_connections_are_not_assumed(tmp_path, route_data):
    planner = load(tmp_path, route_data)
    result = planner.plan(RoutePreferences(start_id="g3", minutes=90, skip_ids=["g3"]))
    assert result["status"] == "route_unavailable"


def test_current_gallery_can_be_visited_without_an_invented_move(tmp_path, route_data):
    route = load(tmp_path, route_data).plan(RoutePreferences(start_id="g3", minutes=15))["route"]
    assert [s["id"] for s in route["steps"]] == ["g3"]
    assert route["steps"][0]["via"] == []
    assert route["walk_minutes"] == 0 and route["total_minutes"] == 8


@pytest.mark.parametrize("field,value", [("start_id", "invented"), ("interests", ["invented"]), ("skip_ids", ["invented"])])
def test_unknown_request_identifiers_are_rejected(tmp_path, route_data, field, value):
    prefs = {"start_id": "entrance", field: value}
    with pytest.raises(ValueError):
        load(tmp_path, route_data).plan(RoutePreferences(**prefs))


@pytest.mark.parametrize("days", [31, -1])
def test_expired_or_future_snapshot_cannot_produce_a_route(tmp_path, route_data, days):
    route_data["checked_at"] = str(date.today() - timedelta(days=days))
    planner = load(tmp_path, route_data)
    assert not planner.options()["available"]
    assert planner.plan(preferences())["status"] == "route_unavailable"


def test_manifest_rejects_unsourced_edges(tmp_path, route_data):
    route_data["edges"][0]["source_ids"] = ["invented"]
    with pytest.raises(ValidationError):
        load(tmp_path, route_data)


@pytest.mark.parametrize("query,wanted", [("我有30分钟，帮我规划参观路线", True), ("带我逛博物馆", True),
    ("Plan a visit route", True), ("这幅画里的道路是怎么画的？", False), ("画家去罗马走哪条路线？", False)])
def test_route_intent_does_not_hijack_art_questions(query, wanted):
    assert is_route_question(query) is wanted


@pytest.fixture
def route_settings(tmp_path, route_data):
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps([{"_id": "test-1", "title": "Vase", "content": "Material: bronze.",
        "source_url": "https://example.org/1", "source_hash": "fixture", "status": "active", "license": "CC0", "fetched_at": "2026-09-29"}]), encoding="utf-8")
    load(tmp_path, route_data)
    return MuseumSettings(_env_file=None, museum_corpus=corpus, museum_route_manifest=tmp_path / "route.json", museum_embedding="lexical")


def session(client):
    return {"Authorization": "Bearer " + client.post('/api/museum/sessions').json()['token']}


def test_real_chat_endpoint_setup_plan_cache_feedback_and_isolation(route_settings):
    with TestClient(create_app(route_settings)) as client:
        a, b = session(client), session(client)
        setup = client.post('/api/museum/chat', headers=a, json={"query": "我有30分钟，想看雕塑，帮我规划参观路线", "request_id": str(uuid.uuid4())})
        assert setup.json()["status"] == "route_setup"
        assert setup.json()["route_preferences"]["interests"] == ["sculpture"]
        body = {"query": "规划路线", "action": "route", "route": preferences().model_dump(), "request_id": str(uuid.uuid4())}
        assert client.post('/api/museum/chat', json=body).status_code == 401
        first = client.post('/api/museum/chat', headers=a, json=body)
        assert first.status_code == 200 and first.json()["status"] == "route_ready"
        again = client.post('/api/museum/chat', headers=a, json=body)
        assert again.json()["trace_id"] == first.json()["trace_id"]
        body["route"]["minutes"] = 45
        assert client.post('/api/museum/chat', headers=a, json=body).status_code == 409
        feedback = {"trace_id": first.json()["trace_id"], "kind": "helpful"}
        assert client.post('/api/museum/feedback', headers=a, json=feedback).status_code == 200
        assert client.post('/api/museum/feedback', headers=b, json=feedback).status_code == 404
        assert client.delete('/api/museum/session', headers=a).status_code == 200


def test_missing_route_map_does_not_fall_back_to_llm_navigation(route_settings):
    route_settings.museum_route_manifest = None
    with TestClient(create_app(route_settings)) as client:
        response = client.post('/api/museum/chat', headers=session(client), json={"query": "规划路线", "request_id": str(uuid.uuid4())})
        assert response.json()["status"] == "route_unavailable"
        assert not response.json()["route_options"]["available"]


def test_invalid_route_parameters_are_not_cached_as_valid_plans(route_settings):
    with TestClient(create_app(route_settings)) as client:
        a = session(client)
        body = {"query": "规划路线", "action": "route", "route": {"start_id": "unknown"}, "request_id": str(uuid.uuid4())}
        assert client.post('/api/museum/chat', headers=a, json=body).status_code == 422
        body["route"] = preferences().model_dump()
        assert client.post('/api/museum/chat', headers=a, json=body).json()["status"] == "route_ready"


def test_model_can_only_propose_preferences_and_cannot_create_map_nodes(route_settings):
    class FakeClient:
        async def complete_json(self, messages):
            return {"minutes": 300, "start_id": "invented_lift", "interests": ["secret_room"], "step_free": False}
    route_settings.deepseek_api_key = "test-only"
    with TestClient(create_app(route_settings, client_factory=FakeClient)) as client:
        result = client.post('/api/museum/chat', headers=session(client), json={"query": "我有30分钟，想看雕塑，请帮我规划路线", "request_id": str(uuid.uuid4())}).json()
        assert result["status"] == "route_setup"
        assert result["route_preferences"]["start_id"] == "entrance"
        assert result["route_preferences"]["minutes"] == 30
        assert result["route_preferences"]["interests"] == ["sculpture"]


def test_confirmed_plan_does_not_need_any_model_call(route_settings):
    class NoModel:
        async def complete_json(self, messages):
            raise AssertionError("Route calculation must not call the model")
    route_settings.deepseek_api_key = "test-only"
    with TestClient(create_app(route_settings, client_factory=NoModel)) as client:
        result = client.post('/api/museum/chat', headers=session(client), json={"query": "规划路线", "action": "route", "route": preferences().model_dump(), "request_id": str(uuid.uuid4())}).json()
        assert result["status"] == "route_ready"


def test_negative_interest_is_not_selected_as_a_preference(route_settings):
    with TestClient(create_app(route_settings)) as client:
        result = client.post('/api/museum/chat', headers=session(client), json={"query": "我不喜欢雕塑，想看绘画，帮我规划参观路线", "request_id": str(uuid.uuid4())}).json()
        assert result["route_preferences"]["interests"] == ["painting"]


@pytest.mark.parametrize("query,wanted", [("厕所在哪", True), ("找出口", True), ("找楼梯／电梯", True),
    ("服务台在哪里", True), ("最近的洗手间怎么走", True), ("为什么画中有楼梯", False)])
def test_facility_queries_are_recognized_without_hijacking_art_history(query, wanted):
    assert is_route_question(query) is wanted


@pytest.mark.parametrize("kind,query", [("toilet", "最近的厕所怎么走"), ("exit", "找出口"),
    ("stairs", "楼梯在哪"), ("help", "服务台在哪里")])
def test_facility_lookup_returns_only_sourced_positions_and_no_invented_route(route_settings, route_data, kind, query):
    route_data["facilities"] = [{"id": kind, "kind": kind, "title": "Synthetic facility", "location": "Test location",
        "note": "No live availability", "map_url": "https://example.org/map", "source_ids": ["fixture"]}]
    route_settings.museum_route_manifest.write_text(json.dumps(route_data), encoding="utf-8")
    with TestClient(create_app(route_settings)) as client:
        result = client.post('/api/museum/chat', headers=session(client), json={"query": query, "request_id": str(uuid.uuid4())}).json()
        assert result["status"] == "facility_found"
        assert result["facilities"]["items"][0]["kind"] == kind
        assert "route" not in result and "不能判断哪个最近" in result["answer"]


def test_stair_constraint_stays_in_visit_planning(route_settings):
    with TestClient(create_app(route_settings)) as client:
        result = client.post('/api/museum/chat', headers=session(client), json={"query": "帮我规划路线，不走楼梯", "request_id": str(uuid.uuid4())}).json()
        assert result["status"] == "route_setup"
        assert result["route_preferences"]["step_free"] is True


def test_unsourced_facilities_are_rejected(tmp_path, route_data):
    route_data["facilities"] = [{"id": "help", "kind": "help", "title": "Desk", "location": "test", "note": "test",
        "map_url": "https://example.org/map", "source_ids": ["unknown"]}]
    with pytest.raises(ValidationError):
        load(tmp_path, route_data)
