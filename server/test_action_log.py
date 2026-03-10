import json
import os
import tempfile
import unittest
from unittest import mock

import action_log


class ActionLogTests(unittest.TestCase):
    def test_log_action_writes_jsonl_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "actions.jsonl")
            with mock.patch.dict(
                os.environ,
                {"PENGUIN_CONNECT_ACTION_LOG_PATH": path},
                clear=False,
            ):
                record = action_log.log_action("test_event", ok=True, nested={"value": 3})

            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()

        self.assertEqual(len(lines), 1)
        written = json.loads(lines[0])
        self.assertEqual(written["event"], "test_event")
        self.assertTrue(written["ok"])
        self.assertEqual(written["nested"]["value"], 3)
        self.assertEqual(written["ts"], record["ts"])


if __name__ == "__main__":
    unittest.main()
