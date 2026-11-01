import base64
import hashlib
import json
import smtplib
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.services import email_relay as relay
from app.services.wedding_email import RelayNotSubmittedError, SmtpSetupError, build_message, send_message, smtp_ready
from app.schemas.email import WeddingEmailContent


class ReceiptDB:
    """Two receiver instances share this durable store; SQL semantics are tested in the DB repo."""
    def __init__(self):
        self.rows = {}
        self.lock = threading.Lock()
        self.lose_reservation = False
        self.lose_result = False

    def rpc(self, name, params):
        assert name == 'reserve_wedding_email_relay'
        def execute():
            with self.lock:
                key = params['p_id']
                if key in self.rows:
                    row = self.rows[key]
                    if row['hash'] != params['p_hash'] or row['sender'] != params['p_sender']:
                        return SimpleNamespace(data={'state': 'conflict', 'status': 'unknown'})
                    return SimpleNamespace(data={'state': 'replay', 'status': 'unknown' if row['status'] == 'sending' else row['status'], 'reason': row.get('reason')})
                self.rows[key] = {'hash': params['p_hash'], 'sender': params['p_sender'], 'status': 'sending'}
                if self.lose_reservation:
                    raise httpx.ReadError('lost committed response')
                return SimpleNamespace(data={'state': 'claimed'})
        return SimpleNamespace(execute=execute)

    def table(self, name):
        assert name == 'wedding_email_relay_requests'
        query = MagicMock()
        filters = {}
        query.eq.side_effect = lambda key, value: (filters.update({key: value}) or query)
        def update(payload):
            def execute():
                if self.lose_result:
                    raise httpx.ReadError('result write unavailable')
                with self.lock:
                    row = self.rows[filters['id']]
                    if row['status'] == filters['status']:
                        row.update(payload)
                return SimpleNamespace(data=[])
            query.execute.side_effect = execute
            return query
        query.update.side_effect = update
        return query


class EmailRelayTest(unittest.TestCase):
    def setUp(self):
        self.config = patch.multiple(settings, email_relay_enabled=True, email_relay_secret='test-only-' + 'x' * 40,
                                     email_relay_url='', smtp_username='sender@gmail.com', smtp_password='test-app-password', smtp_port=465)
        self.config.start()
        self.addCleanup(self.config.stop)
        self.client = TestClient(app)
        self.db = ReceiptDB()
        self.message = build_message({'id': str(uuid4()), 'name': 'Synthetic Guest', 'email': 'guest@example.com',
                                      'checkin_token': 'synthetic-token', 'allocated_table': '好友桌', 'table_number': 8},
                                     WeddingEmailContent(), uuid4())
        self.body = json.dumps({'request_id': str(uuid4()), 'message': base64.b64encode(self.message.as_bytes()).decode()}).encode()

    def post(self, body=None, timestamp=None, signed=None):
        body = self.body if body is None else body
        timestamp = str(int(time.time())) if timestamp is None else timestamp
        return self.client.post('/api/internal/email-relay/send', content=body,
                                headers={'X-Relay-Timestamp': timestamp,
                                         'X-Relay-Signature': relay.signature(timestamp, body) if signed is None else signed})

    def test_disabled_unsigned_tampered_and_expired_requests_never_touch_db_or_smtp(self):
        with patch.object(relay, 'get_supabase') as db, patch.object(relay, 'send_smtp_message') as smtp:
            with patch.object(settings, 'email_relay_enabled', False):
                self.assertEqual(self.post().status_code, 404)
            self.assertEqual(self.post(signed='invalid').status_code, 401)
            self.assertEqual(self.post(timestamp=str(int(time.time()) - 301)).status_code, 401)
            self.assertEqual(self.post(timestamp='broken').status_code, 401)
            self.assertEqual(self.post(body=self.body + b' ', signed=relay.signature(str(int(time.time())), self.body)).status_code, 401)
        db.assert_not_called()
        smtp.assert_not_called()

    def test_oversized_or_wrong_sender_or_multiple_recipients_are_rejected(self):
        with patch.object(relay, 'get_supabase') as db:
            self.assertEqual(self.post(body=b'x' * (relay.MAX_BODY_BYTES + 1)).status_code, 413)
            for field, value in [('From', 'other@gmail.com'), ('To', 'one@example.com,two@example.com'), ('Cc', 'hidden@example.com')]:
                message = build_message({'id': str(uuid4()), 'name': 'Test', 'email': 'guest@example.com', 'checkin_token': 'test'}, WeddingEmailContent(), uuid4())
                if field in message:
                    message.replace_header(field, value)
                else:
                    message[field] = value
                body = json.dumps({'request_id': str(uuid4()), 'message': base64.b64encode(message.as_bytes()).decode()}).encode()
                self.assertEqual(self.post(body).status_code, 422)
        db.assert_not_called()

    def test_successful_response_loss_then_replay_and_restart_does_not_resend(self):
        with patch.object(relay, 'get_supabase', return_value=self.db), patch.object(relay, 'send_smtp_message') as smtp:
            first = self.post().json()
            replay = self.post().json()
        self.assertEqual(first['status'], 'sent')
        self.assertEqual(replay, first)
        smtp.assert_called_once()
        self.assertNotIn('guest@example.com', json.dumps(self.db.rows))
        self.assertNotIn('synthetic-token', json.dumps(self.db.rows))

    def test_two_concurrent_receivers_only_submit_once(self):
        entered, release = threading.Event(), threading.Event()
        def slow_send(message):
            entered.set()
            self.assertTrue(release.wait(5))
        with patch.object(relay, 'get_supabase', return_value=self.db), patch.object(relay, 'send_smtp_message', side_effect=slow_send) as smtp:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(relay.receive_message, self.body)
                self.assertTrue(entered.wait(5))
                duplicate = pool.submit(relay.receive_message, self.body).result(5)
                self.assertEqual(duplicate['status'], 'unknown')
                release.set()
                self.assertEqual(first.result(5)['status'], 'sent')
        smtp.assert_called_once()

    def test_lost_reservation_response_never_submits_or_reclaims(self):
        self.db.lose_reservation = True
        with patch.object(relay, 'get_supabase', return_value=self.db), patch.object(relay, 'send_smtp_message') as smtp:
            self.assertEqual(self.post().json()['status'], 'unknown')
            self.db.lose_reservation = False
            self.assertEqual(self.post().json()['status'], 'unknown')
        smtp.assert_not_called()

    def test_failed_result_persistence_and_uncertain_smtp_never_resubmit(self):
        for failure in ('persistence', 'smtp'):
            with self.subTest(failure=failure):
                self.db = ReceiptDB()
                self.db.lose_result = failure == 'persistence'
                smtp_error = smtplib.SMTPServerDisconnected() if failure == 'smtp' else None
                with patch.object(relay, 'get_supabase', return_value=self.db), patch.object(relay, 'send_smtp_message', side_effect=smtp_error) as smtp, patch('app.database.time.sleep'):
                    self.assertEqual(self.post().json()['status'], 'unknown')
                    self.assertEqual(self.post().json()['status'], 'unknown')
                smtp.assert_called_once()

    def test_same_id_different_payload_does_not_send(self):
        with patch.object(relay, 'get_supabase', return_value=self.db), patch.object(relay, 'send_smtp_message') as smtp:
            self.assertEqual(self.post().json()['status'], 'sent')
            self.assertEqual(self.post(self.body + b' ').json()['status'], 'unknown')
        smtp.assert_called_once()

    def test_replay_retains_original_outcome_after_smtp_configuration_changes(self):
        for smtp_error, expected in [(None, 'sent'), (smtplib.SMTPServerDisconnected(), 'unknown')]:
            self.db = ReceiptDB()
            with patch.object(relay, 'get_supabase', return_value=self.db), patch.object(relay, 'send_smtp_message', side_effect=smtp_error) as smtp:
                self.assertEqual(self.post().json()['status'], expected)
                with patch.object(settings, 'smtp_password', ''), patch.object(settings, 'smtp_port', 0):
                    self.assertEqual(self.post().json()['status'], expected)
            smtp.assert_called_once()

    def test_definitive_failures_replay_without_second_submission(self):
        for error, reason in [(SmtpSetupError(), 'setup'), (smtplib.SMTPAuthenticationError(535, b'bad'), 'auth'),
                              (smtplib.SMTPDataError(550, b'rejected'), 'rejected')]:
            self.db = ReceiptDB()
            with patch.object(relay, 'get_supabase', return_value=self.db), patch.object(relay, 'send_smtp_message', side_effect=error) as smtp:
                result = self.post().json()
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['reason'], reason)
                self.assertEqual(self.post().json(), result)
            smtp.assert_called_once()

    def test_client_preserves_mime_and_uses_https_without_smtp_credentials(self):
        received = []
        class RelayClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def post(inner, url, content, headers):
                received.append((content, headers, inner.kwargs))
                # Simulate separately configured receiver: no production SMTP credentials required.
                with patch.object(settings, 'smtp_password', 'receiver-only-app-password'):
                    result = self.post(content, headers['X-Relay-Timestamp'], headers['X-Relay-Signature'])
                return httpx.Response(result.status_code, json=result.json(), request=httpx.Request('POST', url))
        with patch.object(settings, 'email_relay_url', 'https://relay.example.com/api/internal/email-relay/send'), patch.object(settings, 'smtp_password', ''), patch.object(relay.httpx, 'Client', RelayClient), patch.object(relay, 'get_supabase', return_value=self.db), patch.object(relay, 'send_smtp_message') as smtp:
            self.assertTrue(smtp_ready())
            send_message(self.message)
            send_message(self.message)  # even caller replay can't double-send
        smtp.assert_called_once()
        mime = smtp.call_args.args[0]
        self.assertIn('第 8 桌｜好友桌', mime.get_body(preferencelist=('html',)).get_content())
        self.assertEqual([p.get_payload(decode=True) for p in mime.walk() if p.get_content_type() == 'image/png'],
                         [p.get_payload(decode=True) for p in self.message.walk() if p.get_content_type() == 'image/png'])
        self.assertEqual(received[0][0], received[1][0])
        self.assertFalse(received[0][2]['follow_redirects'])

    def test_client_timeout_or_invalid_response_is_unknown_without_retry_or_fallback(self):
        with patch.object(settings, 'email_relay_url', 'https://relay.example.com/send'), patch.object(relay.httpx, 'Client') as client, patch('app.services.wedding_email.smtplib.SMTP_SSL') as smtp:
            client.return_value.__enter__.return_value.post.side_effect = httpx.ReadTimeout('lost')
            with self.assertRaises(relay.RelayOutcomeUnknown):
                send_message(self.message)
        client.return_value.__enter__.return_value.post.assert_called_once()
        smtp.assert_not_called()
        for url in ('http://relay.example.com/send', 'https://user:password@relay.example.com/send', 'https://relay.example.com/send?token=x'):
            with patch.object(settings, 'email_relay_url', url):
                self.assertFalse(smtp_ready())

    def test_check_route_only_checks_storage_and_authentication_without_email_or_writes(self):
        timestamp, body = str(int(time.time())), b'{}'
        with patch.object(relay, 'get_supabase', return_value=self.db), patch.object(relay, 'connect_smtp') as connect, patch.object(relay, 'send_smtp_message') as send:
            response = self.client.post('/api/internal/email-relay/check', content=body, headers={
                'X-Relay-Timestamp': timestamp, 'X-Relay-Signature': relay.signature(timestamp, body)})
        self.assertEqual(response.json(), {'status': 'ready', 'sender': 'sender@gmail.com'})
        connect.return_value.close.assert_called_once()
        connect.return_value.send_message.assert_not_called()
        send.assert_not_called()
        self.assertEqual(self.db.rows, {})
        with patch.object(relay, 'get_supabase', side_effect=RuntimeError('missing table')), patch.object(relay, 'connect_smtp') as connect:
            self.assertEqual(relay.check_receiver()['reason'], 'storage')
        connect.assert_not_called()

    def test_client_preserves_definitive_failure_and_rejects_unrecognized_outcomes(self):
        for status, reason, expected in [('failed', 'quota', RelayNotSubmittedError),
                                         ('failed', 'auth', smtplib.SMTPAuthenticationError),
                                         ('unknown', 'smtp', relay.RelayOutcomeUnknown),
                                         ('unexpected', None, relay.RelayOutcomeUnknown)]:
            with self.subTest(status=status, reason=reason), patch.object(settings, 'email_relay_url', 'https://relay.example.com/send'), patch.object(relay.httpx, 'Client') as client:
                def post(url, content, headers):
                    return httpx.Response(200, json={'request_id': json.loads(content)['request_id'], 'status': status, 'reason': reason}, request=httpx.Request('POST', url))
                client.return_value.__enter__.return_value.post.side_effect = post
                with self.assertRaises(expected):
                    send_message(self.message)
