"""Reproducible delayed-CV target estimation with synthetic observations."""

from collections.abc import Callable
from dataclasses import dataclass
import math
from random import Random

from .route import target_position
from .scene import SceneConfig
from .uncertainty import (
    ESTIMATOR_VERSION, RATE_HZ, DelayedCV, ellipse_area, estimate_record, quality,
)


OFFLINE_BACKEND = "offline-cv-v1"


class SimulationCancelled(Exception):
    """Raised when a simulation receives a cancellation request."""


@dataclass(frozen=True, slots=True)
class Sample:
    time_s: float
    truth_north_m: float
    truth_east_m: float
    estimate_north_m: float
    estimate_east_m: float
    error_m: float
    uncertainty_area_m2: float
    received_capture_s: float | None
    velocity_north_mps: float
    velocity_east_mps: float
    covariance_nn_m2: float
    covariance_ne_m2: float
    covariance_ee_m2: float
    observation_age_s: float
    prediction_1s_north_m: float
    prediction_1s_east_m: float
    prediction_1s_cov_nn_m2: float
    prediction_1s_cov_ne_m2: float
    prediction_1s_cov_ee_m2: float
    prediction_1s_area_m2: float
    prediction_2s_north_m: float
    prediction_2s_east_m: float
    prediction_2s_cov_nn_m2: float
    prediction_2s_cov_ne_m2: float
    prediction_2s_cov_ee_m2: float
    prediction_2s_area_m2: float
    prediction_4s_north_m: float
    prediction_4s_east_m: float
    prediction_4s_cov_nn_m2: float
    prediction_4s_cov_ne_m2: float
    prediction_4s_cov_ee_m2: float
    prediction_4s_area_m2: float


@dataclass(frozen=True, slots=True)
class RunSummary:
    samples: int
    received_observations: int
    blackout_samples: int
    rmse_m: float
    coverage_95: float
    mean_nll: float
    oosm_updates: int
    estimator_version: str


@dataclass(frozen=True, slots=True)
class OfflineResult:
    summary: RunSummary
    samples: tuple[Sample, ...]


def run_offline(
    scene: SceneConfig,
    *,
    cancelled: Callable[[], bool] = lambda: False,
    on_progress: Callable[[int], None] = lambda percent: None,
) -> OfflineResult:
    """Apply only released observations at their original capture slots."""
    n = math.floor(scene.duration_s * RATE_HZ) + 1
    delay_steps = math.ceil(scene.observation_delay_s * RATE_HZ)
    sigma = scene.measurement_noise_m / scene.illumination_gain
    blackout_start = (scene.duration_s - scene.observation_blackout_s) / 2
    blackout_end = blackout_start + scene.observation_blackout_s
    noise = Random(scene.seed)
    estimator = DelayedCV(n, sigma)
    pending: list[tuple[float, float] | None] = []
    samples = []
    received = blocked = covered = 0
    error_sum = nll_sum = 0.0
    last_capture_time = 0.0
    progress = -1

    for tick in range(n):
        if cancelled():
            raise SimulationCancelled()
        elapsed = tick / RATE_HZ
        truth = target_position(elapsed * scene.target_speed_mps, scene.target_route_direction)
        measured = (truth[0] + noise.gauss(0, sigma), truth[1] + noise.gauss(0, sigma))
        in_blackout = blackout_start <= elapsed < blackout_end
        pending.append(None if in_blackout else measured)
        blocked += in_blackout

        capture_index = tick - delay_steps
        packets = []
        received_at = None
        if capture_index >= 0 and (observation := pending[capture_index]) is not None:
            received_at = capture_index / RATE_HZ
            packets.append((capture_index, observation))
            received += 1
        mean, covariance = estimator.step(packets)
        if packets:
            last_capture_time = max(capture for capture, _ in packets) / RATE_HZ

        north, east = mean[:2]
        error = mean[:2] - truth
        squared_error = float(error @ error)
        is_covered, nll, _ = quality(error, covariance)
        covered += is_covered
        error_sum += squared_error
        nll_sum += nll
        area = ellipse_area(covariance)
        samples.append(Sample(elapsed, *truth, float(north), float(east),
                              math.sqrt(squared_error), area, received_at,
                              **estimate_record(mean, covariance, elapsed, last_capture_time)))
        percent = (tick + 1) * 100 // n
        if percent != progress:
            progress = percent
            on_progress(progress)

    summary = RunSummary(n, received, blocked, math.sqrt(error_sum / n), covered / n,
                         nll_sum / n, estimator.oosm_updates, ESTIMATOR_VERSION)
    return OfflineResult(summary, tuple(samples))
