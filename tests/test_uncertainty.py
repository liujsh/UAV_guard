import unittest

import numpy as np

from guard_workbench.uncertainty import (
    CHI_SQUARE_95, ESTIMATOR_VERSION, FILTER_Q, DelayedCV, ellipse_area,
    ellipse_contour, estimate_record, forecast, matrices, project_road, quality,
    range_risk, representative_points, state_risk,
)


class DelayedCVTests(unittest.TestCase):
    def test_oosm_replay_matches_frozen_reference_numbers(self):
        observations = [np.array(value) for value in ([.8, -.2], [1.1, .1], [1., .5])]
        delayed = DelayedCV(4, .5)
        for packets in ([], [(0, observations[0])], [(2, observations[2])], [(1, observations[1])]):
            mean, covariance = delayed.step(packets)

        chronological = DelayedCV(4, .5)
        chronological.step([(0, observations[0])])
        chronological.step([(1, observations[1])])
        chronological.step([(2, observations[2])])
        expected_mean, expected_covariance = chronological.step([])
        np.testing.assert_allclose(mean, expected_mean, atol=1e-12, rtol=0)
        np.testing.assert_allclose(covariance, expected_covariance, atol=1e-12, rtol=0)
        np.testing.assert_allclose(mean, [0.9156137698580888, 0.1122616509063468,
                                          0.4237984963348838, 0.027644145726568276],
                                   atol=1e-12, rtol=0)
        np.testing.assert_allclose(covariance[0, 0], 0.0714431389564123, atol=1e-12, rtol=0)
        self.assertEqual(delayed.oosm_updates, 1)
        self.assertTrue(np.all(np.linalg.eigvalsh(covariance) > 0))
        self.assertTrue(np.array_equal(covariance, covariance.T))

    def test_future_and_duplicate_packets_are_rejected(self):
        future = DelayedCV(2, .5)
        with self.assertRaisesRegex(ValueError, "已发生"):
            future.step([(1, [0, 0])])
        duplicate = DelayedCV(2, .5)
        duplicate.step([(0, [0, 0])])
        with self.assertRaisesRegex(ValueError, "重复"):
            duplicate.step([(0, [1, 1])])

    def test_unreleased_measurements_cannot_change_present_belief(self):
        first = DelayedCV(4, .5)
        second = DelayedCV(4, .5)
        mean = np.zeros(4)
        covariance = np.eye(4)
        other_mean = np.zeros(4)
        other_covariance = np.eye(4)
        for index in range(4):
            packets = [(index - 1, [index, 0])] if index else []
            mean, covariance = first.step(packets)
            other_mean, other_covariance = second.step(packets)
        np.testing.assert_array_equal(mean, other_mean)
        np.testing.assert_array_equal(covariance, other_covariance)
        self.assertNotIn(3, first.observations)


class PredictionTests(unittest.TestCase):
    def test_horizons_propagate_independently_from_the_same_posterior(self):
        mean = np.array([1., 2., .3, -.2])
        covariance = np.diag([.25, .36, .04, .09])
        for horizon in (1., 2., 4.):
            transition, process = matrices(horizon, FILTER_Q)
            predicted_mean, predicted_covariance = forecast(mean, covariance, horizon)
            np.testing.assert_allclose(predicted_mean, transition @ mean)
            np.testing.assert_allclose(predicted_covariance,
                                       transition @ covariance @ transition.T + process)
        sequential_mean, sequential_covariance = forecast(*forecast(mean, covariance, 1.), 1.)
        _, direct_covariance = forecast(mean, covariance, 2.)
        np.testing.assert_allclose(sequential_mean, forecast(mean, covariance, 2.)[0])
        np.testing.assert_allclose(sequential_covariance, direct_covariance)

    def test_five_points_match_frozen_road_projection(self):
        mean = np.array([0.9156137698580888, 0.1122616509063468,
                         0.4237984963348838, 0.027644145726568276])
        covariance = np.array([
            [0.0714431389564123, 0., 0.020731644355195443, 0.],
            [0., 0.0714431389564123, 0., 0.020731644355195443],
            [0.020731644355195443, 0., 0.05117023285214105, 0.],
            [0., 0.020731644355195443, 0., 0.05117023285214105],
        ])
        points = representative_points(mean, covariance)
        np.testing.assert_allclose(points, [[1.3394122661929726, 0.],
                                            [2.3508481719573515, 0.],
                                            [0.3279763604285939, 0.],
                                            [1.3394122661929726, 0.],
                                            [1.3394122661929726, 0.]], atol=1e-12, rtol=0)
        np.testing.assert_allclose(project_road([20, 6]), [16, 6])
        contour = ellipse_contour(mean, covariance)
        self.assertEqual(contour.shape, (81, 2))
        np.testing.assert_allclose(contour[0], contour[-1])
        self.assertGreater(ellipse_area(covariance), 0)

    def test_estimate_record_contains_current_belief_and_all_horizons(self):
        mean = np.array([1., 2., .3, -.2])
        covariance = np.diag([.25, .36, .04, .09])
        record = estimate_record(mean, covariance, 3., 1.4)
        self.assertEqual(record["observation_age_s"], 1.6)
        for horizon in (1, 2, 4):
            predicted_mean, predicted_covariance = forecast(mean, covariance, horizon)
            prefix = f"prediction_{horizon}s"
            self.assertEqual(record[f"{prefix}_north_m"], predicted_mean[0])
            self.assertEqual(record[f"{prefix}_area_m2"], ellipse_area(predicted_covariance))
        self.assertEqual(ESTIMATOR_VERSION, "DelayedCV-E1-v1")


class RiskTests(unittest.TestCase):
    def test_distance_band_occlusion_and_quality_conventions(self):
        grid = np.array([[12., 0., 50.], [0., 0., 50.], [30., 0., 50.]])
        np.testing.assert_allclose(range_risk(grid, [[0., 0.]]), [0., .5, 1.])
        box = [([-1., -1., 0.], [1., 1., 100.])]
        self.assertEqual(range_risk([[12., 0., 50.]], [[0., 0.]], box)[0], 1.)
        covered, nll, distance_squared = quality([1., 0.], np.eye(4))
        self.assertTrue(covered)
        self.assertEqual(distance_squared, 1.)
        self.assertAlmostEqual(nll, .5 * (2 * np.log(2 * np.pi) + 1))
        self.assertEqual(CHI_SQUARE_95, 5.991464547107979)

    def test_state_risk_evaluates_explicit_one_second_proposal(self):
        mean = np.array([0., 0., 0., 0.])
        covariance = np.diag([.25, .25, .04, .04])
        points = representative_points(mean, covariance)
        expected = range_risk([[0., 0., 50.]], points)[0]
        actual = state_risk(mean, covariance, [0., 0., 50.], [0., 0.])
        self.assertEqual(actual, expected)
        with self.assertRaisesRegex(ValueError, "固定协方差"):
            state_risk(mean, covariance, [0., 0., 50.], [0., 0.], covariance_mode="fixed")
        fixed = state_risk(mean, covariance, [0., 0., 50.], [0., 0.],
                           covariance_mode="fixed", fixed_covariance_diagonal=[1, 1, .25, .25])
        self.assertTrue(np.isfinite(fixed))
        self.assertNotEqual(fixed, actual)


if __name__ == "__main__":
    unittest.main()
