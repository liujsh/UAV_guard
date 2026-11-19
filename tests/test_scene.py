from pathlib import Path
import tempfile
import unittest

from guard_workbench.scene import Obstacle, SceneConfig, load_scene, save_scene


class SceneConfigTests(unittest.TestCase):
    def test_custom_configuration_round_trip(self):
        config = SceneConfig(
            target_speed_mps=1.8,
            target_route_direction="reverse",
            illumination_gain=0.35,
            pedestrian_count=3,
            vehicle_count=1,
            obstacles=(Obstacle(4, -2, 2.0, 0.8, 2.5, 15, 45),),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scene.json"
            save_scene(path, SceneConfig())
            save_scene(path, config)
            self.assertEqual(load_scene(path), config)
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_invalid_inputs_are_rejected(self):
        cases = (
            {"target_speed_mps": float("nan")},
            {"target_route_direction": "sideways"},
            {"illumination_gain": 0},
            {"pedestrian_count": 5},
            {"vehicle_count": True},
            {"seed": -1},
            {"duration_s": 20, "obstacles": (Obstacle(0, 0, 1, 1, 1, 15, 45),)},
        )
        for fields in cases:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                SceneConfig(**fields)

    def test_obstacle_window_is_start_inclusive_and_end_exclusive(self):
        obstacle = Obstacle(0, 0, 1, 1, 1, start_s=2, end_s=5)
        self.assertFalse(obstacle.is_active(1.99))
        self.assertTrue(obstacle.is_active(2))
        self.assertFalse(obstacle.is_active(5))

    def test_invalid_documents_are_rejected(self):
        documents = (
            '{"format_version": 2, "scene": {}}',
            '{"format_version": 1, "scene": {"target_speed_ms": 1}}',
            '{"format_version": 1, "scene": {"obstacles": [42]}}',
            '{"format_version": 1, "scene": {"seed": 1, "seed": 2}}',
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scene.json"
            for document in documents:
                with self.subTest(document=document), self.assertRaises(ValueError):
                    path.write_text(document, encoding="utf-8")
                    load_scene(path)


if __name__ == "__main__":
    unittest.main()
