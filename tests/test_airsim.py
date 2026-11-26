from base64 import b64decode
from io import BytesIO
import os
import unittest
from unittest.mock import patch

from PIL import Image

from guard_workbench.airsim_backend import AirSimEndpoint, inspect_airsim, run_airsim, _target_pixels
from guard_workbench.airsim_rpc import AirSimRPC
from guard_workbench.offline import SimulationCancelled
from guard_workbench.scene import Obstacle, SceneConfig


class AirSimTests(unittest.TestCase):
    def test_segmentation_counts_only_target_color(self):
        with BytesIO() as buffer:
            with Image.new("RGB", (4, 3), (92, 31, 106)) as segmentation:
                segmentation.putpixel((0, 1), (92, 31, 107))
                segmentation.save(buffer, format="PNG")
            self.assertEqual(_target_pixels(buffer.getvalue()), 11)

    def test_already_cancelled_run_never_connects(self):
        with patch("guard_workbench.airsim_backend.AirSimRPC") as connect:
            with self.assertRaises(SimulationCancelled):
                run_airsim(SceneConfig(), AirSimEndpoint(), cancelled=lambda: True, on_progress=lambda _: None)
        connect.assert_not_called()
    def test_preflight_is_read_only_and_returns_actual_camera_png(self):
        with BytesIO() as buffer:
            Image.new("RGB", (4, 3), (10, 20, 30)).save(buffer, format="PNG")
            png = buffer.getvalue()

        class FakeRPC:
            def __init__(self):
                self.calls = []

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def call(self, method, *args):
                self.calls.append(method)
                responses = {
                    "ping": True,
                    "getServerVersion": 181,
                    "simListAssets": ["Cube"],
                    "simListSceneObjects": [],
                    "simGetCameraInfo": {"pose": {"position": {"x_val": 1, "y_val": 2, "z_val": 3}}},
                    "simGetImages": [{"image_data_uint8": png, "time_stamp": 123}],
                }
                return responses[method]

        rpc = FakeRPC()
        with patch("guard_workbench.airsim_backend.AirSimRPC", return_value=rpc):
            result = inspect_airsim(AirSimEndpoint())
        self.assertEqual(result["server_version"], 181)
        self.assertEqual(result["frame_png"], png)
        self.assertNotIn("simPause", rpc.calls)
        self.assertNotIn("simSpawnObject", rpc.calls)
        self.assertNotIn("simDestroyObject", rpc.calls)


@unittest.skipUnless(os.getenv("GUARD_WORKBENCH_AIRSIM_TEST_PORT"), "未配置专用 AirSim 实例")
class LiveAirSimTests(unittest.TestCase):
    def test_scheduled_occlusion_and_actor_cleanup(self):
        endpoint = AirSimEndpoint(int(os.environ["GUARD_WORKBENCH_AIRSIM_TEST_PORT"]))
        scene = SceneConfig(duration_s=2, target_speed_mps=1.5, pedestrian_count=2, vehicle_count=1,
                            illumination_gain=0.5, observation_delay_s=0.4,
                            obstacles=(Obstacle(-6, -6.5, 6, 6, 34, 0, 1),))
        with AirSimRPC("127.0.0.1", endpoint.port) as rpc:
            paused = rpc.call("simIsPaused")
            self.assertEqual(rpc.call("simListSceneObjects", "GuardWorkbench_.*"), [])
            frames = []
            result = run_airsim(scene, endpoint, cancelled=lambda: False, on_progress=lambda _: None,
                                on_camera_frame=lambda data, stamp: frames.append((data, stamp)))
            self.assertEqual(len(frames), 3)
            self.assertTrue(all(data.startswith(b"\x89PNG\r\n\x1a\n") for data, _ in frames))
            self.assertEqual(result["summary"]["samples"], 11)
            self.assertAlmostEqual(result["samples"][-1]["truth_north_m"], 3, places=3)
            self.assertEqual([sample["active_obstacles"] for sample in result["samples"]], [1] * 5 + [0] * 6)
            pixels = [sample["target_pixels"] for sample in result["samples"]]
            self.assertEqual(pixels[:5], [0] * 5)
            self.assertTrue(all(count > 0 for count in pixels[5:]))
            with Image.open(BytesIO(b64decode(result["last_frame_png"]))) as frame:
                self.assertEqual(frame.size, (640, 480))

            progress = []
            with self.assertRaises(SimulationCancelled):
                run_airsim(scene, endpoint, cancelled=lambda: bool(progress), on_progress=progress.append)
            self.assertEqual(rpc.call("simListSceneObjects", "GuardWorkbench_.*"), [])
            self.assertEqual(rpc.call("simIsPaused"), paused)
