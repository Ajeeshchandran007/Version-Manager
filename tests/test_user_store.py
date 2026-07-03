import tempfile
import unittest
from pathlib import Path

from tests.conftest_path import PROJECT_ROOT  # noqa: F401
from App.user_store import (
    authenticate_user,
    list_user_audit,
    list_users,
    seed_users_from_config,
    set_user_active,
    upsert_user,
)


class UserStoreTests(unittest.TestCase):
    def test_seed_authenticate_and_track_last_login(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "users.db"
            seed_users_from_config(
                [{
                    "username": "admin",
                    "password": "secret",
                    "display_name": "Admin",
                    "role": "Admin",
                    "team_scope": ["*"],
                }],
                db_path,
            )

            user = authenticate_user("admin", "secret", db_path)

            self.assertIsNotNone(user)
            self.assertEqual(user["role"], "Admin")
            self.assertTrue(user["last_login_at"])
            self.assertIsNone(authenticate_user("admin", "wrong", db_path))

    def test_create_update_and_deactivate_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "users.db"
            upsert_user(
                username="qa1",
                password="pw1",
                display_name="QA One",
                role="QA Engineer",
                team_scope=["SourceOne"],
                active=True,
                actor="admin",
                db_path=db_path,
            )
            upsert_user(
                username="qa1",
                password=None,
                display_name="QA One Updated",
                role="Release Engineer",
                team_scope="SourceOne,DPS",
                active=True,
                actor="admin",
                db_path=db_path,
            )
            set_user_active("qa1", False, "admin", db_path)

            users = list_users(db_path, include_inactive=True)
            self.assertEqual(users[0]["display_name"], "QA One Updated")
            self.assertEqual(users[0]["role"], "Release Engineer")
            self.assertEqual(users[0]["team_scope"], ["SourceOne", "DPS"])
            self.assertFalse(users[0]["active"])
            self.assertIsNone(authenticate_user("qa1", "pw1", db_path))
            self.assertGreaterEqual(len(list_user_audit(db_path)), 3)


if __name__ == "__main__":
    unittest.main()
