import os
import threading
import unittest
from unittest import mock

import watcher


class _FakeThread:
    def __init__(self, *args, **kwargs):
        self._started = False
        self._joined = False

    def start(self):
        self._started = True

    def is_alive(self):
        return self._started and not self._joined

    def join(self, timeout=None):
        self._joined = True


class WatcherTests(unittest.TestCase):
    def setUp(self):
        watcher._thread = None
        watcher._last_error_code = None
        watcher._shutdown_event = threading.Event()
        with watcher._status_lock:
            watcher._sync_status["penguin_connect"] = {
                "last_sync": None,
                "polling": False,
            }

    def tearDown(self):
        watcher._thread = None
        watcher._last_error_code = None
        watcher._shutdown_event = threading.Event()
        with watcher._status_lock:
            watcher._sync_status["penguin_connect"] = {
                "last_sync": None,
                "polling": False,
            }

    def test_poll_interval_seconds_clamps_to_supported_range(self):
        with mock.patch.dict(os.environ, {"PENGUIN_CONNECT_POLL_SECONDS": "3"}, clear=False):
            self.assertEqual(watcher._poll_interval_seconds(), 10)

        with mock.patch.dict(os.environ, {"PENGUIN_CONNECT_POLL_SECONDS": "45"}, clear=False):
            self.assertEqual(watcher._poll_interval_seconds(), 45)

        with mock.patch.dict(os.environ, {"PENGUIN_CONNECT_POLL_SECONDS": "999"}, clear=False):
            self.assertEqual(watcher._poll_interval_seconds(), 300)

    def test_poll_initial_delay_seconds_uses_interval_on_invalid_input(self):
        with mock.patch.dict(os.environ, {"PENGUIN_CONNECT_POLL_INITIAL_DELAY_SECONDS": "bad"}, clear=False):
            self.assertEqual(watcher._poll_initial_delay_seconds(25), 25)

        with mock.patch.dict(os.environ, {"PENGUIN_CONNECT_POLL_INITIAL_DELAY_SECONDS": "-4"}, clear=False):
            self.assertEqual(watcher._poll_initial_delay_seconds(25), 0)

        with mock.patch.dict(os.environ, {"PENGUIN_CONNECT_POLL_INITIAL_DELAY_SECONDS": "500"}, clear=False):
            self.assertEqual(watcher._poll_initial_delay_seconds(25), 300)

    def test_get_sync_status_returns_copy(self):
        status = watcher.get_sync_status()
        status["penguin_connect"]["polling"] = True

        self.assertFalse(watcher.get_sync_status()["penguin_connect"]["polling"])

    def test_polling_loop_updates_last_sync_after_success(self):
        def fake_run_incremental_sync():
            watcher._shutdown_event.set()
            return {"success": True, "mode": "incremental"}

        with mock.patch.dict(
            os.environ,
            {
                "PENGUIN_CONNECT_POLL_SECONDS": "10",
                "PENGUIN_CONNECT_POLL_INITIAL_DELAY_SECONDS": "0",
            },
            clear=False,
        ), mock.patch("penguin_connect.run_incremental_sync", side_effect=fake_run_incremental_sync), mock.patch(
            "watcher.log_action"
        ):
            watcher._penguin_connect_polling_loop()

        status = watcher.get_sync_status()
        self.assertIsNotNone(status["penguin_connect"]["last_sync"])
        self.assertIsNone(watcher._last_error_code)

    def test_polling_loop_treats_queue_busy_as_non_error(self):
        def fake_run_incremental_sync():
            watcher._shutdown_event.set()
            return {"success": True, "skipped": True, "reason": "queue_busy"}

        with mock.patch.dict(
            os.environ,
            {
                "PENGUIN_CONNECT_POLL_SECONDS": "10",
                "PENGUIN_CONNECT_POLL_INITIAL_DELAY_SECONDS": "0",
            },
            clear=False,
        ), mock.patch("penguin_connect.run_incremental_sync", side_effect=fake_run_incremental_sync), mock.patch(
            "watcher.log_action"
        ):
            watcher._penguin_connect_polling_loop()

        status = watcher.get_sync_status()
        self.assertIsNone(status["penguin_connect"]["last_sync"])
        self.assertIsNone(watcher._last_error_code)

    def test_start_watchers_is_single_flight(self):
        with mock.patch("watcher.threading.Thread", side_effect=_FakeThread) as mock_thread, mock.patch(
            "watcher.log_action"
        ), mock.patch("builtins.print"):
            watcher.start_watchers()
            watcher.start_watchers()
            status = watcher.get_sync_status()
            watcher.stop_watchers()

        self.assertEqual(mock_thread.call_count, 1)
        self.assertTrue(status["penguin_connect"]["polling"])
        self.assertFalse(watcher.get_sync_status()["penguin_connect"]["polling"])


if __name__ == "__main__":
    unittest.main()
