"""Delayed CV estimation, uncertainty forecasts, and geometric risk primitives.

State order is [north, east, north_velocity, east_velocity]. Positions use
metres, velocities use m/s, and time uses seconds. The replay algorithm and
numerical conventions follow the frozen E1 research implementation.
"""

from collections.abc import Iterable, Sequence
import math

import numpy as np
from numpy.typing import ArrayLike, NDArray

from .route import URBAN_ROUTE


ESTIMATOR_VERSION = "DelayedCV-E1-v1"
RATE_HZ = 5
FILTER_DT = 1 / RATE_HZ
FILTER_Q = 0.02
CHI_SQUARE_95 = 5.991464547107979
CHI_SQUARE_GEOMETRY = 5.991464547
PREDICTION_HORIZONS_S = (1.0, 2.0, 4.0)
H = np.array([[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]])
INITIAL_MEAN = np.array([0.0, 0.0, 0.4, 0.0])
INITIAL_COVARIANCE = np.diag([0.25, 0.25, 0.04, 0.04])


def matrices(dt: float, q: float = FILTER_Q) -> tuple[NDArray, NDArray]:
    """Return the CV transition and discretized white-acceleration covariance."""
    if not math.isfinite(dt) or dt < 0 or not math.isfinite(q) or q <= 0:
        raise ValueError("dt 必须是非负有限秒数，q 必须是正有限数")
    transition = np.eye(4)
    transition[0, 2] = transition[1, 3] = dt
    covariance = np.zeros((4, 4))
    block = q * np.array([[dt**3 / 3, dt**2 / 2], [dt**2 / 2, dt]])
    for indices in ([0, 2], [1, 3]):
        covariance[np.ix_(indices, indices)] = block
    return transition, covariance


def update(mean: ArrayLike, covariance: ArrayLike, measurement: ArrayLike,
           measurement_covariance: ArrayLike) -> tuple[NDArray, NDArray]:
    """Apply a position-only Kalman update using the Joseph covariance form."""
    mu = np.asarray(mean, dtype=float)
    cov = np.asarray(covariance, dtype=float)
    observed = np.asarray(measurement, dtype=float)
    noise = np.asarray(measurement_covariance, dtype=float)
    innovation_covariance = cov[:2, :2] + noise
    determinant = np.linalg.det(innovation_covariance)
    if not math.isfinite(determinant) or determinant <= 0:
        raise ValueError("观测更新的新息协方差必须正定")
    inverse = np.array([[innovation_covariance[1, 1], -innovation_covariance[0, 1]],
                        [-innovation_covariance[1, 0], innovation_covariance[0, 0]]]) / determinant
    gain = cov[:, :2] @ inverse
    updated_mean = mu + gain @ (observed - mu[:2])
    residual = np.eye(4) - gain @ H
    updated_covariance = residual @ cov @ residual.T + gain @ noise @ gain.T
    return updated_mean, (updated_covariance + updated_covariance.T) / 2


class DelayedCV:
    """Replay arrived position packets at their original 5 Hz capture slots."""

    def __init__(self, n: int, measurement_sigma_m: float) -> None:
        if type(n) is not int or n < 1:
            raise ValueError("估计器样本数必须是正整数")
        if not math.isfinite(measurement_sigma_m) or measurement_sigma_m <= 0:
            raise ValueError("观测噪声标准差必须是正有限数")
        self.n = n
        self.measurement_covariance = np.eye(2) * measurement_sigma_m**2
        self.means = np.zeros((n, 4))
        self.covariances = np.zeros((n, 4, 4))
        self.observations: dict[int, NDArray] = {}
        self.index = -1
        self.oosm_updates = 0

    def step(self, packets: Iterable[tuple[int, ArrayLike]]) -> tuple[NDArray, NDArray]:
        """Advance one slot and replay from the earliest newly arrived packet."""
        arrived = list(packets)
        self.index += 1
        index = self.index
        if index >= self.n:
            raise IndexError("估计器已超过预分配样本数")
        if index:
            transition, process = matrices(FILTER_DT)
            self.means[index] = transition @ self.means[index - 1]
            self.covariances[index] = (transition @ self.covariances[index - 1]
                                       @ transition.T + process)
        else:
            self.means[index] = INITIAL_MEAN
            self.covariances[index] = INITIAL_COVARIANCE

        newest_existing = max(self.observations, default=-1)
        for capture, measurement in arrived:
            if type(capture) is not int or not 0 <= capture <= index:
                raise ValueError("观测采集槽位必须位于已发生的仿真时间内")
            if capture in self.observations:
                raise ValueError(f"重复接收采集槽位 {capture} 的观测")
            values = np.asarray(measurement, dtype=float)
            if values.shape != (2,) or not np.isfinite(values).all():
                raise ValueError("位置观测必须包含两个有限米制坐标")
            self.observations[capture] = values.copy()
        if arrived:
            earliest = min(capture for capture, _ in arrived)
            self.oosm_updates += earliest < newest_existing
            for slot in range(earliest, index + 1):
                if slot:
                    transition, process = matrices(FILTER_DT)
                    mean = transition @ self.means[slot - 1]
                    covariance = (transition @ self.covariances[slot - 1]
                                  @ transition.T + process)
                else:
                    mean, covariance = INITIAL_MEAN.copy(), INITIAL_COVARIANCE.copy()
                if slot in self.observations:
                    mean, covariance = update(mean, covariance, self.observations[slot],
                                              self.measurement_covariance)
                self.means[slot] = mean
                self.covariances[slot] = covariance
        return self.means[index].copy(), self.covariances[index].copy()


def forecast(mean: ArrayLike, covariance: ArrayLike, horizon_s: float,
             q: float = FILTER_Q) -> tuple[NDArray, NDArray]:
    """Predict independently from one current posterior, never from another horizon."""
    transition, process = matrices(horizon_s, q)
    mu = np.asarray(mean, dtype=float)
    cov = np.asarray(covariance, dtype=float)
    return transition @ mu, transition @ cov @ transition.T + process


def ellipse_area(covariance: ArrayLike) -> float:
    """Return the 95% position-ellipse area in m² from a 4x4 or 2x2 covariance."""
    cov = np.asarray(covariance, dtype=float)
    position = cov[:2, :2]
    determinant = float(np.linalg.det(position))
    if determinant <= 0 or not math.isfinite(determinant):
        raise ValueError("位置协方差必须具有正有限行列式")
    return math.pi * CHI_SQUARE_GEOMETRY * math.sqrt(determinant)


def quality(error: ArrayLike, covariance: ArrayLike) -> tuple[bool, float, float]:
    """Return 95% coverage, Gaussian NLL, and Mahalanobis distance squared."""
    position_error = np.asarray(error, dtype=float)
    position = np.asarray(covariance, dtype=float)[:2, :2]
    determinant = float(np.linalg.det(position))
    if determinant <= 0 or not math.isfinite(determinant):
        raise ValueError("位置协方差必须具有正有限行列式")
    distance_squared = float((position[1, 1] * position_error[0]**2
                              + position[0, 0] * position_error[1]**2
                              - 2 * position[0, 1] * position_error[0] * position_error[1]) / determinant)
    nll = 0.5 * (2 * math.log(2 * math.pi) + math.log(determinant) + distance_squared)
    return distance_squared <= CHI_SQUARE_95, nll, distance_squared


def project_road(point: ArrayLike, road_corners: ArrayLike = URBAN_ROUTE) -> NDArray:
    """Project a planar metre position onto the closest cyclically ordered road edge."""
    corners = np.asarray(road_corners, dtype=float)
    position = np.asarray(point, dtype=float)
    if corners.ndim != 2 or corners.shape[1] != 2 or len(corners) < 2:
        raise ValueError("道路角点必须是 N×2 的平面坐标")
    starts = corners
    ends = np.roll(corners, -1, axis=0)
    directions = ends - starts
    lengths_squared = np.sum(directions * directions, axis=1)
    if np.any(lengths_squared <= 0):
        raise ValueError("道路相邻角点不能重合")
    ratios = np.clip(np.sum((position - starts) * directions, axis=1) / lengths_squared, 0.0, 1.0)
    candidates = starts + ratios[:, None] * directions
    return candidates[np.argmin(np.sum((candidates - position)**2, axis=1))]


def ellipse_contour(center: ArrayLike, covariance: ArrayLike,
                    segments: int = 80) -> NDArray:
    """Return a closed 95% position-ellipse contour for trajectory rendering."""
    if type(segments) is not int or segments < 12:
        raise ValueError("椭圆轮廓至少需要 12 个分段")
    values, vectors = np.linalg.eigh(np.asarray(covariance, dtype=float)[:2, :2])
    axes = vectors @ np.diag(np.sqrt(CHI_SQUARE_GEOMETRY * np.maximum(values, 1e-12)))
    angles = np.linspace(0, 2 * math.pi, segments + 1)
    circle = np.vstack((np.cos(angles), np.sin(angles)))
    return (np.asarray(center, dtype=float)[:2, None] + axes @ circle).T


def projected_ellipse_points(center: ArrayLike, covariance: ArrayLike,
                             road_corners: ArrayLike = URBAN_ROUTE) -> NDArray:
    """Project one predicted center and its four 95% principal-axis endpoints."""
    values, vectors = np.linalg.eigh(np.asarray(covariance, dtype=float)[:2, :2])
    axes = vectors @ np.diag(np.sqrt(CHI_SQUARE_GEOMETRY * np.maximum(values, 1e-12)))
    midpoint = np.asarray(center, dtype=float)[:2]
    raw = (midpoint, midpoint + axes[:, 0], midpoint - axes[:, 0],
           midpoint + axes[:, 1], midpoint - axes[:, 1])
    return np.array([project_road(point, road_corners) for point in raw])


def representative_points(mean: ArrayLike, covariance: ArrayLike, horizon_s: float = 1.0,
                          q: float = FILTER_Q, road_corners: ArrayLike = URBAN_ROUTE) -> NDArray:
    """Return the predicted center and four road-projected 95% principal-axis endpoints."""
    predicted_mean, predicted_covariance = forecast(mean, covariance, horizon_s, q)
    return projected_ellipse_points(predicted_mean, predicted_covariance, road_corners)


def rays_blocked(starts: ArrayLike, ends: ArrayLike,
                 obstacle_boxes: Iterable[tuple[ArrayLike, ArrayLike]]) -> NDArray:
    """Test 3-D segments against axis-aligned boxes using slab intersection."""
    origins = np.atleast_2d(np.asarray(starts, dtype=float))
    targets = np.broadcast_to(np.asarray(ends, dtype=float), origins.shape)
    direction = targets - origins
    parallel = np.abs(direction) < 1e-12
    safe = np.where(parallel, 1.0, direction)
    blocked = np.zeros(len(origins), dtype=bool)
    for lower_bound, upper_bound in obstacle_boxes:
        lower = np.asarray(lower_bound, dtype=float)
        upper = np.asarray(upper_bound, dtype=float)
        first = (lower - origins) / safe
        second = (upper - origins) / safe
        low = np.where(parallel, -np.inf, np.minimum(first, second))
        high = np.where(parallel, np.inf, np.maximum(first, second))
        outside = np.any(parallel & ((origins < lower) | (origins > upper)), axis=1)
        blocked |= (~outside & (np.max(low, axis=1) <= np.minimum(np.min(high, axis=1), 1.0))
                    & (np.min(high, axis=1) >= 0.0))
    return blocked


def range_risk_components(grid: ArrayLike, points: ArrayLike,
                          obstacle_boxes: Iterable[tuple[ArrayLike, ArrayLike]] = (),
                          target_z: float = 1.7, minimum_distance_m: float = 6.0,
                          maximum_distance_m: float = 18.0) -> tuple[NDArray, NDArray]:
    """Return separate averaged occlusion and guard-band distance costs."""
    candidates = np.atleast_2d(np.asarray(grid, dtype=float))
    representatives = np.atleast_2d(np.asarray(points, dtype=float))
    if candidates.shape[1] != 3 or representatives.shape[1] != 2 or not len(representatives):
        raise ValueError("风险输入应为 N×3 候选位置和至少一个 M×2 代表位置")
    if not 0 < minimum_distance_m < maximum_distance_m:
        raise ValueError("守卫带距离必须满足 0 < 最小距离 < 最大距离")
    boxes = tuple(obstacle_boxes)
    occlusion = np.zeros(len(candidates))
    distance_cost = np.zeros(len(candidates))
    normalization = maximum_distance_m - minimum_distance_m
    for point in representatives:
        distance = np.linalg.norm(candidates[:, :2] - point, axis=1)
        excess = (np.maximum(minimum_distance_m - distance, 0)
                  + np.maximum(distance - maximum_distance_m, 0))
        occlusion += rays_blocked(candidates, np.r_[point, target_z], boxes).astype(float) / len(representatives)
        distance_cost += excess / normalization / len(representatives)
    return occlusion, distance_cost


def range_risk(grid: ArrayLike, points: ArrayLike,
               obstacle_boxes: Iterable[tuple[ArrayLike, ArrayLike]] = (),
               target_z: float = 1.7, minimum_distance_m: float = 6.0,
               maximum_distance_m: float = 18.0) -> NDArray:
    """Evaluate averaged occlusion and guard-band excess for candidates."""
    occlusion, distance = range_risk_components(grid, points, obstacle_boxes, target_z,
                                                minimum_distance_m, maximum_distance_m)
    return occlusion + distance


def state_risk(mean: ArrayLike, covariance: ArrayLike, position: ArrayLike,
               velocity_command: ArrayLike, *, obstacle_boxes: Iterable[tuple[ArrayLike, ArrayLike]] = (),
               uav_z: float = 50.0, target_z: float = 1.7, q: float = FILTER_Q,
               covariance_mode: str = "adaptive", fixed_covariance_diagonal: Sequence[float] | None = None,
               road_corners: ArrayLike = URBAN_ROUTE, minimum_distance_m: float = 6.0,
               maximum_distance_m: float = 18.0) -> float:
    """Evaluate one explicit one-second UAV proposal without executing a controller."""
    risk_covariance = np.asarray(covariance, dtype=float)
    if covariance_mode == "fixed":
        if fixed_covariance_diagonal is None:
            raise ValueError("固定协方差模式必须提供四个对角元素")
        diagonal = np.asarray(fixed_covariance_diagonal, dtype=float)
        if diagonal.shape != (4,) or not np.isfinite(diagonal).all() or np.any(diagonal <= 0):
            raise ValueError("固定协方差对角元素必须是四个正有限数")
        risk_covariance = np.diag(diagonal)
    elif covariance_mode != "adaptive":
        raise ValueError("covariance_mode 必须为 adaptive 或 fixed")
    proposal = np.asarray(position, dtype=float)[:2] + np.asarray(velocity_command, dtype=float)
    future_position = np.r_[proposal, uav_z][None, :]
    points = representative_points(mean, risk_covariance, 1.0, q, road_corners)
    return float(range_risk(future_position, points, obstacle_boxes, target_z,
                            minimum_distance_m, maximum_distance_m)[0])


def estimate_record(mean: ArrayLike, covariance: ArrayLike, elapsed_s: float,
                    last_capture_s: float) -> dict[str, float]:
    """Serialize the current belief and independent 1/2/4 s position forecasts."""
    mu = np.asarray(mean, dtype=float)
    cov = np.asarray(covariance, dtype=float)
    record = {
        "velocity_north_mps": float(mu[2]),
        "velocity_east_mps": float(mu[3]),
        "covariance_nn_m2": float(cov[0, 0]),
        "covariance_ne_m2": float(cov[0, 1]),
        "covariance_ee_m2": float(cov[1, 1]),
        "observation_age_s": float(elapsed_s - last_capture_s),
    }
    for horizon in PREDICTION_HORIZONS_S:
        predicted_mean, predicted_covariance = forecast(mu, cov, horizon)
        prefix = f"prediction_{horizon:g}s"
        record.update({
            f"{prefix}_north_m": float(predicted_mean[0]),
            f"{prefix}_east_m": float(predicted_mean[1]),
            f"{prefix}_cov_nn_m2": float(predicted_covariance[0, 0]),
            f"{prefix}_cov_ne_m2": float(predicted_covariance[0, 1]),
            f"{prefix}_cov_ee_m2": float(predicted_covariance[1, 1]),
            f"{prefix}_area_m2": ellipse_area(predicted_covariance),
        })
    return record
