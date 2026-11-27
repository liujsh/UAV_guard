"""Frozen geometric reference control and its 26-dimensional policy input.

All public planar coordinates are relative to the workbench target start. The
third position coordinate is AirSim NED down. The geometric reference can be
used alone or combined with a hash-verified frozen residual actor.
"""

from dataclasses import dataclass
import math

import numpy as np
from numpy.typing import ArrayLike, NDArray

from .flight import (
    DroneState, FLIGHT_BOUNDS, ORIGIN_NORTH_EAST_GROUND, START_NORTH_EAST,
    TARGET_Z_NED_M, UAV_Z_NED_M,
)
from .policy import ResidualPolicy
from .route import URBAN_ROUTE
from .scene import SceneConfig
from .uncertainty import forecast, range_risk_components, rays_blocked, representative_points


CONTROLLER_VERSION = "geometric-reference-v1"
CONTROL_Q = 0.056


def _projected_points(mean: NDArray, covariance: NDArray, q: float) -> NDArray:
    return representative_points(mean, covariance, 1.0, q, URBAN_ROUTE)


def choose_goal(mean: ArrayLike, covariance: ArrayLike, position: ArrayLike,
                previous_goal: ArrayLike, obstacle_boxes=(), *, q: float = CONTROL_Q,
                minimum_distance_m: float = 6.0,
                maximum_distance_m: float = 18.0) -> tuple[NDArray, dict]:
    """Choose the deterministic geometric goal used by the frozen controller."""
    if not 0 < minimum_distance_m < maximum_distance_m:
        raise ValueError("守卫带距离必须满足 0 < 最小距离 < 最大距离")
    mu = np.asarray(mean, dtype=float)
    cov = np.asarray(covariance, dtype=float)
    current = np.asarray(position, dtype=float)
    previous = np.asarray(previous_goal, dtype=float)
    points = _projected_points(mu, cov, q)
    north, south = FLIGHT_BOUNDS
    xx, yy = np.meshgrid(np.arange(north[0] + 3, north[1] - 2, 2.0),
                         np.arange(south[0] + 3, south[1] - 2, 2.0))
    grid = np.c_[xx.ravel(), yy.ravel(), np.full(xx.size, UAV_Z_NED_M)]
    delta = points[0] - grid[:, :2]
    distance = np.linalg.norm(delta, axis=1)
    candidate_minimum = minimum_distance_m + 2
    candidate_maximum = maximum_distance_m - 2
    valid = ((distance >= candidate_minimum) & (distance <= candidate_maximum)
             & (np.abs(np.arctan2(delta[:, 1], delta[:, 0])) <= np.deg2rad(150)))
    grid, distance = grid[valid], distance[valid]
    unblocked = ~rays_blocked(grid, np.r_[points[0], TARGET_Z_NED_M], obstacle_boxes)
    grid, distance = grid[unblocked], distance[unblocked]
    if not len(grid):
        fallback = np.clip(points[0] - [candidate_minimum, candidate_minimum],
                           FLIGHT_BOUNDS[:, 0] + 3, FLIGHT_BOUNDS[:, 1] - 3)
        return fallback, {"candidates": 0, "risk": 0.0, "base_cost": 0.0, "score": 0.0}
    base = (0.03 * np.linalg.norm(grid[:, :2] - current[:2], axis=1)
            + 0.1 * np.abs(distance - (minimum_distance_m + maximum_distance_m) / 2)
            + 0.02 * np.linalg.norm(grid[:, :2] - previous, axis=1))
    # The residual-mode geometric target uses goal_weight=0. Risk is diagnostic here.
    selected = int(np.argmin(base))
    risk = range_risk_components(grid, points, obstacle_boxes, TARGET_Z_NED_M,
                                 minimum_distance_m, maximum_distance_m)
    selected_risk = float(sum(component[selected] for component in risk))
    return grid[selected, :2], {"candidates": len(grid), "risk": selected_risk,
                                "base_cost": float(base[selected]), "score": float(base[selected])}


def fence_velocity(proposal: ArrayLike, position: ArrayLike, velocity: ArrayLike,
                   maximum_speed_mps: float) -> NDArray:
    """Apply the frozen per-axis geofence and velocity projection."""
    applied = np.asarray(proposal, dtype=float).copy()
    current = np.asarray(position, dtype=float)
    speed = np.asarray(velocity, dtype=float)
    for axis, (low, high) in enumerate(FLIGHT_BOUNDS):
        lower = 0.35 * (low + 3 - current[axis]) - maximum_speed_mps * speed[axis]
        upper = 0.35 * (high - 3 - current[axis]) - maximum_speed_mps * speed[axis]
        applied[axis] = np.clip(np.clip(applied[axis], lower, upper),
                                -maximum_speed_mps, maximum_speed_mps)
    return applied


@dataclass(frozen=True, slots=True)
class ControlDecision:
    goal: NDArray
    base_proposal: NDArray
    residual_action: NDArray
    proposal: NDArray
    applied: NDArray
    predicted_occlusion_risk: float
    predicted_distance_risk: float
    predicted_state_risk: float
    goal_candidates: int
    goal_base_cost: float
    goal_risk: float
    observation: NDArray


class GeometricController:
    """Track the previous geometric goal and previous residual action."""

    def __init__(self, scene: SceneConfig, policy: ResidualPolicy | None = None) -> None:
        self.scene = scene
        self.policy = policy
        self.version = CONTROLLER_VERSION if policy is None else policy.version
        self.previous_goal = START_NORTH_EAST.copy()
        self.previous_action = np.zeros(2)

    def decide(self, mean: ArrayLike, covariance: ArrayLike, drone: DroneState, *,
               tick: int, total_steps: int, observation_age_s: float,
               measurement_valid: bool, obstacle_boxes=()) -> ControlDecision:
        mu = np.asarray(mean, dtype=float)
        cov = np.asarray(covariance, dtype=float)
        position = drone.position
        goal, detail = choose_goal(mu, cov, position, self.previous_goal, obstacle_boxes,
                                   minimum_distance_m=self.scene.guard_min_distance_m,
                                   maximum_distance_m=self.scene.guard_max_distance_m)
        limit = self.scene.guard_max_speed_mps
        base = np.clip(0.6 * (goal - position[:2]) + 0.8 * mu[2:]
                       - 0.6 * drone.velocity[:2], -limit, limit)
        observation = self._observation(mu, cov, drone, goal, tick=tick,
                                        total_steps=total_steps,
                                        observation_age_s=observation_age_s,
                                        measurement_valid=measurement_valid)
        residual = (np.zeros(2) if self.policy is None
                    else self.policy.predict(observation))
        scale = 0.0 if self.policy is None else self.policy.residual_scale
        proposal = np.clip(base + scale * residual, -limit, limit)
        points = _projected_points(mu, cov, CONTROL_Q)
        future = np.r_[position[:2] + proposal, UAV_Z_NED_M][None, :]
        occlusion, distance = range_risk_components(
            future, points, obstacle_boxes, TARGET_Z_NED_M,
            self.scene.guard_min_distance_m, self.scene.guard_max_distance_m)
        self.previous_goal = goal.copy()
        self.previous_action = residual.copy()
        return ControlDecision(goal, base, residual, proposal,
                               fence_velocity(proposal, position, drone.velocity, limit),
                               float(occlusion[0]), float(distance[0]),
                               float(occlusion[0] + distance[0]), detail["candidates"],
                               detail["base_cost"], detail["risk"], observation)

    def _observation(self, mean: NDArray, covariance: NDArray, drone: DroneState,
                     goal: NDArray, *, tick: int, total_steps: int,
                     observation_age_s: float, measurement_valid: bool) -> NDArray:
        future_mean, future_covariance = forecast(mean, covariance, 1.0, CONTROL_Q)
        indices = (np.array([0, 0, 1]), np.array([0, 1, 1]))
        covariance_features = covariance[indices] / 25
        future_features = future_covariance[indices] / 25
        remaining = 1 - tick / max(total_steps, 1)
        values = np.r_[
            (mean[:2] - drone.position[:2]) / 20,
            mean[2:] / 2,
            drone.velocity[:2] / 2,
            (goal - drone.position[:2]) / 20,
            covariance_features,
            min(observation_age_s, 100) / 10,
            float(measurement_valid),
            remaining,
            self.previous_action,
            (future_mean[:2] - drone.position[:2]) / 20,
            future_features,
            (drone.position[:2] - np.array([0.0, 0.0])) / 30,
            self.scene.illumination_gain,
            self.scene.measurement_noise_m / self.scene.illumination_gain,
            (ORIGIN_NORTH_EAST_GROUND[2] - drone.z_ned_m) / 50,
        ]
        if values.shape != (26,) or not np.isfinite(values).all():
            raise ValueError("策略输入必须包含 26 个有限特征")
        return np.clip(values, -20, 20).astype(np.float32)


def obstacle_boxes(scene: SceneConfig, elapsed_s: float) -> tuple[tuple[NDArray, NDArray], ...]:
    """Return NED boxes for the workbench building and currently active obstacles."""
    ground = ORIGIN_NORTH_EAST_GROUND[2]
    boxes = [(np.array([5.7, 1.7, ground - 30.0]), np.array([10.3, 10.3, ground]))]
    for obstacle in scene.obstacles:
        if obstacle.is_active(elapsed_s):
            boxes.append((
                np.array([obstacle.north_m - obstacle.length_m / 2,
                          obstacle.east_m - obstacle.width_m / 2, ground - obstacle.height_m]),
                np.array([obstacle.north_m + obstacle.length_m / 2,
                          obstacle.east_m + obstacle.width_m / 2, ground]),
            ))
    return tuple(boxes)


def guard_distance(drone: DroneState, target_north_m: float, target_east_m: float) -> float:
    distance = math.hypot(drone.north_m - target_north_m, drone.east_m - target_east_m)
    if not math.isfinite(distance):
        raise ValueError("守卫距离必须是有限数")
    return distance
