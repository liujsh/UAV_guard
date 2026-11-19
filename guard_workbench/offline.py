"""Reproducible target state estimation with synthetic observations."""

from collections.abc import Callable
from dataclasses import dataclass
import math
from random import Random

from .route import target_position
from .scene import SceneConfig


OFFLINE_BACKEND = "offline-cv-v1"
_RATE_HZ = 5
_PROCESS_NOISE = 0.02
_CHI_SQUARE_95 = 5.9914645471


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


@dataclass(frozen=True, slots=True)
class RunSummary:
    samples: int
    received_observations: int
    blackout_samples: int
    rmse_m: float
    coverage_95: float


@dataclass(frozen=True, slots=True)
class OfflineResult:
    summary: RunSummary
    samples: tuple[Sample, ...]


@dataclass(frozen=True, slots=True)
class _Axis:
    """One axis of a constant velocity Kalman filter."""

    position: float = 0.0
    velocity: float = 0.0
    variance: float = 0.25
    covariance: float = 0.0
    velocity_variance: float = 0.04

    def predict(self, elapsed: float) -> "_Axis":
        return _Axis(
            self.position + elapsed * self.velocity,
            self.velocity,
            self.variance + 2 * elapsed * self.covariance + elapsed**2 * self.velocity_variance
            + _PROCESS_NOISE * elapsed**3 / 3,
            self.covariance + elapsed * self.velocity_variance + _PROCESS_NOISE * elapsed**2 / 2,
            self.velocity_variance + _PROCESS_NOISE * elapsed,
        )

    def observe(self, measured: float, measurement_variance: float) -> "_Axis":
        innovation = measured - self.position
        denominator = self.variance + measurement_variance
        position_gain = self.variance / denominator
        velocity_gain = self.covariance / denominator
        return _Axis(
            self.position + position_gain * innovation,
            self.velocity + velocity_gain * innovation,
            self.variance * measurement_variance / denominator,
            self.covariance * measurement_variance / denominator,
            self.velocity_variance - velocity_gain * self.covariance,
        )


def run_offline(
    scene: SceneConfig,
    *,
    cancelled: Callable[[], bool] = lambda: False,
    on_progress: Callable[[int], None] = lambda percent: None,
) -> OfflineResult:
    """Sample the configured route and estimate its position as observations arrive.

    Use a fixed observation delay equal to the configured upper bound and an
    observation blackout centered in the run. Lighting scales measurement noise.
    """
    n = math.floor(scene.duration_s * _RATE_HZ) + 1
    delay_steps = math.ceil(scene.observation_delay_s * _RATE_HZ)
    sigma = scene.measurement_noise_m / scene.illumination_gain
    blackout_start = (scene.duration_s - scene.observation_blackout_s) / 2
    blackout_end = blackout_start + scene.observation_blackout_s
    noise = Random(scene.seed)
    axis = (_Axis(), _Axis())
    last_capture_time = 0.0
    pending: list[tuple[float, float] | None] = []
    samples = []
    received = blocked = covered = 0
    error_sum = 0.0
    progress = -1

    for tick in range(n):
        if cancelled():
            raise SimulationCancelled()
        elapsed = tick / _RATE_HZ
        truth = target_position(elapsed * scene.target_speed_mps, scene.target_route_direction)
        measured = (truth[0] + noise.gauss(0, sigma), truth[1] + noise.gauss(0, sigma))
        in_blackout = blackout_start <= elapsed < blackout_end
        pending.append(None if in_blackout else measured)
        blocked += in_blackout

        capture_index = tick - delay_steps
        received_at = None
        if capture_index >= 0 and (observation := pending[capture_index]) is not None:
            received_at = capture_index / _RATE_HZ
            axis = tuple(model.predict(received_at - last_capture_time).observe(value, sigma**2)
                         for model, value in zip(axis, observation))
            last_capture_time = received_at
            received += 1

        north, east = (model.predict(elapsed - last_capture_time) for model in axis)
        north_error, east_error = north.position - truth[0], east.position - truth[1]
        squared_error = north_error**2 + east_error**2
        covered += north_error**2 / north.variance + east_error**2 / east.variance <= _CHI_SQUARE_95
        error_sum += squared_error
        area = math.pi * _CHI_SQUARE_95 * math.sqrt(north.variance * east.variance)
        samples.append(Sample(elapsed, *truth, north.position, east.position,
                              math.sqrt(squared_error), area, received_at))
        percent = (tick + 1) * 100 // n
        if percent != progress:
            progress = percent
            on_progress(progress)

    return OfflineResult(RunSummary(n, received, blocked, math.sqrt(error_sum / n), covered / n), tuple(samples))
