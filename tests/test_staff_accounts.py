import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

from app.auth import authenticate_admin, create_access_token, get_current_admin
from app.main import app
from app.rate_limit import reset_rate_limiters


NOW = "2026-07-21T12:00:00+00:00"


def staff_record(**overrides):
    record = {
        "id": "00000000-0000-4000-8000-000000000010",
        "username": "frontdesk-1",
        "display_name": "怡君",
        "password_hash": "hashed-password",
        "role": "staff",
        "is_active": True,
        "token_version": 1,
        "created_at": NOW,
        "updated_at": NOW,
    }
    record.update(overrides)
    return record


class StaffQuery:
    def __init__(self, supabase, table_name):
        self.supabase = supabase
        self.table_name = table_name
        self.operation = "select"
        self.payload = None
        self.filters = []
        self.ordering = None
        self.result_limit = None

    def select(self, *_args):
        self.operation = "select"
        return self

    def insert(self, payload):
        self.operation = "insert"
        self.payload = deepcopy(payload)
        return self

    def update(self, payload):
        self.operation = "update"
        self.payload = deepcopy(payload)
        return self

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def order(self, field, desc=False):
        self.ordering = (field, desc)
        return self

    def limit(self, value):
        self.result_limit = value
        return self

    def _matches(self, row):
        return all(row.get(field) == value for field, value in self.filters)

    def execute(self):
        rows = self.supabase.tables.setdefault(self.table_name, [])
        if self.operation == "select":
            data = [deepcopy(row) for row in rows if self._matches(row)]
            if self.ordering:
                field, desc = self.ordering
                data.sort(key=lambda row: row.get(field, ""), reverse=desc)
            if self.result_limit is not None:
                data = data[: self.result_limit]
            return SimpleNamespace(data=data)

        if self.operation == "insert":
            record = deepcopy(self.payload)
            record.setdefault("id", f"generated-{len(rows) + 1}")
            record.setdefault("created_at", NOW)
            record.setdefault("updated_at", NOW)
            rows.append(record)
            return SimpleNamespace(data=[deepcopy(record)])

        if self.operation == "update":
            updated = []
            for row in rows:
                if self._matches(row):
                    row.update(deepcopy(self.payload))
                    updated.append(deepcopy(row))
            return SimpleNamespace(data=updated)

        raise AssertionError(f"Unhandled operation: {self.operation}")


class StaffSupabase:
    def __init__(self, admin_users=None, audit_logs=None):
        self.tables = {
            "admin_users": deepcopy(admin_users or []),
            "admin_user_audit_logs": deepcopy(audit_logs or []),
        }

    def table(self, table_name):
        return StaffQuery(self, table_name)


class StaffAccountApiTest(unittest.TestCase):
    def setUp(self):
        app.dependency_overrides[get_current_admin] = lambda: {
            "username": "admin",
            "display_name": "管理員",
            "role": "admin",
            "token_version": 1,
        }
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_admin_creates_normalized_staff_account_and_audit_log(self):
        fake_supabase = StaffSupabase()

        with (
            patch("app.routers.admin.get_supabase", return_value=fake_supabase),
            patch("app.routers.admin.hash_password", return_value="hashed-new-password"),
        ):
            response = self.client.post(
                "/api/admin/staff-users",
                json={
                    "username": " FrontDesk-1 ",
                    "display_name": " 怡君 ",
                    "password": "password123",
                },
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["username"], "frontdesk-1")
        self.assertEqual(response.json()["display_name"], "怡君")
        self.assertNotIn("password_hash", response.json())
        self.assertNotIn("token_version", response.json())
        created = fake_supabase.tables["admin_users"][0]
        self.assertEqual(created["password_hash"], "hashed-new-password")
        self.assertEqual(created["role"], "staff")
        self.assertTrue(created["is_active"])
        audit = fake_supabase.tables["admin_user_audit_logs"][0]
        self.assertEqual(audit["actor_username"], "admin")
        self.assertEqual(audit["target_username"], "frontdesk-1")
        self.assertEqual(audit["action"], "created")

    def test_admin_cannot_create_duplicate_username(self):
        fake_supabase = StaffSupabase(admin_users=[staff_record()])

        with patch("app.routers.admin.get_supabase", return_value=fake_supabase):
            response = self.client.post(
                "/api/admin/staff-users",
                json={
                    "username": "FRONTDESK-1",
                    "display_name": "另一位",
                    "password": "password123",
                },
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "工作人員帳號已存在")
        self.assertEqual(len(fake_supabase.tables["admin_users"]), 1)

    def test_account_list_contains_only_staff_sorted_by_username(self):
        fake_supabase = StaffSupabase(
            admin_users=[
                staff_record(username="z-team", display_name="志工乙"),
                staff_record(
                    id="00000000-0000-4000-8000-000000000011",
                    username="a-team",
                    display_name="志工甲",
                ),
                staff_record(
                    id="00000000-0000-4000-8000-000000000012",
                    username="admin",
                    display_name="管理員",
                    role="admin",
                ),
            ],
        )

        with patch("app.routers.admin.get_supabase", return_value=fake_supabase):
            response = self.client.get("/api/admin/staff-users")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [user["username"] for user in response.json()],
            ["a-team", "z-team"],
        )

    def test_staff_role_cannot_access_system_settings(self):
        app.dependency_overrides[get_current_admin] = lambda: {
            "username": "frontdesk-1",
            "display_name": "怡君",
            "role": "staff",
            "token_version": 1,
        }

        response = self.client.get("/api/admin/settings/rsvp")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "需要管理員權限")

    def test_password_reset_increments_token_version_and_records_audit(self):
        fake_supabase = StaffSupabase(
            admin_users=[staff_record(token_version=4)],
        )

        with (
            patch("app.routers.admin.get_supabase", return_value=fake_supabase),
            patch("app.routers.admin.hash_password", return_value="new-hash"),
        ):
            response = self.client.post(
                "/api/admin/staff-users/frontdesk-1/password",
                json={"password": "new-password"},
            )

        self.assertEqual(response.status_code, 200)
        updated = fake_supabase.tables["admin_users"][0]
        self.assertEqual(updated["password_hash"], "new-hash")
        self.assertEqual(updated["token_version"], 5)
        self.assertEqual(
            fake_supabase.tables["admin_user_audit_logs"][0]["action"],
            "password_reset",
        )

    def test_deactivation_revokes_sessions_and_records_audit(self):
        fake_supabase = StaffSupabase(admin_users=[staff_record(token_version=2)])

        with patch("app.routers.admin.get_supabase", return_value=fake_supabase):
            response = self.client.patch(
                "/api/admin/staff-users/frontdesk-1/status",
                json={"is_active": False},
            )

        self.assertEqual(response.status_code, 200)
        updated = fake_supabase.tables["admin_users"][0]
        self.assertFalse(updated["is_active"])
        self.assertEqual(updated["token_version"], 3)
        self.assertEqual(
            fake_supabase.tables["admin_user_audit_logs"][0]["action"],
            "deactivated",
        )

    def test_display_name_can_change_but_username_stays_fixed(self):
        fake_supabase = StaffSupabase(admin_users=[staff_record()])

        with patch("app.routers.admin.get_supabase", return_value=fake_supabase):
            response = self.client.patch(
                "/api/admin/staff-users/frontdesk-1/display-name",
                json={"display_name": " 接待組長 "},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["display_name"], "接待組長")
        self.assertEqual(response.json()["username"], "frontdesk-1")
        self.assertEqual(
            fake_supabase.tables["admin_user_audit_logs"][0]["action"],
            "display_name_updated",
        )

    def test_audit_endpoint_returns_latest_twenty_records(self):
        logs = [
            {
                "actor_username": "admin",
                "target_username": f"staff-{index}",
                "action": "created",
                "created_at": f"2026-07-21T12:{index:02d}:00+00:00",
            }
            for index in range(25)
        ]
        fake_supabase = StaffSupabase(audit_logs=logs)

        with patch("app.routers.admin.get_supabase", return_value=fake_supabase):
            response = self.client.get("/api/admin/staff-user-audit-logs")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 20)
        self.assertEqual(response.json()[0]["target_username"], "staff-24")
        self.assertEqual(response.json()[-1]["target_username"], "staff-5")


class SessionVersionTest(unittest.TestCase):
    def setUp(self):
        reset_rate_limiters()

    def tearDown(self):
        reset_rate_limiters()

    def test_login_response_includes_display_name_and_versioned_token(self):
        client = TestClient(app)
        with patch(
            "app.routers.auth.authenticate_admin",
            return_value={
                "username": "frontdesk-1",
                "display_name": "怡君",
                "role": "staff",
                "token_version": 3,
            },
        ):
            response = client.post(
                "/api/auth/login",
                json={"username": "frontdesk-1", "password": "password123"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["display_name"], "怡君")
        self.assertEqual(response.json()["role"], "staff")
        self.assertTrue(response.json()["access_token"])

    def test_login_rate_limit_rejects_repeated_attempts(self):
        client = TestClient(app)

        with patch("app.routers.auth.authenticate_admin", return_value=None):
            for _index in range(20):
                response = client.post(
                    "/api/auth/login",
                    json={"username": "frontdesk-1", "password": "wrong"},
                )
                self.assertEqual(response.status_code, 401)

            response = client.post(
                "/api/auth/login",
                json={"username": "frontdesk-1", "password": "wrong"},
            )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(
            response.json()["detail"],
            "Too many requests. Please try again later.",
        )
        self.assertIn("retry-after", response.headers)

    def test_login_normalizes_username_and_returns_display_name(self):
        fake_supabase = StaffSupabase(admin_users=[staff_record()])

        with (
            patch("app.auth.get_supabase", return_value=fake_supabase),
            patch("app.auth.verify_password", return_value=True),
        ):
            user = authenticate_admin(" FrontDesk-1 ", "password123")

        self.assertEqual(user["username"], "frontdesk-1")
        self.assertEqual(user["display_name"], "怡君")
        self.assertEqual(user["token_version"], 1)

    def test_current_user_rejects_token_after_session_version_changes(self):
        token = create_access_token("frontdesk-1", "staff", 1)
        fake_supabase = StaffSupabase(
            admin_users=[staff_record(token_version=2)],
        )
        credentials = HTTPAuthorizationCredentials(
            scheme="Bearer",
            credentials=token,
        )

        with patch("app.auth.get_supabase", return_value=fake_supabase):
            with self.assertRaises(HTTPException) as context:
                get_current_admin(credentials)
        self.assertEqual(context.exception.status_code, 401)

    def test_current_user_rejects_disabled_account(self):
        token = create_access_token("frontdesk-1", "staff", 3)
        fake_supabase = StaffSupabase(
            admin_users=[staff_record(is_active=False, token_version=3)],
        )
        credentials = HTTPAuthorizationCredentials(
            scheme="Bearer",
            credentials=token,
        )

        with patch("app.auth.get_supabase", return_value=fake_supabase):
            with self.assertRaises(HTTPException) as context:
                get_current_admin(credentials)
        self.assertEqual(context.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
