import unittest

from guard_workbench.airsim_rpc import AirSimRPC
from guard_workbench.flight import GuardDrone, UAV_Z_NED_M
from guard_workbench.offline import SimulationCancelled
from guard_workbench.scene import SceneConfig


def _state(north=-32.0, east=-70.0, z=UAV_Z_NED_M):
    return {"timestamp": 123, "kinematics_estimated": {
        "position": {"x_val": north, "y_val": east, "z_val": z},
        "linear_velocity": {"x_val": 0.0, "y_val": 0.0, "z_val": 0.0},
    }}


class FakeRPC:
    def __init__(self, states=None):
        self.calls = []
        self.states = list(states or [])
        self.current_state = _state()
        self.collision = {"has_collided": False, "time_stamp": 0}
        self.failures = set()

    def call(self, method, *args, **kwargs):
        self.calls.append((method, args, kwargs))
        if method in self.failures:
            self.failures.remove(method)
            raise RuntimeError(f"测试故障：{method}")
        if method == "simSetVehiclePose":
            return True
        if method == "armDisarm":
            return True
        if method == "getMultirotorState":
            if self.states:
                self.current_state = self.states.pop(0)
            return self.current_state
        if method == "simGetCollisionInfo":
            return self.collision
        return None


class GuardDroneTests(unittest.TestCase):
    def test_ready_start_uses_common_origin_and_configured_wind(self):
        rpc = FakeRPC()
        drone = GuardDrone(rpc, SceneConfig(guard_enabled=True, wind_north_mps=2.0))
        state = drone.start()
        self.assertTrue(drone.initialized)
        self.assertEqual((state.north_m, state.east_m, state.height_m), (-2.0, -10.0, 50.0))
        pose_call = next(call for call in rpc.calls if call[0] == "simSetVehiclePose")
        position = pose_call[1][0]["position"]
        self.assertEqual((position["x_val"], position["y_val"], position["z_val"]),
                         (-32.0, -70.0, 1.02))
        takeoff = next(call for call in rpc.calls if call[0] == "takeoff")
        self.assertEqual(takeoff[2]["timeout"], 30)
        winds = [call[1][0] for call in rpc.calls if call[0] == "simSetWind"]
        self.assertEqual(winds, [{"x_val": 0.0, "y_val": 0.0, "z_val": 0.0},
                                 {"x_val": 2.0, "y_val": 0.0, "z_val": 0.0}])
        self.assertFalse(any(call[0] == "moveByVelocityZ" for call in rpc.calls))

    def test_cancelled_initialization_stops_before_takeoff(self):
        rpc = FakeRPC()
        drone = GuardDrone(rpc, SceneConfig(guard_enabled=True), cancelled=lambda: True)
        with self.assertRaises(SimulationCancelled):
            drone.start()
        self.assertTrue(drone.active)
        self.assertFalse(any(call[0] == "takeoff" for call in rpc.calls))
        drone.stop()
        self.assertFalse(drone.active)

    def test_initialization_pulse_reaches_height_gate_before_wind(self):
        rpc = FakeRPC([_state(z=1.02), _state(), _state()])
        drone = GuardDrone(rpc, SceneConfig(guard_enabled=True, wind_east_mps=-1.0))
        drone.start()
        self.assertEqual(drone.initialization_controls, 1)
        pulse = next(call for call in rpc.calls if call[0] == "moveByVelocityZ")
        self.assertEqual(pulse[1][:4], (0.0, 0.0, UAV_Z_NED_M, 0.2))
        self.assertEqual(pulse[2]["timeout"], 5)

    def test_control_pulse_pauses_and_detects_new_collision(self):
        rpc = FakeRPC()
        drone = GuardDrone(rpc, SceneConfig(guard_enabled=True))
        drone.active = drone.initialized = True
        drone.initial_collision_stamp = 1
        state = drone.command(0.2, -0.3)
        self.assertEqual(state.north_m, -2.0)
        self.assertFalse(any(call[0] == "simContinueForTime" for call in rpc.calls))
        self.assertEqual(rpc.calls[-1][0], "simGetCollisionInfo")
        rpc.collision = {"has_collided": True, "time_stamp": 2, "object_name": "Building"}
        with self.assertRaisesRegex(RuntimeError, "发生碰撞"):
            drone.command(0, 0)

    def test_pause_is_restored_when_control_rpc_fails(self):
        rpc = FakeRPC()
        rpc.failures.add("moveByVelocityZ")
        drone = GuardDrone(rpc, SceneConfig(guard_enabled=True))
        drone.active = True
        with self.assertRaisesRegex(RuntimeError, "moveByVelocityZ"):
            drone.command(0, 0)
        self.assertEqual(rpc.calls[-1], ("simPause", (True,), {}))

    def test_stop_attempts_all_cleanup_and_reports_unconfirmed_steps(self):
        rpc = FakeRPC()
        rpc.failures.update(("land", "enableApiControl"))
        drone = GuardDrone(rpc, SceneConfig(guard_enabled=True))
        drone.active = True
        with self.assertRaisesRegex(RuntimeError, "安全停止未完全确认") as raised:
            drone.stop()
        self.assertIn("land", str(raised.exception))
        self.assertIn("enableApiControl", str(raised.exception))
        self.assertFalse(drone.active)
        self.assertEqual(rpc.calls[-1][0], "simSetWind")
        land = next(call for call in rpc.calls if call[0] == "land")
        self.assertEqual(land[2]["timeout"], 70)


class RPCTimeoutTests(unittest.TestCase):
    def test_single_call_timeout_is_restored(self):
        class Socket:
            def __init__(self):
                self.timeout = 8.0
                self.changes = []
            def gettimeout(self):
                return self.timeout
            def settimeout(self, value):
                self.timeout = value
                self.changes.append(value)
            def sendall(self, _data):
                return None
        rpc = AirSimRPC.__new__(AirSimRPC)
        rpc._socket = Socket()
        rpc._unpacker = [[1, 1, None, True]]
        rpc._request_id = 0
        self.assertTrue(rpc.call("ping", timeout=3.0))
        self.assertEqual(rpc._socket.changes, [3.0, 8.0])
        self.assertEqual(rpc._socket.timeout, 8.0)


if __name__ == "__main__":
    unittest.main()
