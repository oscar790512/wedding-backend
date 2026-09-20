import base64
import hashlib
import hmac
import json
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


def line_signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


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

    def delete(self):
        self.operation = "delete"
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

    def like(self, field, value):
        self.filters.append(("like", field, value))
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
                elif operation == "like":
                    needle = value.strip("%")
                    if value.startswith("%") and not value.endswith("%"):
                        data = [row for row in data if str(row.get(field) or "").endswith(needle)]
                    elif value.endswith("%") and not value.startswith("%"):
                        data = [row for row in data if str(row.get(field) or "").startswith(needle)]
                    else:
                        data = [row for row in data if needle in str(row.get(field) or "")]
            total = len(data)
            if self.range_args is not None:
                start, end = self.range_args
                data = data[start : end + 1]
                self.supabase.last_range_args = self.range_args
            self.supabase.last_select_count = self.count
            return SimpleNamespace(data=data, count=total)
        if self.operation == "insert":
            self.supabase.inserted_payload = deepcopy(self.payload)
            if self.table_name != "guests":
                data = deepcopy(self.payload)
                return SimpleNamespace(data=data if isinstance(data, list) else [data])
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
        if self.operation == "delete":
            self.supabase.deleted_table = self.table_name
            self.supabase.delete_filters = deepcopy(self.filters)
            return SimpleNamespace(data=[])
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
        self.deleted_table = None
        self.delete_filters = None
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
        import app.routers.rsvp as rsvp_router

        rsvp_router.line_lookup_sessions.clear()

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

    def test_seat_video_lookup_returns_guest_and_table_video_by_phone_last5(self):
        fake_supabase = FakeSupabase(
            table_data={
                "guests": [
                    guest_record(
                        name="王小明",
                        phone="0912345678",
                        total_adults=2,
                        total_children=1,
                        allocated_table="第 3 桌",
                    ),
                ],
            },
        )

        with patch("app.routers.rsvp.get_supabase", return_value=fake_supabase):
            response = self.client.post(
                "/api/seat-video-lookup",
                json={"phone_last5": "45678"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "guest": {
                    "name": "王小明",
                    "total_adults": 2,
                    "total_children": 1,
                    "attendee_count": 3,
                    "allocated_table": "第 3 桌",
                    "phone_last5": "45678",
                },
                "message_text": "王小明，電話後五碼 45678\n桌次：第 3 桌\n出席總人數：3 位",
                "video_filename": "table-03.mp4",
                "video_url": "http://testserver/static/seat-videos/table-03.mp4",
                "preview_image_url": "http://testserver/static/seat-videos/table-03.png",
                "line_video_message": {
                    "type": "video",
                    "originalContentUrl": "http://testserver/static/seat-videos/table-03.mp4",
                    "previewImageUrl": "http://testserver/static/seat-videos/table-03.png",
                },
            },
        )

    def test_seat_video_lookup_maps_system_table_name_from_manifest(self):
        fake_supabase = FakeSupabase(
            table_data={
                "guests": [
                    guest_record(
                        name="王小明",
                        phone="0912345678",
                        total_adults=2,
                        total_children=1,
                        allocated_table="男方同事6",
                    ),
                ],
            },
        )

        with patch("app.routers.rsvp.get_supabase", return_value=fake_supabase):
            response = self.client.post(
                "/api/seat-video-lookup",
                json={"phone_last5": "45678"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["guest"]["allocated_table"], "男方同事6")
        self.assertEqual(response.json()["video_filename"], "table-25.mp4")
        self.assertEqual(
            response.json()["line_video_message"],
            {
                "type": "video",
                "originalContentUrl": "http://testserver/static/seat-videos/table-25.mp4",
                "previewImageUrl": "http://testserver/static/seat-videos/table-25.png",
            },
        )

    def test_seat_video_lookup_uses_fixed_video_key_after_table_rename(self):
        fake_supabase = FakeSupabase(
            table_data={
                "guests": [
                    guest_record(
                        name="王小明",
                        phone="0912345678",
                        total_adults=2,
                        total_children=1,
                        allocated_table="男方公司同事",
                    ),
                ],
                "table_settings": [
                    {
                        "table_name": "男方公司同事",
                        "capacity": 12,
                        "seat_video_key": "table-25",
                    },
                ],
            },
        )

        with patch("app.routers.rsvp.get_supabase", return_value=fake_supabase):
            response = self.client.post(
                "/api/seat-video-lookup",
                json={"phone_last5": "45678"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["guest"]["allocated_table"], "男方公司同事")
        self.assertEqual(response.json()["video_filename"], "table-25.mp4")
        self.assertEqual(
            response.json()["message_text"],
            "王小明，電話後五碼 45678\n桌次：男方公司同事\n出席總人數：3 位",
        )

    def test_seat_video_lookup_uses_floor_slot_when_fixed_video_key_is_missing(self):
        fake_supabase = FakeSupabase(
            table_data={
                "guests": [
                    guest_record(
                        name="王小明",
                        phone="0912345678",
                        total_adults=2,
                        total_children=1,
                        allocated_table="男方公司同事",
                    ),
                ],
                "table_settings": [
                    {
                        "table_name": "男方公司同事",
                        "capacity": 12,
                        "seat_video_key": None,
                    },
                ],
                "table_layout_slots": [
                    {
                        "layout_name": "default",
                        "column_index": 3,
                        "position_index": 6,
                        "table_name": "男方公司同事",
                    },
                ],
            },
        )

        with patch("app.routers.rsvp.get_supabase", return_value=fake_supabase):
            response = self.client.post(
                "/api/seat-video-lookup",
                json={"phone_last5": "45678"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["guest"]["allocated_table"], "男方公司同事")
        self.assertEqual(response.json()["video_filename"], "table-25.mp4")

    def test_seat_video_lookup_returns_unassigned_guest_without_video(self):
        fake_supabase = FakeSupabase(
            table_data={
                "guests": [
                    guest_record(
                        name="王小明",
                        phone="0912345678",
                        total_adults=2,
                        total_children=1,
                        allocated_table=None,
                    ),
                ],
            },
        )

        with patch("app.routers.rsvp.get_supabase", return_value=fake_supabase):
            response = self.client.post(
                "/api/seat-video-lookup",
                json={"phone_last5": "45678"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["guest"]["allocated_table"], None)
        self.assertEqual(
            response.json()["message_text"],
            "王小明，電話後五碼 45678\n桌次：座位安排中，請洽現場工作人員\n出席總人數：3 位",
        )
        self.assertIsNone(response.json()["video_filename"])
        self.assertIsNone(response.json()["line_video_message"])

    def test_seat_video_lookup_rejects_unknown_phone_last5(self):
        fake_supabase = FakeSupabase(table_data={"guests": []})

        with patch("app.routers.rsvp.get_supabase", return_value=fake_supabase):
            response = self.client.post(
                "/api/seat-video-lookup",
                json={"phone_last5": "99999"},
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json()["detail"],
            (
                "找不到出席資料，可能原因有：\n\n"
                "1. 請再次確認，後五碼為您在登記問卷上填寫的電話後五碼\n"
                "2. 若您為隨席賓客，請輸入登記人電話後五碼"
                "（舉例：登記人為王小明，隨席 5 位，您可能為其中一位隨席賓客）\n\n"
                "若以上無法查詢到，請洽詢兩位新人或您已知的工作人員"
            ),
        )

    def test_seat_video_lookup_rejects_duplicate_phone_last5_matches(self):
        fake_supabase = FakeSupabase(
            table_data={
                "guests": [
                    guest_record(id="00000000-0000-4000-8000-000000000001", phone="0912345678"),
                    guest_record(id="00000000-0000-4000-8000-000000000002", phone="0987645678"),
                ],
            },
        )

        with patch("app.routers.rsvp.get_supabase", return_value=fake_supabase):
            response = self.client.post(
                "/api/seat-video-lookup",
                json={"phone_last5": "45678"},
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["detail"],
            "查到多筆資料，請洽現場工作人員協助確認座位",
        )

    def test_seat_video_lookup_rate_limits_phone_last5(self):
        fake_supabase = FakeSupabase(
            table_data={
                "guests": [
                    guest_record(
                        name="王小明",
                        phone="0912345678",
                        total_adults=2,
                        total_children=1,
                        allocated_table="第 3 桌",
                    ),
                ],
            },
        )

        with patch("app.routers.rsvp.get_supabase", return_value=fake_supabase):
            responses = [
                self.client.post(
                    "/api/seat-video-lookup",
                    json={"phone_last5": "45678"},
                )
                for _ in range(11)
            ]

        self.assertEqual([response.status_code for response in responses[:10]], [200] * 10)
        self.assertEqual(responses[10].status_code, 429)
        self.assertEqual(responses[10].json()["detail"], "查詢太頻繁，請稍後再試。")
        self.assertIn("retry-after", responses[10].headers)

    def test_line_webhook_replies_with_seat_text_and_video_for_keyword_and_phone_last5(self):
        fake_supabase = FakeSupabase(
            table_data={
                "guests": [
                    guest_record(
                        name="王小明",
                        phone="0912345678",
                        total_adults=2,
                        total_children=1,
                        allocated_table="第 3 桌",
                    ),
                ],
            },
        )
        body = json.dumps(
            {
                "events": [
                    {
                        "replyToken": "reply-token",
                        "source": {"type": "user", "userId": "user-1"},
                        "message": {
                            "type": "text",
                            "text": "我坐哪啊？ 45678",
                        },
                    },
                ],
            },
            separators=(",", ":"),
        ).encode("utf-8")

        with (
            patch("app.routers.rsvp.get_supabase", return_value=fake_supabase),
            patch("app.routers.rsvp.settings.line_channel_secret", "secret"),
            patch("app.routers.rsvp._reply_line_message") as reply,
        ):
            response = self.client.post(
                "/api/line/webhook",
                content=body,
                headers={"x-line-signature": line_signature(body, "secret")},
            )

        self.assertEqual(response.status_code, 200)
        reply.assert_called_once_with(
            "reply-token",
            [
                {
                    "type": "text",
                    "text": "王小明，電話後五碼 45678\n桌次：第 3 桌\n出席總人數：3 位",
                },
                {
                    "type": "video",
                    "originalContentUrl": "http://testserver/static/seat-videos/table-03.mp4",
                    "previewImageUrl": "http://testserver/static/seat-videos/table-03.png",
                },
            ],
        )

    def test_line_webhook_rate_limits_repeated_phone_last5_lookups(self):
        fake_supabase = FakeSupabase(
            table_data={
                "guests": [
                    guest_record(
                        name="王小明",
                        phone="0912345678",
                        total_adults=2,
                        total_children=1,
                        allocated_table="第 3 桌",
                    ),
                ],
            },
        )
        body = json.dumps(
            {
                "events": [
                    {
                        "replyToken": "reply-token",
                        "source": {"type": "user", "userId": "user-1"},
                        "message": {
                            "type": "text",
                            "text": "我坐哪啊？ 45678",
                        },
                    },
                ],
            },
            separators=(",", ":"),
        ).encode("utf-8")

        with (
            patch("app.routers.rsvp.get_supabase", return_value=fake_supabase),
            patch("app.routers.rsvp.settings.line_channel_secret", "secret"),
            patch("app.routers.rsvp._reply_line_message") as reply,
        ):
            responses = [
                self.client.post(
                    "/api/line/webhook",
                    content=body,
                    headers={"x-line-signature": line_signature(body, "secret")},
                )
                for _ in range(11)
            ]

        self.assertEqual([response.status_code for response in responses], [200] * 11)
        self.assertEqual(reply.call_count, 11)
        self.assertEqual(
            reply.call_args_list[-1].args,
            ("reply-token", [{"type": "text", "text": "查詢太頻繁，請稍後再試。"}]),
        )

    def test_line_webhook_requires_keyword_before_phone_last5(self):
        body = json.dumps(
            {
                "events": [
                    {
                        "replyToken": "reply-token",
                        "source": {"type": "user", "userId": "user-1"},
                        "message": {
                            "type": "text",
                            "text": "45678",
                        },
                    },
                ],
            },
            separators=(",", ":"),
        ).encode("utf-8")

        with (
            patch("app.routers.rsvp.settings.line_channel_secret", "secret"),
            patch("app.routers.rsvp._reply_line_message") as reply,
        ):
            response = self.client.post(
                "/api/line/webhook",
                content=body,
                headers={"x-line-signature": line_signature(body, "secret")},
            )

        self.assertEqual(response.status_code, 200)
        reply.assert_called_once_with(
            "reply-token",
            [{"type": "text", "text": "請先輸入「我坐哪啊？」，再輸入電話後五碼。"}],
        )

    def test_line_webhook_accepts_phone_last5_after_lookup_keyword(self):
        fake_supabase = FakeSupabase(
            table_data={
                "guests": [
                    guest_record(
                        name="王小明",
                        phone="0912345678",
                        total_adults=2,
                        total_children=1,
                        allocated_table="第 3 桌",
                    ),
                ],
            },
        )
        body = json.dumps(
            {
                "events": [
                    {
                        "replyToken": "keyword-token",
                        "source": {"type": "user", "userId": "user-1"},
                        "message": {
                            "type": "text",
                            "text": "我坐哪啊？",
                        },
                    },
                    {
                        "replyToken": "lookup-token",
                        "source": {"type": "user", "userId": "user-1"},
                        "message": {
                            "type": "text",
                            "text": "45678",
                        },
                    },
                ],
            },
            separators=(",", ":"),
        ).encode("utf-8")

        with (
            patch("app.routers.rsvp.get_supabase", return_value=fake_supabase),
            patch("app.routers.rsvp.settings.line_channel_secret", "secret"),
            patch("app.routers.rsvp._reply_line_message") as reply,
        ):
            response = self.client.post(
                "/api/line/webhook",
                content=body,
                headers={"x-line-signature": line_signature(body, "secret")},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(reply.call_args_list[0].args[0], "keyword-token")
        self.assertEqual(
            reply.call_args_list[0].args[1],
            [{"type": "text", "text": "請輸入電話後五碼，例如：45678"}],
        )
        self.assertEqual(reply.call_args_list[1].args[0], "lookup-token")
        self.assertEqual(
            reply.call_args_list[1].args[1][0],
            {
                "type": "text",
                "text": "王小明，電話後五碼 45678\n桌次：第 3 桌\n出席總人數：3 位",
            },
        )
        self.assertEqual(
            reply.call_args_list[1].args[1][1],
            {
                "type": "video",
                "originalContentUrl": "http://testserver/static/seat-videos/table-03.mp4",
                "previewImageUrl": "http://testserver/static/seat-videos/table-03.png",
            },
        )

    def test_line_webhook_rejects_invalid_signature(self):
        body = b'{"events":[]}'

        with patch("app.routers.rsvp.settings.line_channel_secret", "secret"):
            response = self.client.post(
                "/api/line/webhook",
                content=body,
                headers={"x-line-signature": "bad-signature"},
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "Invalid LINE signature")

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

    def test_table_layout_returns_slots_and_unplaced_tables(self):
        fake_supabase = FakeSupabase(
            table_data={
                "table_settings": [
                    {"table_name": "主桌", "capacity": 12},
                    {"table_name": "第 1 桌", "capacity": 12},
                    {"table_name": "第 2 桌", "capacity": 12},
                ],
                "table_layout_slots": [
                    {
                        "id": "slot-1",
                        "layout_name": "default",
                        "column_index": 1,
                        "position_index": 1,
                        "table_name": "第 1 桌",
                    },
                    {
                        "id": "slot-2",
                        "layout_name": "default",
                        "column_index": 1,
                        "position_index": 2,
                        "table_name": None,
                    },
                ],
            },
        )

        with patch("app.routers.admin.get_supabase", return_value=fake_supabase):
            response = self.client.get("/api/admin/table-layout")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [slot["table_name"] for slot in response.json()["slots"]],
            ["第 1 桌", None],
        )
        self.assertEqual(
            [table["table_name"] for table in response.json()["unplaced_tables"]],
            ["第 2 桌"],
        )

    def test_table_layout_replace_rejects_duplicate_table_assignment(self):
        fake_supabase = FakeSupabase()

        with patch("app.routers.admin.get_supabase", return_value=fake_supabase):
            response = self.client.put(
                "/api/admin/table-layout",
                json={
                    "slots": [
                        {"column_index": 1, "position_index": 1, "table_name": "第 1 桌"},
                        {"column_index": 2, "position_index": 1, "table_name": "第 1 桌"},
                    ],
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "同一桌不可安排在多個位置")

    def test_table_layout_replace_rejects_duplicate_position(self):
        fake_supabase = FakeSupabase()

        with patch("app.routers.admin.get_supabase", return_value=fake_supabase):
            response = self.client.put(
                "/api/admin/table-layout",
                json={
                    "slots": [
                        {"column_index": 1, "position_index": 1, "table_name": "第 1 桌"},
                        {"column_index": 1, "position_index": 1, "table_name": "第 2 桌"},
                    ],
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "同一個座位圖位置不可重複")

    def test_table_layout_replace_saves_slots(self):
        fake_supabase = FakeSupabase(
            table_data={
                "table_settings": [
                    {"table_name": "主桌", "capacity": 12},
                    {"table_name": "第 1 桌", "capacity": 12},
                    {"table_name": "第 2 桌", "capacity": 12},
                ],
                "table_layout_slots": [],
            },
        )

        with patch("app.routers.admin.get_supabase", return_value=fake_supabase):
            response = self.client.put(
                "/api/admin/table-layout",
                json={
                    "slots": [
                        {"column_index": 1, "position_index": 1, "table_name": "第 1 桌"},
                        {"column_index": 1, "position_index": 2, "table_name": None},
                    ],
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(fake_supabase.deleted_table, "table_layout_slots")
        self.assertEqual(
            fake_supabase.inserted_payload,
            [
                {
                    "layout_name": "default",
                    "column_index": 1,
                    "position_index": 1,
                    "table_name": "第 1 桌",
                    "updated_at": fake_supabase.inserted_payload[0]["updated_at"],
                },
                {
                    "layout_name": "default",
                    "column_index": 1,
                    "position_index": 2,
                    "table_name": None,
                    "updated_at": fake_supabase.inserted_payload[1]["updated_at"],
                },
            ],
        )


if __name__ == "__main__":
    unittest.main()
