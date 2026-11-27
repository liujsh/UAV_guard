import csv
from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest

from guard_workbench.airsim_backend import AIRSIM_BACKEND
from guard_workbench.offline import OFFLINE_BACKEND, run_offline
from guard_workbench.results import (
    TRAJECTORY, chart_data, export_csv, metrics, prediction_overlay,
)
from guard_workbench.scene import SceneConfig
from guard_workbench.workspace import RunInfo, RunRecord


def _run(run_id, backend, scene, result):
    return RunRecord(RunInfo(run_id, "demo", backend, "completed", "", None, ""), scene, result)


class ResultTests(unittest.TestCase):
    def test_tracks_and_metrics_are_aligned_to_original_times(self):
        first = SceneConfig(duration_s=1)
        second = SceneConfig(duration_s=2, target_route_direction="reverse")
        runs = [_run(str(number), OFFLINE_BACKEND, scene, asdict(run_offline(scene)))
                for number, scene in enumerate((first, second))]
        paths, x_label, y_label = chart_data(runs, TRAJECTORY)
        self.assertEqual((x_label, y_label), ("东向 / m", "北向 / m"))
        self.assertEqual([len(path.points) for path in paths], [6, 6, 11, 11])
        self.assertEqual(paths[0].points[-1], (0, 0.7))
        self.assertEqual(paths[2].points[-1], (1.4, 0))
        error, _, _ = chart_data(runs, "位置误差")
        self.assertEqual(error[0].points[-1][0], 1.0)
        self.assertEqual(error[1].points[-1][0], 2.0)
        ages, _, _ = chart_data(runs, "观测年龄")
        self.assertEqual([len(series.points) for series in ages], [6, 11])
        overlay = prediction_overlay(runs[0], .4, 2.0)
        self.assertIsNotNone(overlay)
        assert overlay is not None
        self.assertEqual(overlay.horizon_s, 2.0)
        self.assertEqual(len(overlay.contour), 81)
        self.assertEqual(len(overlay.representatives), 5)
        stored_result = runs[0].result
        assert stored_result is not None
        row = stored_result["samples"][2]
        self.assertEqual(overlay.center, (row["prediction_2s_east_m"], row["prediction_2s_north_m"]))
        self.assertEqual(overlay.observation_age_s, row["observation_age_s"])
        old_airsim = _run("old", AIRSIM_BACKEND, first,
                          {"samples": [{"time_s": 0, "visible": True}]})
        self.assertNotIn("位置误差", metrics(AIRSIM_BACKEND, old_airsim.result))
        self.assertIsNone(prediction_overlay(old_airsim, 0, 1.0))

    def test_airsim_export_preserves_identifiers_missing_values_and_visibility(self):
        rows = [{"time_s": 0, "visible": False, "received_capture_s": None},
                {"time_s": .2, "visible": True, "received_capture_s": 0}]
        first = _run("a", AIRSIM_BACKEND, SceneConfig(seed=1), {"samples": rows})
        second = _run("b", AIRSIM_BACKEND, SceneConfig(seed=2), {"samples": rows[:1]})
        data, _, _ = chart_data([first, second], "相机可见性")
        self.assertEqual(data[0].points, ((0, 0.0), (.2, 1.0)))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "comparison.csv"
            export_csv(output, [first, second])
            with output.open(encoding="utf-8-sig", newline="") as stream:
                saved = list(csv.DictReader(stream))
        self.assertEqual([row["run_id"] for row in saved], ["a", "a", "b"])
        self.assertEqual([row["visible"] for row in saved], ["0", "1", "0"])
        self.assertEqual([row["received_capture_s"] for row in saved], ["", "0", ""])

    def test_guard_metrics_and_uav_trajectory_use_stored_samples(self):
        rows = [{"time_s": 0.0, "truth_north_m": 0.0, "truth_east_m": 0.0,
                 "estimate_north_m": 0.1, "estimate_east_m": 0.0,
                 "uav_north_m": -2.0, "uav_east_m": -10.0,
                 "guard_distance_m": 10.198, "measurement_valid": True, "guard": True,
                 "predicted_occlusion_risk": 0.0, "predicted_distance_risk": 0.0,
                 "predicted_state_risk": 0.0, "residual_action_north": -0.5,
                 "residual_action_east": 0.25, "control_effort": 0.002}]
        run = _run("guard", AIRSIM_BACKEND, SceneConfig(guard_enabled=True), {"samples": rows})
        available = metrics(AIRSIM_BACKEND, run.result)
        self.assertIn("守卫距离", available)
        self.assertIn("预测综合风险", available)
        residual, _, label = chart_data([run], "北向残差动作")
        self.assertEqual(residual[0].points, ((0.0, -0.5),))
        self.assertEqual(label, "归一化动作")
        trajectory, _, _ = chart_data([run], TRAJECTORY)
        self.assertEqual([series.name for series in trajectory], ["本次真值", "本次估计", "本次无人机"])
        self.assertTrue(trajectory[2].dotted)
        self.assertEqual(trajectory[2].points, ((-10.0, -2.0),))
        effort, _, label = chart_data([run], "控制代价")
        self.assertEqual(effort[0].points, ((0.0, 0.002),))
        self.assertIn("均方速度", label)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "guard.csv"
            export_csv(output, [run])
            with output.open(encoding="utf-8-sig", newline="") as stream:
                saved = next(csv.DictReader(stream))
        self.assertEqual(saved["guard"], "1")
        self.assertEqual(saved["uav_north_m"], "-2.0")
