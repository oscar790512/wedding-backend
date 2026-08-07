import unittest
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app.auth import get_current_admin
from app.main import app
from app.rate_limit import reset_rate_limiters


GUEST_ID = "00000000-0000-4000-8000-000000000001"


def guest_record(**overrides):
    base = {
        "id": GUEST_ID,
        "name": "Guest",
        "phone": "0912345678",
        "email": None,
        "status": "attend",
        "total_adults": 1,
        "total_children": 0,
        "actual_adults": None,
        "actual_children": None,
        "vegetarian_count": 0,
        "vegetarian_adults": 0,
        "vegetarian_children": 0,
        "allergy_notes": None,
        "child_seats": 0,
        "diet_notes": None,
        "need_invitation": False,
        "invitation_address": None,
        "decline_response": None,
        "blessing_message": None,
        "guest_category": None,
        "invitation_status": "not_required",
        "cake_status": "pending_pickup",
        "shipping_recipient": None,
        "shipping_phone": None,
        "shipping_address": None,
        "shipping_date": None,
        "tracking_no": None,
        "is_arrived": False,
        "arrived_at": None,
        "checkin_updated_at": None,
        "checkin_note": None,
        "checkin_token": None,
        "checkin_token_rotated_at": None,
        "gift_amount": Decimal("0"),
        "allocated_table": None,
        "admin_notes": None,
        "created_at": "2026-07-17T00:00:00+00:00",
        "updated_at": None,
        "deleted_at": None,
    }
    base.update(overrides)
    return base


class FakeQuery:
    def __init__(self, supabase, table_name):
        self.supabase = supabase
        self.table_name = table_name
        self.operation = "select"
        self.payload = None
        self.filters = []
        self.range_args = None
        self.count = None
        self.order_args = None
        self.order_kwargs = None

    def select(self, *_args, count=None):
        self.operation = "select"
        self.count = count
        return self

    def insert(self, payload):
        self.operation = "insert"
        self.payload = payload
        return self

    def update(self, payload):
        self.operation = "update"
        self.payload = payload
        return self

    def upsert(self, payload, on_conflict=None):
        self.operation = "upsert"
        self.payload = payload
        self.supabase.upsert_conflict = on_conflict
        return self

    def eq(self, field, value):
        self.filters.append(("eq", field, value))
        return self

    def is_(self, field, value):
        self.filters.append(("is", field, value))
        return self

    def limit(self, *_args):
        return self

    def order(self, *args, **kwargs):
        self.order_args = args
        self.order_kwargs = kwargs
        self.supabase.last_order_args = args
        self.supabase.last_order_kwargs = kwargs
        return self

    def range(self, start, end):
        self.range_args = (start, end)
        return self

    def execute(self):
        if self.operation == "select":
            self.supabase.select_execute_calls += 1
            if self.supabase.select_errors:
                raise self.supabase.select_errors.pop(0)
            data = deepcopy(self.supabase.table_data.get(self.table_name, self.supabase.select_data))
            for operation, field, value in self.filters:
                if operation == "eq":
                    data = [row for row in data if row.get(field) == value]
                elif operation == "is" and value == "null":
                    data = [row for row in data if row.get(field) is None]
            total = len(data)
            if self.range_args is not None:
                start, end = self.range_args
                data = data[start : end + 1]
                self.supabase.last_range_args = self.range_args
            self.supabase.last_select_count = self.count
            return SimpleNamespace(data=data, count=total)
        if self.operation == "insert":
            self.supabase.inserted_payload = deepcopy(self.payload)
            return SimpleNamespace(data=[guest_record(**self.payload)])
        if self.operation == "update":
            self.supabase.updated_payload = deepcopy(self.payload)
            merged = {
                **guest_record(),
                **deepcopy(self.supabase.update_base),
                **deepcopy(self.payload),
            }
            return SimpleNamespace(data=[merged])
        if self.operation == "upsert":
            self.supabase.upserted_payload = deepcopy(self.payload)
            return SimpleNamespace(data=[deepcopy(self.payload)])
        raise AssertionError(f"Unhandled fake operation: {self.operation}")


class FakeSupabase:
    def __init__(
        self,
        select_data=None,
        update_base=None,
        table_data=None,
        select_errors=None,
    ):
        self.select_data = select_data or []
        self.update_base = update_base or {}
        self.table_data = table_data or {}
        self.select_errors = list(select_errors or [])
        self.select_execute_calls = 0
        self.inserted_payload = None
        self.updated_payload = None
        self.upserted_payload = None
        self.upsert_conflict = None
        self.last_range_args = None
        self.last_select_count = None
        self.last_order_args = None
        self.last_order_kwargs = None

    def table(self, table_name):
        return FakeQuery(self, table_name)


class WeddingApiIntegrationTest(unittest.TestCase):
    def setUp(self):
        reset_rate_limiters()
        app.dependency_overrides[get_current_admin] = lambda: {
            "username": "admin",
            "role": "admin",
        }
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        reset_rate_limiters()

    def test_rsvp_submit_creates_attending_guest_with_checkin_token(self):
        fake_supabase = FakeSupabase()

        with (
            patch("app.routers.rsvp.get_supabase", return_value=fake_supabase),
            patch("app.routers.rsvp._create_unique_checkin_token", return_value="token-123"),
        ):
            response = self.client.post(
                "/api/rsvp",
                json={
                    "name": "  Oscar  ",
                    "phone": "0912-345-678",
                    "email": "OSCAR@example.com",
                    "status": "attend",
                    "total_adults": 2,
                    "total_children": 1,
                    "vegetarian_count": 1,
                    "need_invitation": True,
                    "invitation_address": "Taipei",
                    "guest_category": "男方朋友/同學",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["checkin_token"], "token-123")
        self.assertEqual(fake_supabase.inserted_payload["name"], "Oscar")
        self.assertEqual(fake_supabase.inserted_payload["phone"], "0912345678")
        self.assertEqual(fake_supabase.inserted_payload["email"], "oscar@example.com")
        self.assertEqual(fake_supabase.inserted_payload["invitation_status"], "pending_send")
        self.assertEqual(fake_supabase.inserted_payload["cake_status"], "pending_pickup")
        self.assertTrue(
            datetime.fromisoformat(fake_supabase.inserted_payload["updated_at"]).tzinfo
            is not None,
        )

    def test_rsvp_requesting_cake_requires_explicit_shipping_contact(self):
        fake_supabase = FakeSupabase()

        with patch("app.routers.rsvp.get_supabase", return_value=fake_supabase):
            response = self.client.post(
                "/api/rsvp",
                json={
                    "name": "Peiyu",
                    "phone": "0912-345-678",
                    "status": "decline",
                    "decline_response": "request_cake",
                    "shipping_address": "New Taipei",
                },
            )

        self.assertEqual(response.status_code, 422)
        messages = [item["msg"] for item in response.json()["detail"]]
        self.assertTrue(
            any("希望收到喜餅時請填寫收件人" in message for message in messages),
        )
        self.assertIsNone(fake_supabase.inserted_payload)
        self.assertIsNone(fake_supabase.updated_payload)

    def test_rsvp_rate_limit_rejects_repeated_submissions_for_same_phone(self):
        fake_supabase = FakeSupabase()

        with (
            patch("app.routers.rsvp.get_supabase", return_value=fake_supabase),
            patch("app.routers.rsvp._create_unique_checkin_token", return_value="token-123"),
        ):
            for _index in range(5):
                response = self.client.post(
                    "/api/rsvp",
                    json={
                        "name": "Oscar",
                        "phone": "0912-345-678",
                        "status": "attend",
                        "total_adults": 1,
                    },
                )
                self.assertEqual(response.status_code, 200)

            response = self.client.post(
                "/api/rsvp",
                json={
                    "name": "Oscar",
                    "phone": "0912-345-678",
                    "status": "attend",
                    "total_adults": 1,
                },
            )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(
            response.json()["detail"],
            "Too many requests. Please try again later.",
        )
        self.assertIn("retry-after", response.headers)

    def test_public_rsvp_settings_returns_configured_deadline(self):
        fake_supabase = FakeSupabase(
            table_data={
                "wedding_settings": [
                    {
                        "id": 1,
                        "rsvp_deadline": "2026-10-04",
                        "updated_at": "2026-07-17T00:00:00+00:00",
                    },
                ],
            },
        )

        with patch("app.routers.rsvp.get_supabase", return_value=fake_supabase):
            response = self.client.get("/api/rsvp/settings")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["rsvp_deadline"], "2026-10-04")

    def test_admin_guest_list_retries_transient_supabase_read_errors(self):
        request = httpx.Request(
            "GET",
            "https://example.supabase.co/rest/v1/guests",
        )
        fake_supabase = FakeSupabase(
            select_data=[guest_record()],
            select_errors=[
                httpx.ReadError("temporarily unavailable", request=request),
                httpx.ReadError("temporarily unavailable", request=request),
            ],
        )

        with (
            patch("app.routers.admin.get_supabase", return_value=fake_supabase),
            patch("app.database.time.sleep") as sleep,
        ):
            response = self.client.get("/api/admin/guests")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["id"], GUEST_ID)
        self.assertEqual(fake_supabase.select_execute_calls, 3)
        self.assertEqual(
            [call.args[0] for call in sleep.call_args_list],
            [0.1, 0.2],
        )

    def test_admin_guest_list_returns_paginated_result(self):
        guests = [
            guest_record(id=f"00000000-0000-4000-8000-00000000000{index}", name=f"Guest {index}")
            for index in range(1, 6)
        ]
        fake_supabase = FakeSupabase(select_data=guests)

        with patch("app.routers.admin.get_supabase", return_value=fake_supabase):
            response = self.client.get("/api/admin/guests?page=2&page_size=2")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "items": [
                    {**guest_record(id="00000000-0000-4000-8000-000000000003", name="Guest 3"), "gift_amount": "0"},
                    {**guest_record(id="00000000-0000-4000-8000-000000000004", name="Guest 4"), "gift_amount": "0"},
                ],
                "total": 5,
                "page": 2,
                "page_size": 2,
            },
        )
        self.assertEqual(fake_supabase.last_range_args, (2, 3))
        self.assertEqual(fake_supabase.last_select_count, "exact")

    def test_admin_guest_list_accepts_created_at_desc_order(self):
        fake_supabase = FakeSupabase(select_data=[guest_record()])

        with patch("app.routers.admin.get_supabase", return_value=fake_supabase):
            response = self.client.get("/api/admin/guests?sort=created_at&order=desc")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(fake_supabase.last_order_args, ("created_at",))
        self.assertEqual(fake_supabase.last_order_kwargs, {"desc": True})

    def test_admin_can_update_rsvp_deadline(self):
        fake_supabase = FakeSupabase()

        with patch("app.routers.admin.get_supabase", return_value=fake_supabase):
            response = self.client.put(
                "/api/admin/settings/rsvp",
                json={"rsvp_deadline": "2026-10-04"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["rsvp_deadline"], "2026-10-04")
        self.assertEqual(fake_supabase.upserted_payload["id"], 1)
        self.assertEqual(
            fake_supabase.upserted_payload["rsvp_deadline"],
            "2026-10-04",
        )
        self.assertEqual(fake_supabase.upsert_conflict, "id")

    def test_checkin_update_marks_attending_guest_arrived(self):
        existing = guest_record(status="attend", arrived_at=None)
        fake_supabase = FakeSupabase(select_data=[existing], update_base=existing)

        with patch("app.routers.admin.get_supabase", return_value=fake_supabase):
            response = self.client.patch(
                f"/api/admin/guests/{GUEST_ID}/checkin",
                json={
                    "is_arrived": True,
                    "actual_adults": 2,
                    "actual_children": 1,
                    "checkin_note": "  front desk  ",
                    "gift_amount": "3600",
                    "admin_notes": "  received by front desk  ",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(fake_supabase.updated_payload["is_arrived"])
        self.assertEqual(fake_supabase.updated_payload["actual_adults"], 2)
        self.assertEqual(fake_supabase.updated_payload["actual_children"], 1)
        self.assertEqual(fake_supabase.updated_payload["checkin_note"], "front desk")
        self.assertEqual(fake_supabase.updated_payload["gift_amount"], "3600")
        self.assertEqual(fake_supabase.updated_payload["admin_notes"], "received by front desk")
        self.assertIn("arrived_at", fake_supabase.updated_payload)
        self.assertIn("checkin_updated_at", fake_supabase.updated_payload)

    def test_checkin_update_allows_arrival_when_existing_table_is_over_capacity(self):
        existing = guest_record(
            id=GUEST_ID,
            name="Existing Guest",
            total_adults=2,
            total_children=0,
            allocated_table="第 1 桌",
            arrived_at=None,
        )
        seated_guest = guest_record(
            id="00000000-0000-4000-8000-000000000002",
            name="Already Seated",
            total_adults=11,
            total_children=0,
            allocated_table="第 1 桌",
        )
        fake_supabase = FakeSupabase(
            table_data={
                "guests": [existing, seated_guest],
                "table_settings": [
                    {"table_name": "第 1 桌", "capacity": 12},
                ],
            },
            update_base=existing,
        )

        with patch("app.routers.admin.get_supabase", return_value=fake_supabase):
            response = self.client.patch(
                f"/api/admin/guests/{GUEST_ID}/checkin",
                json={
                    "is_arrived": True,
                    "actual_adults": 2,
                    "actual_children": 0,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(fake_supabase.updated_payload["is_arrived"])
        self.assertEqual(fake_supabase.updated_payload["actual_adults"], 2)

    def test_checkin_update_clears_actual_counts_when_arrival_is_cancelled(self):
        existing = guest_record(
            status="attend",
            is_arrived=True,
            arrived_at="2026-07-17T12:00:00+00:00",
            actual_adults=2,
            actual_children=1,
        )
        fake_supabase = FakeSupabase(select_data=[existing], update_base=existing)

        with patch("app.routers.admin.get_supabase", return_value=fake_supabase):
            response = self.client.patch(
                f"/api/admin/guests/{GUEST_ID}/checkin",
                json={
                    "is_arrived": False,
                    "actual_adults": 0,
                    "actual_children": 0,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(fake_supabase.updated_payload["is_arrived"])
        self.assertIsNone(fake_supabase.updated_payload["arrived_at"])
        self.assertIsNone(fake_supabase.updated_payload["actual_adults"])
        self.assertIsNone(fake_supabase.updated_payload["actual_children"])

    def test_checkin_update_rejects_table_assignment_when_capacity_would_be_exceeded(self):
        target_guest = guest_record(
            id=GUEST_ID,
            name="Two Seat Guest",
            total_adults=2,
            total_children=0,
            allocated_table=None,
        )
        seated_guest = guest_record(
            id="00000000-0000-4000-8000-000000000002",
            name="Already Seated",
            total_adults=11,
            total_children=0,
            allocated_table="第 1 桌",
        )
        fake_supabase = FakeSupabase(
            table_data={
                "guests": [target_guest, seated_guest],
                "table_settings": [
                    {"table_name": "第 1 桌", "capacity": 12},
                ],
            },
            update_base=target_guest,
        )

        with patch("app.routers.admin.get_supabase", return_value=fake_supabase):
            response = self.client.patch(
                f"/api/admin/guests/{GUEST_ID}/checkin",
                json={"allocated_table": "第 1 桌"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"],
            "第 1 桌 剩餘 1 位，無法安排 Two Seat Guest（2 位）。",
        )
        self.assertIsNone(fake_supabase.updated_payload)

    def test_guest_update_rejects_table_assignment_when_capacity_would_be_exceeded(self):
        target_guest = guest_record(
            id=GUEST_ID,
            name="Two Seat Guest",
            total_adults=2,
            total_children=0,
            allocated_table=None,
        )
        seated_guest = guest_record(
            id="00000000-0000-4000-8000-000000000002",
            name="Already Seated",
            total_adults=11,
            total_children=0,
            allocated_table="第 1 桌",
        )
        fake_supabase = FakeSupabase(
            table_data={
                "guests": [target_guest, seated_guest],
                "table_settings": [
                    {"table_name": "第 1 桌", "capacity": 12},
                ],
            },
            update_base=target_guest,
        )

        with patch("app.routers.admin.get_supabase", return_value=fake_supabase):
            response = self.client.patch(
                f"/api/admin/guests/{GUEST_ID}",
                json={"allocated_table": "第 1 桌"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"],
            "第 1 桌 剩餘 1 位，無法安排 Two Seat Guest（2 位）。",
        )
        self.assertIsNone(fake_supabase.updated_payload)

    def test_guest_update_allows_table_assignment_when_capacity_has_room(self):
        target_guest = guest_record(
            id=GUEST_ID,
            name="One Seat Guest",
            total_adults=1,
            total_children=0,
            allocated_table=None,
        )
        seated_guest = guest_record(
            id="00000000-0000-4000-8000-000000000002",
            name="Already Seated",
            total_adults=11,
            total_children=0,
            allocated_table="第 1 桌",
        )
        fake_supabase = FakeSupabase(
            table_data={
                "guests": [target_guest, seated_guest],
                "table_settings": [
                    {"table_name": "第 1 桌", "capacity": 12},
                ],
            },
            update_base=target_guest,
        )

        with patch("app.routers.admin.get_supabase", return_value=fake_supabase):
            response = self.client.patch(
                f"/api/admin/guests/{GUEST_ID}",
                json={"allocated_table": "第 1 桌"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(fake_supabase.updated_payload["allocated_table"], "第 1 桌")

    def test_guest_update_uses_planned_counts_not_actual_counts_for_capacity(self):
        target_guest = guest_record(
            id=GUEST_ID,
            name="Planned Four Seat Guest",
            total_adults=4,
            total_children=0,
            actual_adults=1,
            actual_children=0,
            allocated_table=None,
        )
        seated_guest = guest_record(
            id="00000000-0000-4000-8000-000000000002",
            name="Already Seated",
            total_adults=9,
            total_children=0,
            actual_adults=9,
            actual_children=0,
            allocated_table="第 1 桌",
        )
        fake_supabase = FakeSupabase(
            table_data={
                "guests": [target_guest, seated_guest],
                "table_settings": [
                    {"table_name": "第 1 桌", "capacity": 12},
                ],
            },
            update_base=target_guest,
        )

        with patch("app.routers.admin.get_supabase", return_value=fake_supabase):
            response = self.client.patch(
                f"/api/admin/guests/{GUEST_ID}",
                json={"allocated_table": "第 1 桌"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"],
            "第 1 桌 剩餘 3 位，無法安排 Planned Four Seat Guest（4 位）。",
        )
        self.assertIsNone(fake_supabase.updated_payload)

    def test_checkin_update_rejects_declined_guest_arrival(self):
        existing = guest_record(status="decline", decline_response="blessing_only")
        fake_supabase = FakeSupabase(select_data=[existing], update_base=existing)

        with patch("app.routers.admin.get_supabase", return_value=fake_supabase):
            response = self.client.patch(
                f"/api/admin/guests/{GUEST_ID}/checkin",
                json={"is_arrived": True},
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Only attending guests can be checked in")
        self.assertIsNone(fake_supabase.updated_payload)


if __name__ == "__main__":
    unittest.main()
