from base64 import b64decode
from contextlib import contextmanager
from io import BytesIO
import math
import os
import unittest
from unittest.mock import patch

from PIL import Image

from guard_workbench.airsim_backend import (
    AirSimEndpoint, _pose, _target_pixels, inspect_airsim, run_airsim,
)
from guard_workbench.airsim_rpc import AirSimRPC
from guard_workbench.flight import DroneState
from guard_workbench.offline import SimulationCancelled
from guard_workbench.scene import Obstacle, SceneConfig
from guard_workbench.uncertainty import ESTIMATOR_VERSION


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

    def test_airsim_result_contains_delayed_belief_and_all_forecasts(self):
        with BytesIO() as blank_buffer, BytesIO() as target_buffer:
            Image.new("RGB", (2, 2), (0, 0, 0)).save(blank_buffer, format="PNG")
            Image.new("RGB", (2, 2), (92, 31, 106)).save(target_buffer, format="PNG")
            blank = blank_buffer.getvalue()
            target = target_buffer.getvalue()

        class FakeRPC:
            def call(self, method, *_args):
                if method == "getServerVersion":
                    return 181
                if method == "simGetCameraInfo":
                    return {"pose": {"position": {"x_val": 0, "y_val": 0, "z_val": 0}}}
                if method == "simContinueForTime":
                    return None
                raise AssertionError(method)

        class FakeWorld:
            def __init__(self):
                self.rpc = FakeRPC()
                self.positions = {}

            def spawn(self, role, north, east, dimensions):
                self.positions[role] = _pose(north, east, dimensions[2])["position"]
                return role

            def move(self, name, north, east, height):
                self.positions[name] = _pose(north, east, height)["position"]

            def position(self, name):
                position = self.positions[name]
                return tuple(position[key] for key in ("x_val", "y_val", "z_val"))

        world = FakeWorld()
        segmentation_calls = 0

        @contextmanager
        def fake_world(*_args):
            yield world

        def fake_image(_rpc, image_type):
            nonlocal segmentation_calls
            if image_type == 5:
                segmentation_calls += 1
                return (blank if segmentation_calls == 1 else target), segmentation_calls
            return blank, 999

        with (patch("guard_workbench.airsim_backend._world", fake_world),
              patch("guard_workbench.airsim_backend._image", fake_image)):
            result = run_airsim(SceneConfig(duration_s=1), AirSimEndpoint(),
                                cancelled=lambda: False, on_progress=lambda _: None)

        self.assertEqual(result["summary"]["samples"], 6)
        self.assertEqual(result["summary"]["received_observations"], 6)
        self.assertEqual(result["summary"]["estimator_version"], ESTIMATOR_VERSION)
        for sample in result["samples"]:
            self.assertTrue(math.isfinite(sample["error_m"]))
            self.assertGreater(sample["uncertainty_area_m2"], 0)
            for horizon in (1, 2, 4):
                self.assertIn(f"prediction_{horizon}s_area_m2", sample)
                self.assertGreater(sample[f"prediction_{horizon}s_area_m2"], 0)
        self.assertEqual(result["samples"][-1]["observation_age_s"], 0)
    def test_guard_closed_loop_records_state_risk_events_and_safe_stop(self):
        with BytesIO() as blank_buffer, BytesIO() as target_buffer:
            Image.new("RGB", (2, 2), (0, 0, 0)).save(blank_buffer, format="PNG")
            Image.new("RGB", (2, 2), (92, 31, 106)).save(target_buffer, format="PNG")
            blank = blank_buffer.getvalue()
            target = target_buffer.getvalue()

        class FakeRPC:
            def __init__(self):
                self.calls = []
            def call(self, method, *_args):
                self.calls.append(method)
                if method == "getServerVersion":
                    return 181
                if method == "simGetCameraInfo":
                    return {"pose": {"position": {"x_val": 0, "y_val": 0, "z_val": 0}}}
                raise AssertionError(method)

        class FakeWorld:
            def __init__(self):
                self.rpc = FakeRPC()
                self.positions = {}
            def spawn(self, role, north, east, dimensions):
                self.positions[role] = _pose(north, east, dimensions[2])["position"]
                return role
            def move(self, name, north, east, height):
                self.positions[name] = _pose(north, east, height)["position"]
            def position(self, name):
                position = self.positions[name]
                return tuple(position[key] for key in ("x_val", "y_val", "z_val"))

        class FakeDrone:
            instances = []
            def __init__(self, rpc, scene, cancelled=lambda: False):
                self.rpc = rpc
                self.scene = scene
                self.cancelled = cancelled
                self.initialization_controls = 0
                self.stopped = False
                self.commands = []
                self.instances.append(self)
            def start(self):
                return DroneState(0, -2.0, -10.0, 50.0, 0.0, 0.0, 0.0, -48.98)
            def command(self, north, east):
                self.commands.append((north, east))
                index = len(self.commands)
                return DroneState(index * 200_000_000, -2.0 + north / 5, -10.0 + east / 5,
                                  50.0, north, east, 0.0, -48.98)
            def stop(self):
                self.stopped = True

        world = FakeWorld()
        segmentation_calls = 0
        @contextmanager
        def fake_world(*_args):
            yield world
        def fake_image(_rpc, image_type):
            nonlocal segmentation_calls
            if image_type == 5:
                segmentation_calls += 1
                return (blank if segmentation_calls == 1 else target), segmentation_calls
            return blank, 999

        states = []
        with (patch("guard_workbench.airsim_backend._world", fake_world),
              patch("guard_workbench.airsim_backend._image", fake_image),
              patch("guard_workbench.airsim_backend.GuardDrone", FakeDrone)):
            result = run_airsim(SceneConfig(duration_s=1, guard_enabled=True,
                                            observation_blackout_s=0.4), AirSimEndpoint(),
                                cancelled=lambda: False, on_progress=lambda _: None,
                                on_state=states.append)
        self.assertEqual(len(states), 6)
        self.assertTrue(FakeDrone.instances[-1].stopped)
        self.assertNotIn("simContinueForTime", world.rpc.calls)
        summary = result["summary"]
        self.assertEqual(summary["controller_version"], "geometric-reference-v1")
        self.assertEqual(summary["observation_losses"], 1)
        self.assertEqual(summary["observation_reacquisitions"], 1)
        self.assertGreaterEqual(summary["guard_fraction"], 0)
        for sample in result["samples"]:
            self.assertEqual(len(sample["policy_observation"]), 26)
            self.assertAlmostEqual(sample["predicted_state_risk"],
                                   sample["predicted_occlusion_risk"] + sample["predicted_distance_risk"])
            self.assertEqual(sample["uav_height_m"], 50.0)
        self.assertEqual([row["guard_event"] for row in result["samples"]],
                         [None, None, "observation_lost", None, "observation_reacquired", None])

        cancelled_states = []
        segmentation_calls = 0
        with (patch("guard_workbench.airsim_backend._world", fake_world),
              patch("guard_workbench.airsim_backend._image", fake_image),
              patch("guard_workbench.airsim_backend.GuardDrone", FakeDrone),
              self.assertRaises(SimulationCancelled)):
            run_airsim(SceneConfig(duration_s=1, guard_enabled=True), AirSimEndpoint(),
                       cancelled=lambda: bool(cancelled_states), on_progress=lambda _: None,
                       on_state=cancelled_states.append)
        self.assertTrue(FakeDrone.instances[-1].stopped)

        segmentation_calls = 0
        with (patch("guard_workbench.airsim_backend._world", fake_world),
              patch("guard_workbench.airsim_backend._image", fake_image),
              patch("guard_workbench.airsim_backend.GuardDrone", FakeDrone)):
            residual = run_airsim(SceneConfig(
                duration_s=1, guard_enabled=True, guard_policy_id="s55007_zero"),
                AirSimEndpoint(), cancelled=lambda: False, on_progress=lambda _: None)
        self.assertEqual(residual["summary"]["guard_policy_id"], "s55007_zero")
        self.assertEqual(residual["summary"]["controller_version"], "td3-residual-s55007-zero-v1")
        for index, sample in enumerate(residual["samples"]):
            previous = ([0.0, 0.0] if index == 0 else
                        [residual["samples"][index - 1]["residual_action_north"],
                         residual["samples"][index - 1]["residual_action_east"]])
            self.assertAlmostEqual(sample["policy_observation"][14], previous[0], places=6)
            self.assertAlmostEqual(sample["policy_observation"][15], previous[1], places=6)
            self.assertTrue(math.isfinite(sample["residual_action_north"]))
            self.assertTrue(math.isfinite(sample["residual_action_east"]))
        self.assertTrue(any(abs(row["residual_action_north"]) > 0.1
                            for row in residual["samples"]))


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

    def test_guard_drone_initialization_control_and_cleanup(self):
        endpoint = AirSimEndpoint(int(os.environ["GUARD_WORKBENCH_AIRSIM_TEST_PORT"]))
        scene = SceneConfig(duration_s=2, guard_enabled=True, observation_delay_s=0.4,
                            observation_blackout_s=0.4, wind_north_mps=0.2)
        with AirSimRPC("127.0.0.1", endpoint.port) as rpc:
            paused = rpc.call("simIsPaused")
            states = []
            result = run_airsim(scene, endpoint, cancelled=lambda: False, on_progress=lambda _: None,
                                on_state=states.append)
            self.assertEqual(len(states), result["summary"]["samples"])
            self.assertEqual(result["summary"]["controller_version"], "geometric-reference-v1")
            self.assertIn("guard_fraction", result["summary"])
            self.assertEqual(result["summary"]["observation_losses"], 1)
            self.assertEqual(result["summary"]["observation_reacquisitions"], 1)
            for sample in result["samples"]:
                self.assertTrue(math.isfinite(sample["uav_north_m"]))
                self.assertTrue(math.isfinite(sample["predicted_state_risk"]))
                self.assertEqual(len(sample["policy_observation"]), 26)
                self.assertLessEqual(abs(sample["applied_velocity_north_mps"]), scene.guard_max_speed_mps)
                self.assertLessEqual(abs(sample["applied_velocity_east_mps"]), scene.guard_max_speed_mps)
            self.assertEqual(rpc.call("simListSceneObjects", "GuardWorkbench_.*"), [])
            self.assertEqual(rpc.call("simIsPaused"), paused)
