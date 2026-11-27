import unittest

import numpy as np

from guard_workbench.control import (
    CONTROL_Q, GeometricController, choose_goal, fence_velocity, obstacle_boxes,
)
from guard_workbench.flight import DroneState
from guard_workbench.policy import ResidualPolicy
from guard_workbench.scene import Obstacle, SceneConfig


class GeometricControlTests(unittest.TestCase):
    def setUp(self):
        self.mean = np.array([1.0, 2.0, 0.3, -0.2])
        self.covariance = np.diag([0.25, 0.36, 0.04, 0.09])
        self.drone = DroneState(0, -2.0, -10.0, 50.0, 0.0, 0.0, 0.0, -48.98)

    def test_goal_and_proposal_match_frozen_reference_numbers(self):
        goal, detail = choose_goal(self.mean, self.covariance, self.drone.position,
                                   [-2.0, -10.0])
        np.testing.assert_array_equal(goal, [-3.0, -9.0])
        self.assertEqual(detail["candidates"], 129)
        self.assertAlmostEqual(detail["base_cost"], 0.14981817658249044)
        self.assertEqual(detail["risk"], 0.0)
        decision = GeometricController(SceneConfig(guard_enabled=True)).decide(
            self.mean, self.covariance, self.drone, tick=0, total_steps=300,
            observation_age_s=0.0, measurement_valid=False)
        np.testing.assert_allclose(decision.base_proposal, [-0.36, 0.44], atol=1e-12)
        np.testing.assert_allclose(decision.applied, decision.base_proposal, atol=1e-12)
        np.testing.assert_array_equal(decision.residual_action, [0.0, 0.0])
        self.assertEqual(decision.predicted_state_risk, 0.0)
        self.assertEqual(CONTROL_Q, 0.056)

    def test_policy_input_has_frozen_order_scaling_and_zero_residual_history(self):
        decision = GeometricController(SceneConfig(guard_enabled=True)).decide(
            self.mean, self.covariance, self.drone, tick=0, total_steps=300,
            observation_age_s=0.0, measurement_valid=False)
        self.assertEqual(decision.observation.shape, (26,))
        self.assertEqual(decision.observation.dtype, np.float32)
        expected = [
            0.15, 0.6, 0.15, -0.1, 0.0, 0.0, -0.05, 0.05,
            0.01, 0.0, 0.0144, 0.0, 0.0, 1.0, 0.0, 0.0,
            0.165, 0.59, 0.012346666306257248, 0.0, 0.018746666610240936,
            -2 / 30, -10 / 30, 1.0, 0.25, 1.0,
        ]
        np.testing.assert_allclose(decision.observation, expected, atol=1e-7, rtol=0)

    def test_residual_action_and_previous_action_history_match_frozen_actor(self):
        policy = ResidualPolicy.load("s55007_zero")
        controller = GeometricController(SceneConfig(guard_enabled=True,
                                                     guard_policy_id="s55007_zero"), policy)
        first = controller.decide(self.mean, self.covariance, self.drone, tick=0,
                                  total_steps=300, observation_age_s=0.0,
                                  measurement_valid=False)
        expected = np.array([-0.997143030166626, 0.25229763984680176])
        np.testing.assert_allclose(first.residual_action, expected, atol=2e-7, rtol=0)
        np.testing.assert_allclose(first.proposal,
                                   np.clip(first.base_proposal + 0.75 * expected, -1.5, 1.5),
                                   atol=2e-7, rtol=0)
        self.assertEqual(controller.version, "td3-residual-s55007-zero-v1")
        second = controller.decide(self.mean, self.covariance, self.drone, tick=1,
                                   total_steps=300, observation_age_s=0.2,
                                   measurement_valid=True)
        np.testing.assert_allclose(second.observation[14:16], first.residual_action,
                                   atol=1e-7, rtol=0)

    def test_geofence_changes_only_applied_command(self):
        drone = DroneState(0, -17.0, 0.0, 50.0, -1.0, 0.0, 0.0, -48.98)
        applied = fence_velocity([-1.5, 0.0], drone.position, drone.velocity, 1.5)
        np.testing.assert_allclose(applied, [1.5, 0.0])
        near_fence = DroneState(0, -15.0, 0.0, 50.0, -1.0, 0.0, 0.0, -48.98)
        proposal = np.array([1.2, 0.0])
        applied = fence_velocity(proposal, near_fence.position, near_fence.velocity, 1.5)
        np.testing.assert_allclose(applied, [1.5, 0.0])
        np.testing.assert_allclose(proposal, [1.2, 0.0])

    def test_scene_boxes_follow_obstacle_activation_window(self):
        scene = SceneConfig(guard_enabled=True,
                            obstacles=(Obstacle(4, -2, 2, 4, 6, 1, 2),))
        initial = obstacle_boxes(scene, 0.0)
        active = obstacle_boxes(scene, 1.0)
        ended = obstacle_boxes(scene, 2.0)
        self.assertEqual((len(initial), len(active), len(ended)), (1, 2, 1))
        lower, upper = active[1]
        np.testing.assert_allclose(lower, [3, -4, 1.02 - 6])
        np.testing.assert_allclose(upper, [5, 0, 1.02])


if __name__ == "__main__":
    unittest.main()
