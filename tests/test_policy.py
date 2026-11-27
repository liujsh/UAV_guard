import shutil
from pathlib import Path
import tempfile
import unittest

import numpy as np

from guard_workbench.policy import POLICY_DIRECTORY, ResidualPolicy


REFERENCE_OBSERVATION = [
    0.15, 0.6, 0.15, -0.1, 0.0, 0.0, -0.05, 0.05,
    0.01, 0.0, 0.0144, 0.0, 0.0, 1.0, 0.0, 0.0,
    0.165, 0.59, 0.012346666306257248, 0.0, 0.018746666610240936,
    -2 / 30, -10 / 30, 1.0, 0.25, 1.0,
]
RANDOM_OBSERVATION = [
    -0.37409061193466187, -0.25563400983810425, 0.938418447971344,
    1.3921113014221191, -1.4164371490478516, 0.07248543202877045,
    1.8792126178741455, 0.8829987049102783, -1.7092552185058594,
    1.1123629808425903, -0.6152471899986267, -0.9437167048454285,
    0.3978410065174103, 0.0854761153459549, -0.3393697142601013,
    1.4011496305465698, 0.6586012840270996, -0.21141356229782104,
    0.1915159821510315, -0.9336500763893127, -0.2358149290084839,
    0.6722367405891418, 0.7156110405921936, 0.6664674878120422,
    1.2015354633331299, -0.099952831864357,
]


class ResidualPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = ResidualPolicy.load("s55007_zero")

    def test_numpy_actor_matches_all_four_sb3_regression_actions(self):
        cases = (
            (REFERENCE_OBSERVATION, [-0.997143030166626, 0.25229763984680176]),
            (np.zeros(26), [-0.7097516059875488, 0.05499589443206787]),
            (np.linspace(-1, 1, 26), [-0.9835621118545532, 0.10779869556427002]),
            (RANDOM_OBSERVATION, [-0.9371713399887085, -0.2733340263366699]),
        )
        for observation, expected in cases:
            with self.subTest(expected=expected):
                first = self.policy.predict(observation)
                second = self.policy.predict(observation)
                np.testing.assert_array_equal(first, second)
                np.testing.assert_allclose(first, expected, atol=2e-7, rtol=0)

    def test_invalid_observations_and_unknown_policy_are_rejected(self):
        for observation in (np.zeros(25), np.r_[np.zeros(25), np.nan]):
            with self.subTest(shape=np.shape(observation)), self.assertRaises(ValueError):
                self.policy.predict(observation)
        with self.assertRaises(ValueError):
            ResidualPolicy.load("unknown")

    def test_modified_weight_asset_fails_hash_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "policy"
            shutil.copytree(POLICY_DIRECTORY / "s55007_zero", copied)
            (copied / "weights.npz").write_bytes(b"modified")
            with self.assertRaisesRegex(RuntimeError, "哈希校验失败"):
                ResidualPolicy.load("s55007_zero", copied)


if __name__ == "__main__":
    unittest.main()
