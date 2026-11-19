from dataclasses import replace
import unittest

from guard_workbench.offline import SimulationCancelled, run_offline
from guard_workbench.scene import SceneConfig


class OfflineSimulationTests(unittest.TestCase):
    def test_configured_route_speed_and_direction(self):
        scene = SceneConfig(duration_s=2, target_speed_mps=1.5)
        forward = run_offline(scene)
        reverse = run_offline(replace(scene, target_route_direction="reverse"))
        self.assertEqual(len(forward.samples), 11)
        self.assertEqual(forward.samples[-1].truth_north_m, 3)
        self.assertEqual(forward.samples[-1].truth_east_m, 0)
        self.assertEqual(reverse.samples[-1].truth_north_m, 0)
        self.assertEqual(reverse.samples[-1].truth_east_m, 3)
        self.assertEqual(run_offline(scene), forward)

    def test_delay_and_blackout_do_not_release_missing_or_future_observations(self):
        scene = SceneConfig(duration_s=4, observation_delay_s=0.4, observation_blackout_s=2)
        result = run_offline(scene)
        self.assertIsNone(result.samples[0].received_capture_s)
        self.assertIsNone(result.samples[1].received_capture_s)
        self.assertEqual(result.samples[2].received_capture_s, 0)
        captures = [sample.received_capture_s for sample in result.samples
                    if sample.received_capture_s is not None]
        self.assertEqual(captures, [0, 0.2, 0.4, 0.6, 0.8, 3, 3.2, 3.4, 3.6])
        self.assertEqual(result.summary.received_observations, len(captures))
        self.assertEqual(result.summary.blackout_samples, 10)
        self.assertTrue(all(sample.uncertainty_area_m2 > 0 for sample in result.samples))

    def test_light_gain_changes_estimation_and_cancellation_is_cooperative(self):
        scene = SceneConfig(duration_s=4)
        base = run_offline(scene)
        dim = run_offline(replace(scene, illumination_gain=0.5))
        self.assertGreater(dim.samples[-1].uncertainty_area_m2, base.samples[-1].uncertainty_area_m2)
        progress = []
        with self.assertRaises(SimulationCancelled):
            run_offline(scene, cancelled=lambda: len(progress) >= 3, on_progress=progress.append)
        self.assertLess(progress[-1], 100)
