import unittest
from pydantic import ValidationError

from app.schemas.guest import RsvpRequest


class RsvpRequestSchemaTest(unittest.TestCase):
    def test_attending_guest_is_normalized_for_storage(self):
        payload = RsvpRequest(
            name="  Oscar  ",
            phone=" 0912-345-678 ",
            email=" OSCAR@example.COM ",
            status="attend",
            total_adults=2,
            total_children=1,
            vegetarian_count=2,
            child_seats=1,
            need_invitation=True,
            invitation_address="  Taipei City  ",
            allergy_notes="  peanut  ",
            diet_notes="  less spicy  ",
            guest_category="男方朋友/同學",
        )

        self.assertEqual(payload.name, "Oscar")
        self.assertEqual(payload.phone, "0912345678")
        self.assertEqual(payload.email, "oscar@example.com")
        self.assertEqual(payload.invitation_address, "Taipei City")
        self.assertEqual(payload.vegetarian_adults, 2)
        self.assertEqual(payload.vegetarian_children, 0)
        self.assertEqual(payload.cake_status, "pending_pickup")

    def test_declined_guest_requesting_cake_requires_shipping_recipient(self):
        with self.assertRaises(ValidationError) as context:
            RsvpRequest(
                name="Peiyu",
                phone="(02) 2345-6789",
                status="decline",
                decline_response="request_cake",
                shipping_phone="02-2345-6789",
                shipping_address="New Taipei",
            )

        self.assertIn("希望收到喜餅時請填寫收件人", str(context.exception))

    def test_declined_guest_requesting_cake_requires_shipping_phone(self):
        with self.assertRaises(ValidationError) as context:
            RsvpRequest(
                name="Peiyu",
                phone="(02) 2345-6789",
                status="decline",
                decline_response="request_cake",
                shipping_recipient="Peiyu Chen",
                shipping_address="New Taipei",
            )

        self.assertIn("希望收到喜餅時請填寫收件電話", str(context.exception))

    def test_declined_guest_requesting_cake_keeps_shipping_fields(self):
        payload = RsvpRequest(
            name="Peiyu",
            phone="(02) 2345-6789",
            status="decline",
            decline_response="request_cake",
            shipping_recipient="  Peiyu Chen  ",
            shipping_phone=" 02-2345-6789 ",
            shipping_address="  New Taipei  ",
            blessing_message="  congrats  ",
        )

        dumped = payload.model_dump()

        self.assertEqual(dumped["phone"], "0223456789")
        self.assertEqual(dumped["total_adults"], 0)
        self.assertEqual(dumped["total_children"], 0)
        self.assertEqual(dumped["cake_status"], "pending_send")
        self.assertEqual(dumped["shipping_recipient"], "Peiyu Chen")
        self.assertEqual(dumped["shipping_phone"], "0223456789")
        self.assertEqual(dumped["shipping_address"], "New Taipei")
        self.assertNotIn("gift_amount", dumped)
        self.assertNotIn("actual_adults", dumped)

    def test_declined_guest_must_choose_response_type(self):
        with self.assertRaises(ValidationError) as context:
            RsvpRequest(
                name="Guest",
                phone="0912345678",
                status="decline",
            )

        self.assertIn("無法出席時請選擇一個回覆選項", str(context.exception))

    def test_attending_vegetarian_count_cannot_exceed_guest_count(self):
        with self.assertRaises(ValidationError) as context:
            RsvpRequest(
                name="Guest",
                phone="0912345678",
                status="attend",
                total_adults=1,
                total_children=0,
                vegetarian_count=2,
            )

        self.assertIn("素食人數不可超過總出席人數", str(context.exception))


if __name__ == "__main__":
    unittest.main()
