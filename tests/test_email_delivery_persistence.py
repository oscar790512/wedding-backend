import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx

from app.config import settings
from app.routers import emails
from app.schemas.email import WeddingEmailContent


class EmailDeliveryPersistenceTest(unittest.TestCase):
    def test_transient_write_error_after_smtp_acceptance_retries_only_the_result_write(self):
        batch_id = str(uuid4())
        guest = {'id': str(uuid4()), 'name': 'Guest', 'email': 'guest@example.com', 'checkin_token': 'qr-token'}
        delivery = {**guest, 'id': str(uuid4()), 'guest_id': guest['id'], 'status': 'sending'}
        db = MagicMock()
        state = {'status': 'sending'}
        writes = []
        calls = 0

        def table(name):
            query = MagicMock()
            def update(payload):
                def execute():
                    nonlocal calls
                    if name == 'wedding_email_deliveries':
                        writes.append(payload)
                        calls += 1
                        if calls == 1:
                            raise httpx.ReadError('Resource temporarily unavailable')
                        state.update(payload)
                    return SimpleNamespace(data=[])
                query.execute.side_effect = execute
                return query
            query.update.side_effect = update
            query.eq.return_value = query
            return query

        db.table.side_effect = table
        db.rpc.return_value.execute.side_effect = [
            SimpleNamespace(data={'state': 'claimed', 'delivery': delivery}),
            SimpleNamespace(data={'state': 'empty'}),
        ]
        with patch.object(emails, 'get_supabase', return_value=db), patch.object(emails, '_batch', return_value={'sender': settings.smtp_username.lower(), 'content': WeddingEmailContent().model_dump()}), patch.object(emails, '_load_guests', return_value=[guest]), patch.object(emails, 'send_message') as smtp, patch('app.database.time.sleep'):
            emails._run_batch(batch_id)
        self.assertEqual(state['status'], 'sent', 'SMTP accepted but delivery remained sending after a transient database failure')
        smtp.assert_called_once()
        self.assertEqual(len(writes), 2)

    def test_lost_claim_response_retries_the_same_reservation_before_smtp(self):
        batch_id = str(uuid4())
        guest = {'id': str(uuid4()), 'name': 'Guest', 'email': 'guest@example.com', 'checkin_token': 'qr-token'}
        delivery = {**guest, 'id': str(uuid4()), 'guest_id': guest['id'], 'status': 'sending'}
        db = MagicMock()
        # The DB committed the claim, but the first HTTP response was lost.
        db.rpc.return_value.execute.side_effect = [
            httpx.ReadError('Resource temporarily unavailable'),
            SimpleNamespace(data={'state': 'claimed', 'delivery': delivery}),
            SimpleNamespace(data={'state': 'empty'}),
        ]
        with patch.object(emails, 'get_supabase', return_value=db), patch.object(emails, '_batch', return_value={'sender': settings.smtp_username.lower(), 'content': WeddingEmailContent().model_dump()}), patch.object(emails, '_load_guests', return_value=[guest]), patch.object(emails, 'send_message') as smtp, patch('app.database.time.sleep'):
            emails._run_batch(batch_id)
        smtp.assert_called_once()
        self.assertEqual(db.rpc.call_args_list[0].args[0], 'claim_wedding_email_v2')
        self.assertIn('p_request_id', db.rpc.call_args_list[0].args[1])
        self.assertNotEqual(db.rpc.call_args_list[0].args[1]['p_request_id'], db.rpc.call_args_list[1].args[1]['p_request_id'])

    def test_active_status_poll_only_reads_without_an_unnecessary_database_write(self):
        from datetime import datetime, timezone
        row = {'id': str(uuid4()), 'status': 'sending', 'attempted_at': datetime.now(timezone.utc).isoformat()}
        with patch.object(emails, '_batch', return_value={'last_error': 'paused'}), patch.object(emails, '_all_rows', return_value=[row]), patch.object(emails, 'get_supabase') as get_db:
            result = emails.batch_status(uuid4())
        self.assertEqual(result['deliveries'][0]['status'], 'sending')
        get_db.assert_not_called()

    def test_stale_sending_row_becomes_unknown_without_resubmitting_smtp(self):
        from datetime import datetime, timedelta, timezone
        row = {'id': str(uuid4()), 'status': 'sending', 'attempted_at': (datetime.now(timezone.utc)-timedelta(minutes=6)).isoformat()}
        db = MagicMock()
        db.table.return_value.update.return_value.eq.return_value.eq.return_value.lt.return_value.execute.return_value.data = [{'id': row['id']}]
        with patch.object(emails, '_batch', return_value={'last_error': 'paused'}), patch.object(emails, '_all_rows', return_value=[row]), patch.object(emails, 'get_supabase', return_value=db), patch.object(emails, 'send_message') as smtp:
            result = emails.batch_status(uuid4())
        self.assertEqual(result['deliveries'][0]['status'], 'unknown')
        smtp.assert_not_called()
