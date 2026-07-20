import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from app.database import execute_read


class SequencedQuery:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.execute_calls = 0

    def execute(self):
        self.execute_calls += 1
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def read_error():
    return httpx.ReadError(
        "Resource temporarily unavailable",
        request=httpx.Request("GET", "https://example.supabase.co/rest/v1/guests"),
    )


class ExecuteReadTest(unittest.TestCase):
    def test_retries_transport_errors_twice_then_returns_response(self):
        expected = SimpleNamespace(data=[{"id": "guest-1"}])
        query = SequencedQuery([read_error(), read_error(), expected])

        with patch("app.database.time.sleep") as sleep:
            response = execute_read(query)

        self.assertIs(response, expected)
        self.assertEqual(query.execute_calls, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.1, 0.2])

    def test_raises_after_two_transport_error_retries(self):
        query = SequencedQuery([read_error(), read_error(), read_error()])

        with (
            patch("app.database.time.sleep") as sleep,
            self.assertRaises(httpx.ReadError),
        ):
            execute_read(query)

        self.assertEqual(query.execute_calls, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_does_not_retry_non_transport_errors(self):
        query = SequencedQuery([RuntimeError("invalid query")])

        with (
            patch("app.database.time.sleep") as sleep,
            self.assertRaisesRegex(RuntimeError, "invalid query"),
        ):
            execute_read(query)

        self.assertEqual(query.execute_calls, 1)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
