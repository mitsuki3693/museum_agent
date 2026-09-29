"""One explicitly simulated path over an optional locally held official floor map."""
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class MapTile(StrictModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9-]+$", max_length=40)
    file: str
    x: float
    y: float
    size: int = Field(ge=1, le=2048)


class Waypoint(StrictModel):
    x: float
    y: float
    label: str
    instruction: str


class FloorCase(StrictModel):
    id: str
    title: str
    venue: str
    level: str
    attribution: str
    source_url: HttpUrl
    checked_at: str
    view_box: tuple[float, float, float, float]
    tiles: list[MapTile] = Field(min_length=1, max_length=64)
    waypoints: list[Waypoint] = Field(min_length=2, max_length=32)

    @model_validator(mode="after")
    def coherent(self):
        x, y, width, height = self.view_box
        if width <= 0 or height <= 0 or len({tile.id for tile in self.tiles}) != len(self.tiles):
            raise ValueError("Invalid floor map geometry")
        if any(not (x <= p.x <= x + width and y <= p.y <= y + height) for p in self.waypoints):
            raise ValueError("Waypoint outside map viewport")
        if any(a.x == b.x and a.y == b.y for a, b in zip(self.waypoints, self.waypoints[1:])):
            raise ValueError("Consecutive waypoints must differ")
        return self


class FloorDemo:
    def __init__(self, manifest: Path | None):
        self.root = manifest.resolve().parent if manifest else None
        self.case = FloorCase.model_validate_json(manifest.read_text(encoding="utf-8-sig")) if manifest else None

    def tile_path(self, tile_id: str) -> Path | None:
        if not self.case:
            return None
        tile = next((tile for tile in self.case.tiles if tile.id == tile_id), None)
        if not tile:
            return None
        path = (self.root / tile.file).resolve()
        if not path.is_relative_to(self.root) or path.suffix.lower() != ".png" or not path.is_file():
            return None
        return path

    def describe(self):
        if not self.case or any(self.tile_path(tile.id) is None for tile in self.case.tiles):
            return {"available": False}
        result = self.case.model_dump(mode="json", exclude={"tiles"})
        result["tiles"] = [{**tile.model_dump(exclude={"file"}),
            "url": f"/api/museum/routes/floor-demo/tiles/{tile.id}"} for tile in self.case.tiles]
        return {"available": True, "simulated_position": True, **result}
