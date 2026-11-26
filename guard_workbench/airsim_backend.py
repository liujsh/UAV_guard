"""Scenario actor orchestration and rendered observations in AirSim Blocks 1.8.1."""

from base64 import b64encode
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO
import logging
import math
from random import Random
from uuid import uuid4

from PIL import Image

from .airsim_rpc import AirSimRPC
from .offline import SimulationCancelled
from .route import target_position
from .scene import SceneConfig
from .uncertainty import (
    ESTIMATOR_VERSION, RATE_HZ, DelayedCV, ellipse_area, estimate_record, quality,
)


AIRSIM_BACKEND = "airsim-blocks-v1"
_ORIGIN_NORTH, _ORIGIN_EAST, _GROUND_DOWN = -30.0, -60.0, 1.02
_CAMERA = "Workbench"
_TARGET_ID = 42
_TARGET_COLOR = (92, 31, 106)
_SIZE_LIMIT = 1_000_000


def _vector(x: float, y: float, z: float) -> dict:
    return {"x_val": x, "y_val": y, "z_val": z}


def _xyz(vector: dict) -> tuple[float, float, float]:
    coordinates = tuple(float(vector[key]) for key in ("x_val", "y_val", "z_val"))
    if not all(math.isfinite(value) for value in coordinates):
        raise ValueError("AirSim 返回的坐标无效")
    return coordinates


def _pose(north: float, east: float, height: float) -> dict:
    return {
        "position": _vector(_ORIGIN_NORTH + north, _ORIGIN_EAST + east, _GROUND_DOWN - height / 2),
        "orientation": {"w_val": 1.0, "x_val": 0.0, "y_val": 0.0, "z_val": 0.0},
    }


def _image(rpc: AirSimRPC, image_type: int) -> tuple[bytes, int]:
    [response] = rpc.call("simGetImages", [{"camera_name": _CAMERA, "image_type": image_type,
                                           "pixels_as_float": False, "compress": True}], "", True)
    raw = response["image_data_uint8"]
    data = raw.encode("utf-8", errors="surrogateescape") if isinstance(raw, str) else bytes(raw)
    if len(data) > _SIZE_LIMIT or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError(f"AirSim 相机 {_CAMERA} 未返回有效 PNG")
    return data, response["time_stamp"]


def _target_pixels(data: bytes) -> int:
    with Image.open(BytesIO(data)) as image:
        if image.width * image.height > 2_000_000:
            raise ValueError("分割图像尺寸过大，请降低 AirSim 相机分辨率")
        colors = image.convert("RGB").getcolors(image.width * image.height)
        return next((count for count, color in colors if color == _TARGET_COLOR), 0)


@dataclass(frozen=True, slots=True)
class AirSimEndpoint:
    port: int = 41451

    def __post_init__(self) -> None:
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("AirSim 本地端口必须在 1–65535 范围内")


class _World:
    def __init__(self, rpc: AirSimRPC, cube: str, cancelled: Callable[[], bool]) -> None:
        self.rpc = rpc
        self.cube = cube
        self.cancelled = cancelled
        self.prefix = f"GuardWorkbench_{uuid4().hex[:12]}"
        self.actors: list[str] = []

    def spawn(self, role: str, north: float, east: float, dimensions: tuple[float, float, float]) -> str:
        if self.cancelled():
            raise SimulationCancelled()
        name = f"{self.prefix}_{role}"
        self.actors.append(name)
        pose = _pose(north, east, dimensions[2])
        actual = self.rpc.call("simSpawnObject", name, self.cube, pose, _vector(*dimensions), False, False)
        if actual and actual != name:
            self.actors[-1] = actual
        if not actual:
            raise RuntimeError(f"AirSim 无法放置场景对象：{role}")
        if math.dist(self.position(actual), _xyz(pose["position"])) > 0.05:
            raise RuntimeError(f"AirSim 对象位置与配置不一致：{role}")
        if math.dist(_xyz(self.rpc.call("simGetObjectScale", actual)), dimensions) > 0.05:
            raise RuntimeError(f"AirSim 对象尺寸与配置不一致：{role}")
        segmentation_id = _TARGET_ID if role == "target" else 0
        if not self.rpc.call("simSetSegmentationObjectID", actual, segmentation_id, False):
            raise RuntimeError(f"无法为 AirSim 场景对象设置分割标签：{role}")
        return actual

    def move(self, name: str, north: float, east: float, height: float) -> None:
        if not self.rpc.call("simSetObjectPose", name, _pose(north, east, height), True):
            raise RuntimeError(f"AirSim 无法移动场景对象：{name}")

    def position(self, name: str) -> tuple[float, float, float]:
        return _xyz(self.rpc.call("simGetObjectPose", name)["position"])

    def cleanup(self, was_paused: bool) -> None:
        errors = []
        for actor in reversed(self.actors):
            try:
                if not self.rpc.call("simDestroyObject", actor):
                    errors.append(actor)
            except RuntimeError:
                errors.append(actor)
            except OSError:
                errors.append(actor)
                break
        try:
            self.rpc.call("simPause", was_paused)
        except (RuntimeError, OSError):
            errors.append("暂停状态")
        if errors:
            raise RuntimeError(f"AirSim 场景清理未完全确认：{', '.join(errors)}")


@contextmanager
def _world(endpoint: AirSimEndpoint, cancelled: Callable[[], bool]) -> Iterator[_World]:
    with AirSimRPC("127.0.0.1", endpoint.port) as rpc:
        if rpc.call("ping") is not True:
            raise ConnectionError("AirSim 未确认连接")
        cube = next((name for name in rpc.call("simListAssets") if name.casefold() == "cube"), None)
        if cube is None:
            raise ValueError("AirSim 场景缺少 Cube 资产")
        paused = rpc.call("simIsPaused")
        world = _World(rpc, cube, cancelled)
        try:
            rpc.call("simPause", True)
            yield world
        except BaseException as error:
            try:
                world.cleanup(paused)
            except Exception as cleanup_error:
                logging.getLogger(__name__).exception("AirSim scene cleanup failed")
                raise RuntimeError(f"{error or type(error).__name__}；{cleanup_error}") from cleanup_error
            raise
        else:
            world.cleanup(paused)


def inspect_airsim(endpoint: AirSimEndpoint) -> dict:
    """Check a dedicated instance without pausing it or creating scene objects."""
    with AirSimRPC("127.0.0.1", endpoint.port) as rpc:
        if rpc.call("ping") is not True:
            raise ConnectionError("AirSim 未确认连接")
        server_version = rpc.call("getServerVersion")
        cube = next((name for name in rpc.call("simListAssets") if name.casefold() == "cube"), None)
        if cube is None:
            raise ValueError("AirSim 场景缺少 Cube 资产")
        remaining = rpc.call("simListSceneObjects", "GuardWorkbench_.*")
        if remaining:
            raise RuntimeError(f"专用实例仍有工作台对象：{', '.join(remaining)}")
        camera = _xyz(rpc.call("simGetCameraInfo", _CAMERA, "", True)["pose"]["position"])
        if _target_pixels(_image(rpc, 5)[0]):
            raise ValueError("场景已使用目标分割颜色，请清理专用 Blocks 实例")
        image, stamp = _image(rpc, 0)
        return {"server_version": server_version, "camera": _CAMERA,
                "camera_position": camera, "image_timestamp_ns": stamp, "frame_png": image}


def run_airsim(scene: SceneConfig, endpoint: AirSimEndpoint, *,
               cancelled: Callable[[], bool], on_progress: Callable[[int], None],
               on_camera_frame: Callable[[bytes, int], None] | None = None) -> dict:
    if cancelled():
        raise SimulationCancelled()
    n = math.floor(scene.duration_s * RATE_HZ) + 1
    delay_ticks = math.ceil(scene.observation_delay_s * RATE_HZ)
    blackout_start = (scene.duration_s - scene.observation_blackout_s) / 2
    blackout_end = blackout_start + scene.observation_blackout_s
    sigma = scene.measurement_noise_m / scene.illumination_gain
    estimator = DelayedCV(n, sigma)
    rng = Random(scene.seed)
    pedestrian_phase = rng.uniform(0, 68)
    vehicle_phase = rng.uniform(0, 32)
    samples = []
    captured: dict[int, tuple[float, float]] = {}
    last_capture_time = 0.0
    error_sum = nll_sum = 0.0
    covered = 0
    last_progress = -1
    last_rgb: bytes | None = None

    with _world(endpoint, cancelled) as world:
        rpc = world.rpc
        server_version = rpc.call("getServerVersion")
        camera = _xyz(rpc.call("simGetCameraInfo", _CAMERA, "", True)["pose"]["position"])
        if _target_pixels(_image(rpc, 5)[0]):
            raise ValueError("场景已使用目标分割颜色，请使用干净的专用 Blocks 实例")
        target = world.spawn("target", 0, 0, (0.5, 0.4, 1.7))
        world.spawn("building", 8, 6, (4.6, 8.6, 30))
        pedestrians = [world.spawn(f"person_{index}", -1.5, -1.5, (0.5, 0.4, 1.7))
                       for index in range(scene.pedestrian_count)]
        vehicle = world.spawn("vehicle", 0, -4, (4.5, 1.8, 1.6)) if scene.vehicle_count else None
        obstacles = [world.spawn(f"obstacle_{index}", item.north_m, -150 - index,
                                 (item.length_m, item.width_m, item.height_m))
                     for index, item in enumerate(scene.obstacles)]
        previous_activity = [False] * len(obstacles)
        for tick in range(n):
            if cancelled():
                raise SimulationCancelled()
            elapsed = tick / RATE_HZ
            planned = target_position(elapsed * scene.target_speed_mps, scene.target_route_direction)
            world.move(target, *planned, 1.7)
            for index, actor in enumerate(pedestrians):
                # Walk along the perimeter of the original 19 x 15 m sidewalk.
                along = (pedestrian_phase + 17 * index + elapsed * 0.72) % 68
                north, east = _sidewalk(along)
                world.move(actor, north, east, 1.7)
            if vehicle:
                along = (vehicle_phase + elapsed * 2.5) % 32
                world.move(vehicle, min(along, 32 - along), -4, 1.6)
            for index, (actor, item) in enumerate(zip(obstacles, scene.obstacles)):
                active = item.is_active(elapsed)
                if active != previous_activity[index]:
                    east = item.east_m if active else -150 - index
                    world.move(actor, item.north_m, east, item.height_m)
                    expected = _xyz(_pose(item.north_m, east, item.height_m)["position"])
                    if math.dist(world.position(actor), expected) > 0.05:
                        raise RuntimeError(f"AirSim 障碍物 {index + 1} 回读位置与配置不一致")
                    previous_activity[index] = active

            rpc.call("simContinueForTime", 1 / RATE_HZ)
            actual = world.position(target)
            north, east = actual[0] - _ORIGIN_NORTH, actual[1] - _ORIGIN_EAST
            if math.dist((north, east), planned) > 0.05:
                raise RuntimeError("AirSim 目标回读位置与路线偏差超过 0.05 m")
            mask, stamp = _image(rpc, 5)
            pixels = _target_pixels(mask)
            visible = pixels > 0
            usable = pixels * scene.illumination_gain >= 4 and not blackout_start <= elapsed < blackout_end
            if usable:
                captured[tick] = (north + rng.gauss(0, sigma), east + rng.gauss(0, sigma))
            observation = captured.pop(tick - delay_ticks, None)
            packets = [] if observation is None else [(tick - delay_ticks, observation)]
            mean, covariance = estimator.step(packets)
            if packets:
                last_capture_time = max(capture for capture, _ in packets) / RATE_HZ
            error = mean[:2] - (north, east)
            squared_error = float(error @ error)
            is_covered, nll, _ = quality(error, covariance)
            covered += is_covered
            error_sum += squared_error
            nll_sum += nll
            sample = {"time_s": elapsed, "image_timestamp_ns": stamp,
                      "truth_north_m": north, "truth_east_m": east,
                      "estimate_north_m": float(mean[0]), "estimate_east_m": float(mean[1]),
                      "error_m": math.sqrt(squared_error), "uncertainty_area_m2": ellipse_area(covariance),
                      "target_pixels": pixels, "active_obstacles": sum(previous_activity),
                      "visible": visible, "camera_distance_m": math.dist(camera, actual),
                      "observed_north_m": observation[0] if observation else None,
                      "observed_east_m": observation[1] if observation else None,
                      "received_capture_s": (tick - delay_ticks) / RATE_HZ if observation else None}
            sample.update(estimate_record(mean, covariance, elapsed, last_capture_time))
            samples.append(sample)
            progress = (tick + 1) * 100 // n
            if progress != last_progress:
                on_progress(progress)
                last_progress = progress
            if on_camera_frame is not None and (tick % RATE_HZ == 0 or tick == n - 1):
                last_rgb, rgb_stamp = _image(rpc, 0)
                on_camera_frame(last_rgb, rgb_stamp)

        if last_rgb is None:
            image, _ = _image(rpc, 0)
        else:
            image = last_rgb

    return {"summary": {"samples": n, "server_version": server_version,
                        "visibility_fraction": sum(row["visible"] for row in samples) / n,
                        "received_observations": sum(row["received_capture_s"] is not None for row in samples),
                        "mean_camera_distance_m": sum(row["camera_distance_m"] for row in samples) / n,
                        "rmse_m": math.sqrt(error_sum / n), "coverage_95": covered / n,
                        "mean_nll": nll_sum / n, "oosm_updates": estimator.oosm_updates,
                        "estimator_version": ESTIMATOR_VERSION},
            "samples": samples, "last_frame_png": b64encode(image).decode("ascii")}


def _sidewalk(distance: float) -> tuple[float, float]:
    if distance < 19:
        return distance - 1.5, -1.5
    if distance < 34:
        return 17.5, distance - 20.5
    if distance < 53:
        return 51.5 - distance, 13.5
    return -1.5, 66.5 - distance
