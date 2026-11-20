import csv
from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest

from guard_workbench.airsim_backend import AIRSIM_BACKEND
from guard_workbench.offline import OFFLINE_BACKEND, run_offline
from guard_workbench.results import TRAJECTORY, chart_data, export_csv
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
