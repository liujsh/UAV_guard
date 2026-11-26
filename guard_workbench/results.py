"""Chart series and CSV export for stored simulation runs."""

import csv
from dataclasses import dataclass
from pathlib import Path

from .airsim_backend import AIRSIM_BACKEND
from .workspace import RunRecord


TRAJECTORY = "轨迹"
OFFLINE_METRICS = {"位置误差": ("error_m", "误差 / m"),
                   "预测椭圆面积 (95%)": ("uncertainty_area_m2", "面积 / m²")}
AIRSIM_METRICS = {"相机距离": ("camera_distance_m", "距离 / m"),
                  "目标像素数": ("target_pixels", "像素数"), "相机可见性": ("visible", "可见性  0 / 1")}


@dataclass(frozen=True, slots=True)
class Series:
    name: str
    color: str
    points: tuple[tuple[float, float], ...]
    times: tuple[float, ...]
    dashed: bool = False


def metrics(backend: str) -> dict[str, tuple[str, str]]:
    return AIRSIM_METRICS if backend == AIRSIM_BACKEND else OFFLINE_METRICS


def chart_data(runs: list[RunRecord], metric: str) -> tuple[list[Series], str, str]:
    """Build aligned series from original samples without interpolation."""
    series = []
    for index, run in enumerate(runs):
        rows = run.result["samples"]
        times = tuple(row["time_s"] for row in rows)
        color = ("#338CED", "#E76624")[index]
        label = "本次" if index == 0 else "对照"
        if metric == TRAJECTORY:
            truth = tuple((row["truth_east_m"], row["truth_north_m"]) for row in rows)
            series.append(Series(label + "真值", color, truth, times))
            if run.info.backend != AIRSIM_BACKEND:
                estimate = tuple((row["estimate_east_m"], row["estimate_north_m"]) for row in rows)
                series.append(Series(label + "估计", color, estimate, times, dashed=True))
        else:
            field, unit = metrics(run.info.backend)[metric]
            series.append(Series(label, color, tuple((row["time_s"], float(row[field])) for row in rows), times))
    return (series, "东向 / m", "北向 / m") if metric == TRAJECTORY else (series, "时间 / s", unit)


def export_csv(path: Path, runs: list[RunRecord]) -> None:
    """Export original rows with identifiers and stable numeric booleans."""
    sample_fields = dict.fromkeys(key for run in runs for row in run.result["samples"] for key in row)
    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, ["run_id", "backend", "seed", *sample_fields])
        writer.writeheader()
        for run in runs:
            for sample in run.result["samples"]:
                row = {key: int(value) if isinstance(value, bool) else value for key, value in sample.items()}
                writer.writerow({"run_id": run.info.run_id, "backend": run.info.backend,
                                 "seed": run.scene.seed, **row})
