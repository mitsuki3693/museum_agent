"""Read-only, explicitly synthetic staff-to-visitor demonstration.

This endpoint never reads internal conservation records, changes live closures,
or grants a staff role. Real staff operations must use authenticated endpoints.
"""
from copy import deepcopy
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from .routes import RoutePlanner, RoutePreferences


class DemoCase(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    title: str
    affected_id: str
    preferences: RoutePreferences
    people: int = Field(ge=1, le=100)
    humidity: float = Field(ge=0, le=100)
    attention_humidity: float = Field(ge=0, le=100)
    # Polygon and positions use the same local pixel coordinates as FloorDemo.
    area: list[tuple[float, float]] = Field(min_length=3, max_length=12)
    visitors: list[tuple[float, float]] = Field(min_length=1, max_length=100)
    source_title: str
    source_url: HttpUrl
    evidence: list[str] = Field(min_length=1, max_length=5)

    @model_validator(mode='after')
    def matching_visitors(self):
        if len(self.visitors) != self.people:
            raise ValueError('People count must match simulated markers')
        return self


class OperationsDemo:
    def __init__(self, manifest: Path | None, planner: RoutePlanner):
        self.case = DemoCase.model_validate_json(manifest.read_text(encoding='utf-8-sig')) if manifest else None
        self.planner = planner

    def describe(self):
        if not self.case or self.planner.unavailable():
            return {'available': False, 'simulated': True}
        case = self.case
        if case.affected_id not in self.planner.nodes or case.affected_id == case.preferences.start_id:
            return {'available': False, 'simulated': True}
        # A request-local copy excludes the gallery as a destination AND transit.
        # Never mutate the live planner or create a shortcut through the wall.
        preview = deepcopy(self.planner)
        preview.nodes[case.affected_id].closed = True
        before = self.planner.plan(case.preferences)
        after = preview.plan(case.preferences)
        return {'available': True, 'simulated': True, **case.model_dump(mode='json'),
                'affected_title': self.planner.nodes[case.affected_id].title,
                'before': before, 'after': after}
