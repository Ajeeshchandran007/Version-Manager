import os
import unittest

from tests.conftest_path import PROJECT_ROOT  # noqa: F401
from Core.policy import PolicyError, require_approval


class PolicyTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("VERSION_MANAGER_AUTO_APPROVE", None)

    def test_side_effect_requires_approval_by_default(self):
        os.environ.pop("VERSION_MANAGER_AUTO_APPROVE", None)
        with self.assertRaises(PolicyError):
            require_approval("send_email")

    def test_auto_approval_allows_side_effect(self):
        os.environ["VERSION_MANAGER_AUTO_APPROVE"] = "true"
        require_approval("send_email")


if __name__ == "__main__":
    unittest.main()

