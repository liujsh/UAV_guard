"""GuardDrone lifecycle, initialization gates, and fixed-height AirSim pulses."""

from collections.abc import Callable
from dataclasses import dataclass
import math

import numpy as np

from .airsim_rpc import AirSimRPC
from .offline import SimulationCancelled
from .scene import SceneConfig
from .uncertainty import RATE_HZ


VEHICLE_NAME = "GuardDrone"
GUARD_HEIGHT_M = 50.0
ORIGIN_NORTH_EAST_GROUND = (-30.0, -60.0, 1.02)
START_NORTH_EAST = np.array([-2.0, -10.0])
UAV_Z_NED_M = ORIGIN_NORTH_EAST_GROUND[2] - GUARD_HEIGHT_M
TARGET_Z_NED_M = 0.17000000000000004
FLIGHT_BOUNDS = np.array([[-18.0, 34.0], [-20.0, 32.0]])
_YAW_HOLD = {"is_rate": False, "yaw_or_rate": 0.0}


def _vector(x: float, y: float, z: float) -> dict[str, float]:
    return {"x_val": x, "y_val": y, "z_val": z}


def _coordinates(value: dict) -> tuple[float, float, float]:
    coordinates = (float(value["x_val"]), float(value["y_val"]), float(value["z_val"]))
    if not all(math.isfinite(coordinate) for coordinate in coordinates):
        raise ValueError("GuardDrone 返回了无效坐标")
    return coordinates


@dataclass(frozen=True, slots=True)
class DroneState:
    timestamp_ns: int
    north_m: float
    east_m: float
    height_m: float
    velocity_north_mps: float
    velocity_east_mps: float
    velocity_down_mps: float
    z_ned_m: float

    @property
    def position(self) -> np.ndarray:
        return np.array([self.north_m, self.east_m, self.z_ned_m])

    @property
    def velocity(self) -> np.ndarray:
        return np.array([self.velocity_north_mps, self.velocity_east_mps,
                         self.velocity_down_mps])


class GuardDrone:
    """Own the dedicated vehicle's API control, wind, and safety cleanup."""

    def __init__(self, rpc: AirSimRPC, scene: SceneConfig,
                 cancelled: Callable[[], bool] = lambda: False) -> None:
        self.rpc = rpc
        self.scene = scene
        self.cancelled = cancelled
        self.active = False
        self.initialized = False
        self.initialization_controls = 0
        self.initial_collision_stamp: int | None = None

    def start(self) -> DroneState:
        """Take off and pass the 50 m / common-start physical readiness gate."""
        self.active = True
        if self.cancelled():
            raise SimulationCancelled()
        origin_north, origin_east, ground = ORIGIN_NORTH_EAST_GROUND
        pose = {"position": _vector(origin_north + START_NORTH_EAST[0],
                                    origin_east + START_NORTH_EAST[1], ground),
                "orientation": {"w_val": 1.0, "x_val": 0.0, "y_val": 0.0, "z_val": 0.0}}
        if not self.rpc.call("simSetVehiclePose", pose, True, VEHICLE_NAME):
            raise RuntimeError("GuardDrone 初始位姿设置失败")
        self.rpc.call("simSetWind", _vector(0.0, 0.0, 0.0))
        self.rpc.call("enableApiControl", True, VEHICLE_NAME)
        if not self.rpc.call("armDisarm", True, VEHICLE_NAME):
            raise RuntimeError("GuardDrone 解锁失败")
        self.rpc.call("simPause", True)
        collision = self.rpc.call("simGetCollisionInfo", VEHICLE_NAME)
        self.initial_collision_stamp = int(collision.get("time_stamp", 0)) if collision.get("has_collided") else 0
        self.rpc.call("simPause", False)
        self.rpc.call("takeoff", 20.0, VEHICLE_NAME, timeout=30)
        self.rpc.call("simPause", True)

        for _ in range(180):
            if self.cancelled():
                raise SimulationCancelled()
            state = self.state()
            if (np.linalg.norm(state.position - np.r_[START_NORTH_EAST, UAV_Z_NED_M]) < 0.4
                    and np.linalg.norm(state.velocity) < 0.5):
                self.initialized = True
                break
            horizontal = np.clip(0.6 * (START_NORTH_EAST - state.position[:2])
                                 - 0.6 * state.velocity[:2], -2.0, 2.0)
            if abs(state.z_ned_m - UAV_Z_NED_M) >= 0.5:
                horizontal[:] = 0.0
            self.command(*horizontal)
            self.initialization_controls += 1
        else:
            raise RuntimeError("GuardDrone 未通过 50 m 高度与初始位置门槛")
        self.rpc.call("simSetWind", _vector(self.scene.wind_north_mps, self.scene.wind_east_mps,
                                            self.scene.wind_down_mps))
        return self.state()

    def state(self) -> DroneState:
        response = self.rpc.call("getMultirotorState", VEHICLE_NAME)
        kinematics = response["kinematics_estimated"]
        x, y, z = _coordinates(kinematics["position"])
        vx, vy, vz = _coordinates(kinematics["linear_velocity"])
        origin_north, origin_east, ground = ORIGIN_NORTH_EAST_GROUND
        return DroneState(int(response["timestamp"]), x - origin_north, y - origin_east,
                          ground - z, vx, vy, vz, z)

    def command(self, velocity_north_mps: float, velocity_east_mps: float) -> DroneState:
        """Advance one 5 Hz physics pulse while holding the 50 m height setpoint."""
        if not self.active:
            raise RuntimeError("GuardDrone 尚未初始化")
        if not all(math.isfinite(value) for value in (velocity_north_mps, velocity_east_mps)):
            raise ValueError("GuardDrone 速度指令必须是有限数")
        self.rpc.call("simPause", False)
        try:
            self.rpc.call("moveByVelocityZ", velocity_north_mps, velocity_east_mps,
                          UAV_Z_NED_M, 1 / RATE_HZ, 0, _YAW_HOLD, VEHICLE_NAME, timeout=5)
        finally:
            self.rpc.call("simPause", True)
        state = self.state()
        collision = self.rpc.call("simGetCollisionInfo", VEHICLE_NAME)
        stamp = int(collision.get("time_stamp", 0))
        if collision.get("has_collided") and stamp > (self.initial_collision_stamp or 0):
            raise RuntimeError(f"GuardDrone 发生碰撞：{collision.get('object_name') or '未知对象'}")
        if self.initialized:
            position = state.position
            if (np.any(position[:2] < FLIGHT_BOUNDS[:, 0] - 2)
                    or np.any(position[:2] > FLIGHT_BOUNDS[:, 1] + 2)
                    or not ORIGIN_NORTH_EAST_GROUND[2] - 57 <= state.z_ned_m <= ORIGIN_NORTH_EAST_GROUND[2] - 43):
                raise RuntimeError("GuardDrone 超出允许的物理飞行包线")
        return state

    def stop(self) -> None:
        """Stop, land, disarm, release API control, and clear this task's wind."""
        if not self.active:
            return
        errors = []
        try:
            z = self.state().z_ned_m
        except (KeyError, OSError, RuntimeError, TypeError, ValueError):
            z = UAV_Z_NED_M
            errors.append("最终位姿读取")
        operations = (
            ("simPause", (False,), {}),
            ("cancelLastTask", (VEHICLE_NAME,), {}),
            ("moveByVelocityZ", (0.0, 0.0, z, 0.2, 0, _YAW_HOLD, VEHICLE_NAME), {"timeout": 5}),
            ("land", (60.0, VEHICLE_NAME), {"timeout": 70}),
            ("armDisarm", (False, VEHICLE_NAME), {}),
            ("enableApiControl", (False, VEHICLE_NAME), {}),
            ("simSetWind", (_vector(0.0, 0.0, 0.0),), {}),
        )
        for method, args, kwargs in operations:
            try:
                self.rpc.call(method, *args, **kwargs)
            except (OSError, RuntimeError) as error:
                errors.append(f"{method}: {error}")
        self.active = False
        self.initialized = False
        if errors:
            raise RuntimeError("GuardDrone 安全停止未完全确认：" + "；".join(errors))
