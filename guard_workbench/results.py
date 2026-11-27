"""Chart series, prediction overlays, and CSV export for stored simulation runs."""

from bisect import bisect_left
import csv
from dataclasses import dataclass
from pathlib import Path

from .airsim_backend import AIRSIM_BACKEND
from .uncertainty import PREDICTION_HORIZONS_S, ellipse_contour, projected_ellipse_points
from .workspace import RunRecord


TRAJECTORY = "轨迹"
ESTIMATION_METRICS = {
    "位置误差": ("error_m", "误差 / m"),
    "当前椭圆面积 (95%)": ("uncertainty_area_m2", "面积 / m²"),
    "观测年龄": ("observation_age_s", "年龄 / s"),
    "1 s 预测椭圆面积 (95%)": ("prediction_1s_area_m2", "面积 / m²"),
    "2 s 预测椭圆面积 (95%)": ("prediction_2s_area_m2", "面积 / m²"),
    "4 s 预测椭圆面积 (95%)": ("prediction_4s_area_m2", "面积 / m²"),
}
OFFLINE_METRICS = ESTIMATION_METRICS
AIRSIM_METRICS = {
    "相机距离": ("camera_distance_m", "距离 / m"),
    "目标像素数": ("target_pixels", "像素数"),
    "相机可见性": ("visible", "可见性  0 / 1"),
    **ESTIMATION_METRICS,
    "守卫距离": ("guard_distance_m", "距离 / m"),
    "有效观测": ("measurement_valid", "有效性  0 / 1"),
    "守卫判定": ("guard", "守卫  0 / 1"),
    "预测遮挡风险": ("predicted_occlusion_risk", "风险"),
    "预测距离风险": ("predicted_distance_risk", "风险"),
    "预测综合风险": ("predicted_state_risk", "风险"),
    "北向残差动作": ("residual_action_north", "归一化动作"),
    "东向残差动作": ("residual_action_east", "归一化动作"),
    "控制代价": ("control_effort", "0.02 × 均方速度"),
}


@dataclass(frozen=True, slots=True)
class Series:
    name: str
    color: str
    points: tuple[tuple[float, float], ...]
    times: tuple[float, ...]
    dashed: bool = False
    dotted: bool = False


@dataclass(frozen=True, slots=True)
class PredictionOverlay:
    horizon_s: float
    center: tuple[float, float]
    contour: tuple[tuple[float, float], ...]
    representatives: tuple[tuple[float, float], ...]
    observation_age_s: float | None


def metrics(backend: str, result: dict | None = None) -> dict[str, tuple[str, str]]:
    options = AIRSIM_METRICS if backend == AIRSIM_BACKEND else OFFLINE_METRICS
    if result is None:
        return options
    available = {key for row in result.get("samples", []) for key, value in row.items() if value is not None}
    return {name: definition for name, definition in options.items() if definition[0] in available}


def chart_data(runs: list[RunRecord], metric: str) -> tuple[list[Series], str, str]:
    """Build aligned series from original samples without interpolation."""
    series = []
    for index, run in enumerate(runs):
        if run.result is None:
            continue
        rows = run.result["samples"]
        color = ("#338CED", "#E76624")[index]
        label = "本次" if index == 0 else "对照"
        if metric == TRAJECTORY:
            truth = tuple((row["truth_east_m"], row["truth_north_m"]) for row in rows)
            times = tuple(row["time_s"] for row in rows)
            series.append(Series(label + "真值", color, truth, times))
            if rows and "estimate_north_m" in rows[0]:
                estimate = tuple((row["estimate_east_m"], row["estimate_north_m"]) for row in rows)
                series.append(Series(label + "估计", color, estimate, times, dashed=True))
            if rows and "uav_north_m" in rows[0]:
                uav = tuple((row["uav_east_m"], row["uav_north_m"]) for row in rows)
                series.append(Series(label + "无人机", color, uav, times, dotted=True))
            continue
        definition = metrics(run.info.backend).get(metric)
        if definition is None:
            continue
        field, _ = definition
        points = tuple((row["time_s"], float(row[field])) for row in rows if row.get(field) is not None)
        if points:
            series.append(Series(label, color, points, tuple(point[0] for point in points)))
    if metric == TRAJECTORY:
        return series, "东向 / m", "北向 / m"
    return series, "时间 / s", metrics(runs[0].info.backend)[metric][1]


def prediction_overlay(run: RunRecord, elapsed_s: float, horizon_s: float) -> PredictionOverlay | None:
    """Read a stored forecast without rerunning or modifying the estimator."""
    if horizon_s not in PREDICTION_HORIZONS_S:
        raise ValueError("预测时距必须是 1、2 或 4 秒")
    if run.result is None:
        return None
    rows = run.result["samples"]
    if not rows:
        return None
    times = [row["time_s"] for row in rows]
    position = bisect_left(times, elapsed_s)
    if position >= len(rows):
        index = len(rows) - 1
    elif position and abs(times[position] - elapsed_s) >= abs(times[position - 1] - elapsed_s):
        index = position - 1
    else:
        index = position
    row = rows[index]
    prefix = f"prediction_{horizon_s:g}s"
    names = (f"{prefix}_north_m", f"{prefix}_east_m", f"{prefix}_cov_nn_m2",
             f"{prefix}_cov_ne_m2", f"{prefix}_cov_ee_m2")
    if any(row.get(name) is None for name in names):
        return None
    north, east, nn, ne, ee = (float(row[name]) for name in names)
    contour = ellipse_contour((north, east), ((nn, ne), (ne, ee)))
    representatives = projected_ellipse_points((north, east), ((nn, ne), (ne, ee)))
    return PredictionOverlay(
        horizon_s, (east, north),
        tuple((float(point[1]), float(point[0])) for point in contour),
        tuple((float(point[1]), float(point[0])) for point in representatives),
        None if row.get("observation_age_s") is None else float(row["observation_age_s"]),
    )


def export_csv(path: Path, runs: list[RunRecord]) -> None:
    """Export original rows with identifiers and stable numeric booleans."""
    sample_fields: dict[str, None] = {}
    for run in runs:
        if run.result is not None:
            for sample in run.result["samples"]:
                for key in sample:
                    sample_fields[key] = None
    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, ["run_id", "backend", "seed", *sample_fields])
        writer.writeheader()
        for run in runs:
            if run.result is None:
                continue
            for sample in run.result["samples"]:
                row = {key: int(value) if isinstance(value, bool) else value for key, value in sample.items()}
                writer.writerow({"run_id": run.info.run_id, "backend": run.info.backend,
                                 "seed": run.scene.seed, **row})
