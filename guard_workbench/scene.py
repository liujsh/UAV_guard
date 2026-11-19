"""Validated scene settings shared by the editor and simulation backends."""

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import tempfile


FORMAT_VERSION = 1
CITY_TEMPLATE = "urban_occlusion"


def _number(field: str, value: object, minimum: float, maximum: float) -> None:
    if type(value) not in (int, float) or not minimum <= value <= maximum:
        raise ValueError(f"{field} 必须在 [{minimum}, {maximum}] 范围内")


@dataclass(frozen=True, slots=True)
class Obstacle:
    """A box relative to the scenario origin, in the North/East ground plane."""

    north_m: float
    east_m: float
    length_m: float
    width_m: float
    height_m: float
    start_s: float = 0.0
    end_s: float | None = None

    def __post_init__(self) -> None:
        _number("north_m", self.north_m, -1_000, 1_000)
        _number("east_m", self.east_m, -1_000, 1_000)
        _number("length_m", self.length_m, 0.05, 100)
        _number("width_m", self.width_m, 0.05, 100)
        _number("height_m", self.height_m, 0.05, 100)
        _number("start_s", self.start_s, 0, 3_600)
        if self.end_s is not None:
            _number("end_s", self.end_s, 0, 3_600)
            if self.end_s <= self.start_s:
                raise ValueError("end_s 必须晚于 start_s")

    def is_active(self, elapsed_s: float) -> bool:
        """An obstacle's active time window includes its start and excludes its end."""
        return self.start_s <= elapsed_s and (self.end_s is None or elapsed_s < self.end_s)


@dataclass(frozen=True, slots=True)
class SceneConfig:
    """A scene template and its per-run overrides, independent of AirSim."""

    template_id: str = CITY_TEMPLATE
    seed: int = 42
    duration_s: float = 60.0
    target_speed_mps: float = 0.7
    target_route_direction: str = "forward"
    illumination_gain: float = 1.0
    pedestrian_count: int = 0
    vehicle_count: int = 0
    obstacles: tuple[Obstacle, ...] = ()

    def __post_init__(self) -> None:
        if self.template_id != CITY_TEMPLATE:
            raise ValueError("template_id 不属于已支持的场景模板")
        if self.target_route_direction not in ("forward", "reverse"):
            raise ValueError("target_route_direction 必须为 forward 或 reverse")
        for field, value, maximum in (
            ("seed", self.seed, 2**32 - 1),
            ("pedestrian_count", self.pedestrian_count, 4),
            ("vehicle_count", self.vehicle_count, 1),
        ):
            if type(value) is not int:
                raise ValueError(f"{field} 必须是整数")
            _number(field, value, 0, maximum)
        _number("duration_s", self.duration_s, 1, 3_600)
        _number("target_speed_mps", self.target_speed_mps, 0, 15)
        _number("illumination_gain", self.illumination_gain, 0.05, 1)
        if not isinstance(self.obstacles, tuple) or len(self.obstacles) > 32:
            raise ValueError("obstacles 应为最多包含 32 项的障碍物元组")
        for index, obstacle in enumerate(self.obstacles):
            if not isinstance(obstacle, Obstacle):
                raise ValueError(f"obstacles[{index}] 必须是障碍物")
            if obstacle.start_s >= self.duration_s:
                raise ValueError(f"obstacles[{index}].start_s 必须早于仿真结束")
            if obstacle.end_s is not None and obstacle.end_s > self.duration_s:
                raise ValueError(f"obstacles[{index}].end_s 不能晚于仿真结束")


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    fields: dict[str, object] = {}
    for key, value in pairs:
        if key in fields:
            raise ValueError(f"配置字段重复：{key}")
        fields[key] = value
    return fields


def load_scene(path: Path) -> SceneConfig:
    """Load and validate a saved scene configuration."""
    payload = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_fields)
    if not isinstance(payload, dict) or set(payload) != {"format_version", "scene"}:
        raise ValueError("配置必须包含 format_version 和 scene")
    version = payload["format_version"]
    if type(version) is not int or version != FORMAT_VERSION:
        raise ValueError(f"不支持的场景配置版本：{version!r}")
    fields = payload["scene"]
    if not isinstance(fields, dict):
        raise ValueError("scene 必须是 JSON 对象")
    items = fields.get("obstacles", [])
    if not isinstance(items, list):
        raise ValueError("obstacles 必须是 JSON 数组")
    obstacles = []
    for index, item in enumerate(items):
        try:
            obstacles.append(Obstacle(**item))
        except (TypeError, ValueError) as error:
            raise ValueError(f"obstacles[{index}]：{error}") from error
    try:
        return SceneConfig(**{**fields, "obstacles": tuple(obstacles)})
    except TypeError as error:
        raise ValueError(f"scene 字段无效：{error}") from error


def save_scene(path: Path, config: SceneConfig) -> None:
    """Write a complete scene snapshot before replacing the previous file."""
    payload = {"format_version": FORMAT_VERSION, "scene": asdict(config)}
    contents = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(contents)
            temporary.flush()
            os.fsync(temporary.fileno())
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
