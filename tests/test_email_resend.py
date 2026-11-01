import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

from app.auth import require_admin
from app.main import app
from app.routers import emails
from app.schemas.email import WeddingEmailContent


class EmailResendTest(unittest.TestCase):
    def setUp(self):
        self.guest = {'id': str(uuid4()), 'name': 'Synthetic Guest', 'email': 'corrected@example.com',
                      'checkin_token': 'synthetic-qr', 'allocated_table': '好友桌', 'table_number': 8}
        self.previous = {'id': str(uuid4()), 'guest_id': self.guest['id'], 'batch_id': str(uuid4()),
                         'name': 'Synthetic Guest', 'email': 'typo@example.com', 'status': 'sent',
                         'attempted_at': '2026-11-01T00:00:00+00:00', 'sent_at': '2026-11-01T00:00:01+00:00',
                         'superseded_at': None}
        self.payload = {'guest_ids': [self.guest['id']], 'preview_guest_id': self.guest['id'],
                        'content': WeddingEmailContent().model_dump(), 'resend_delivery_id': self.previous['id']}
        self.db = MagicMock()
        self.query = self.db.table.return_value
        for method in ('select', 'eq', 'limit', 'is_', 'order', 'range'):
            getattr(self.query, method).return_value = self.query
        self.query.execute.return_value.data = [self.previous]
        self.client = TestClient(app)
        app.dependency_overrides[require_admin] = lambda: {'username': 'admin', 'role': 'admin'}
        self.addCleanup(app.dependency_overrides.clear)
        for target, kwargs in [('_load_guests', {'return_value': [self.guest]}), ('get_supabase', {'return_value': self.db}),
                               ('smtp_ready', {'return_value': True}), ('send_message', {}), ('_run_batch', {})]:
            patched = patch.object(emails, target, **kwargs)
            mocked = patched.start()
            self.addCleanup(patched.stop)
            if target == '_run_batch': self.worker = mocked
            if target == 'send_message': self.smtp = mocked

    def preview(self, payload=None):
        return self.client.post('/api/admin/email/preview', json=payload or self.payload)

    def confirmation(self):
        response = self.preview()
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        return {**self.payload, 'batch_id': data['batch_id'], 'preview_token': data['preview_token'], 'resend_confirmed': True}

    def test_resend_preview_uses_current_address_seating_and_qr_without_sending_or_writing(self):
        response = self.preview()
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['recipient']['email'], 'corrected@example.com')
        self.assertEqual(data['resend'], {'previous_email': 'typo@example.com'})
        self.assertIn('第 8 桌｜好友桌', data['html'])
        self.assertIn('data:image/png;base64,', data['html'])
        self.db.rpc.assert_not_called()
        self.worker.assert_not_called()
        self.smtp.assert_not_called()
        self.query.eq.assert_any_call('guest_id', self.guest['id'])

    def test_missing_false_or_coerced_confirmation_cannot_enqueue(self):
        payload = self.confirmation()
        for value in (None, False, 'true', 1):
            body = {**payload, 'resend_confirmed': value}
            if value is None: body.pop('resend_confirmed')
            self.assertEqual(self.client.post('/api/admin/email/send', json=body).status_code, 422)
        self.db.rpc.assert_not_called()
        self.worker.assert_not_called()

    def test_confirmation_enqueues_new_delivery_via_atomic_resend_rpc(self):
        payload = self.confirmation()
        result = self.client.post('/api/admin/email/send', json=payload)
        self.assertEqual(result.status_code, 202)
        name, params = self.db.rpc.call_args.args
        self.assertEqual(name, 'enqueue_wedding_email_resend')
        self.assertEqual(params['p_guest']['email'], 'corrected@example.com')
        self.assertEqual(params['p_content']['seating_by_guest'][self.guest['id']]['table_number'], 8)
        self.assertEqual(params['p_previous']['email'], 'typo@example.com')
        self.assertEqual(params['p_content']['resend_of'], params['p_previous'])
        self.worker.assert_called_once_with(payload['batch_id'])
        self.smtp.assert_not_called()
        self.query.update.assert_not_called()

    def test_accepted_same_address_can_be_explicitly_resent(self):
        self.guest['email'] = 'typo@example.com'
        result = self.client.post('/api/admin/email/send', json=self.confirmation())
        self.assertEqual(result.status_code, 202)
        self.assertEqual(self.db.rpc.call_args.args[1]['p_guest']['email'], 'typo@example.com')

    def test_unknown_inflight_and_superseded_rows_cannot_start_resend_preview(self):
        for status in ('unknown', 'sending', 'pending', 'failed'):
            self.previous['status'] = status
            self.assertEqual(self.preview().status_code, 409)
        self.previous['status'] = 'sent'
        self.previous['superseded_at'] = '2026-11-01T01:00:00+00:00'
        self.assertEqual(self.preview().status_code, 409)
        self.db.rpc.assert_not_called()
        self.worker.assert_not_called()

    def test_resend_requires_one_matching_recipient(self):
        self.assertEqual(self.preview({**self.payload, 'guest_ids': [self.guest['id'], str(uuid4())]}).status_code, 422)
        self.assertEqual(self.preview({**self.payload, 'preview_guest_id': str(uuid4())}).status_code, 422)

    def test_source_snapshot_current_email_and_resend_intent_are_signed(self):
        payload = self.confirmation()
        self.previous['email'] = 'changed-source@example.com'
        self.assertEqual(self.client.post('/api/admin/email/send', json=payload).status_code, 409)
        self.previous['email'] = 'typo@example.com'
        self.guest['email'] = 'another-correction@example.com'
        self.assertEqual(self.client.post('/api/admin/email/send', json=payload).status_code, 409)
        self.guest['email'] = 'corrected@example.com'
        without_intent = {**payload, 'resend_delivery_id': None}
        self.assertEqual(self.client.post('/api/admin/email/send', json=without_intent).status_code, 409)
        regular = self.preview({**self.payload, 'resend_delivery_id': None}).json()
        bypass = {**payload, 'batch_id': regular['batch_id'], 'preview_token': regular['preview_token']}
        self.assertEqual(self.client.post('/api/admin/email/send', json=bypass).status_code, 409)
        self.db.rpc.assert_not_called()
        self.worker.assert_not_called()

    def test_superseded_source_can_only_replay_its_already_reviewed_confirmation(self):
        payload = self.confirmation()
        self.previous['superseded_at'] = '2026-11-01T01:00:00+00:00'
        self.assertEqual(self.preview().status_code, 409)
        # The RPC, not an unchecked row update, determines whether this is the same completed confirmation.
        self.assertEqual(self.client.post('/api/admin/email/send', json=payload).status_code, 202)
        self.assertEqual(self.db.rpc.call_args.args[1]['p_id'], payload['batch_id'])

    def test_database_race_returns_conflict_and_does_not_start_worker(self):
        payload = self.confirmation()
        self.db.rpc.return_value.execute.side_effect = APIError({'code': 'P0001', 'message': 'Delivery changed'})
        self.assertEqual(self.client.post('/api/admin/email/send', json=payload).status_code, 409)
        self.worker.assert_not_called()

    def test_committed_but_lost_confirmation_retries_the_same_batch_id(self):
        payload = self.confirmation()
        self.db.rpc.return_value.execute.side_effect = [httpx.ReadError('lost response'), MagicMock()]
        with patch('app.database.time.sleep'):
            self.assertEqual(self.client.post('/api/admin/email/send', json=payload).status_code, 202)
        self.assertEqual(self.db.rpc.return_value.execute.call_count, 2)
        self.db.rpc.assert_called_once()
        self.worker.assert_called_once_with(payload['batch_id'])
