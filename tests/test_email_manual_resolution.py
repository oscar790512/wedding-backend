import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from app.auth import require_admin
from app.main import app
from app.routers import emails


class ManualEmailResolutionTest(unittest.TestCase):
    def setUp(self):
        self.batch_id, self.delivery_id = str(uuid4()), str(uuid4())
        self.attempted_at = datetime.now(timezone.utc).isoformat()
        self.row = {'id': self.delivery_id, 'batch_id': self.batch_id, 'status': 'unknown', 'error': 'delivery interrupted', 'attempted_at': self.attempted_at}
        self.client = TestClient(app)
        app.dependency_overrides[require_admin] = lambda: {'username': 'admin', 'role': 'admin'}

    def tearDown(self):
        app.dependency_overrides.clear()

    @property
    def path(self):
        return f'/api/admin/email/batches/{self.batch_id}/deliveries/{self.delivery_id}/resolve'

    def test_confirmation_is_required_and_cannot_itself_send_an_email(self):
        db = MagicMock()
        query = db.table.return_value
        for method in ['select', 'eq', 'limit', 'update']:
            getattr(query, method).return_value = query
        query.execute.side_effect = [SimpleNamespace(data=[self.row]), SimpleNamespace(data=[{**self.row, 'status': 'failed'}])]
        with patch.object(emails, 'get_supabase', return_value=db), patch.object(emails, 'send_message') as smtp, patch.object(emails, '_run_batch') as worker:
            response = self.client.post(self.path, json={'confirmed_not_sent': True, 'attempted_at': self.attempted_at})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'failed')
        query.eq.assert_any_call('status', 'unknown')
        query.eq.assert_any_call('attempted_at', self.attempted_at)
        query.eq.assert_any_call('batch_id', self.batch_id)
        smtp.assert_not_called()
        worker.assert_not_called()

    def test_false_missing_or_non_boolean_confirmation_is_rejected(self):
        with patch.object(emails, 'get_supabase') as db:
            for value in [False, 'true', 1, None]:
                response = self.client.post(self.path, json={'confirmed_not_sent': value, 'attempted_at': self.attempted_at})
                self.assertEqual(response.status_code, 422)
            db.assert_not_called()

    def mock_database(self, outcomes):
        db = MagicMock()
        query = db.table.return_value
        for method in ['select', 'eq', 'limit', 'update']:
            getattr(query, method).return_value = query
        query.execute.side_effect = [SimpleNamespace(data=rows) for rows in outcomes]
        return db, query

    def test_sent_sending_pending_and_other_failures_cannot_be_reclassified(self):
        for state in ['sent', 'sending', 'pending', 'failed']:
            db, query = self.mock_database([[{**self.row, 'status': state}]])
            with patch.object(emails, 'get_supabase', return_value=db):
                response = self.client.post(self.path, json={'confirmed_not_sent': True, 'attempted_at': self.attempted_at})
            self.assertEqual(response.status_code, 409)
            query.update.assert_not_called()

    def test_an_old_confirmation_cannot_reset_a_new_attempt(self):
        from datetime import timedelta
        newer = (datetime.fromisoformat(self.attempted_at)+timedelta(minutes=1)).isoformat()
        db, query = self.mock_database([[{**self.row, 'attempted_at': newer}]])
        with patch.object(emails, 'get_supabase', return_value=db):
            response = self.client.post(self.path, json={'confirmed_not_sent': True, 'attempted_at': self.attempted_at})
        self.assertEqual(response.status_code, 409)
        query.update.assert_not_called()

    def test_a_racing_status_change_is_not_overwritten(self):
        db, query = self.mock_database([[self.row], [], [{**self.row, 'status': 'sent'}]])
        with patch.object(emails, 'get_supabase', return_value=db), patch.object(emails, 'send_message') as smtp:
            response = self.client.post(self.path, json={'confirmed_not_sent': True, 'attempted_at': self.attempted_at})
        self.assertEqual(response.status_code, 409)
        smtp.assert_not_called()
        query.eq.assert_any_call('status', 'unknown')

    def test_replaying_a_confirmed_attempt_is_idempotent(self):
        db, query = self.mock_database([[{**self.row, 'status': 'failed', 'error': emails.MANUAL_NOT_SENT_ERROR}]])
        with patch.object(emails, 'get_supabase', return_value=db):
            response = self.client.post(self.path, json={'confirmed_not_sent': True, 'attempted_at': self.attempted_at})
        self.assertEqual(response.status_code, 200)
        query.update.assert_not_called()

    def test_lost_update_response_can_recover_without_sending(self):
        import httpx
        db, query = self.mock_database([])
        query.execute.side_effect = [SimpleNamespace(data=[self.row]), httpx.ReadError('temporary disconnect'), SimpleNamespace(data=[]), SimpleNamespace(data=[{**self.row, 'status': 'failed', 'error': emails.MANUAL_NOT_SENT_ERROR}])]
        with patch.object(emails, 'get_supabase', return_value=db), patch('app.database.time.sleep'), patch.object(emails, '_run_batch') as worker:
            response = self.client.post(self.path, json={'confirmed_not_sent': True, 'attempted_at': self.attempted_at})
        self.assertEqual(response.status_code, 200)
        worker.assert_not_called()

    def test_staff_cannot_confirm_deliveries(self):
        from app.auth import get_current_admin
        app.dependency_overrides.pop(require_admin)
        app.dependency_overrides[get_current_admin] = lambda: {'username': 'staff', 'role': 'staff'}
        with patch.object(emails, 'get_supabase') as db:
            response = self.client.post(self.path, json={'confirmed_not_sent': True, 'attempted_at': self.attempted_at})
        self.assertEqual(response.status_code, 403)
        db.assert_not_called()
