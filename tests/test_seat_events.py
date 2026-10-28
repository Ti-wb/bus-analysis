"""A failed CSV append must remain retryable without losing or duplicating events."""
import csv
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.collect_seat_events import append_events


class SeatEventTests(unittest.TestCase):
    def test_partial_write_rolls_back_and_retry_deduplicates_the_batch(self):
        event = {"BusID": "bus", "StopID": "stop", "CarOnStop": "1", "DataTime": "2026-10-26T08:00:00"}
        next_event = {**event, "DataTime": "2026-10-26T08:01:00"}
        seen = set()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.csv"
            append_events(path, [event], seen)
            original, original_seen = path.read_bytes(), seen.copy()

            def fail(writer, rows):
                writer.writerow(next(iter(rows)))
                raise OSError("simulated write failure")

            with patch.object(csv.DictWriter, "writerows", fail), self.assertRaises(OSError):
                append_events(path, [next_event], seen)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(seen, original_seen)
            self.assertEqual(append_events(path, [event, next_event, next_event], seen), 1)
            self.assertEqual(append_events(path, [event, next_event], seen), 0)
            with path.open(newline="") as stream:
                self.assertEqual([row["DataTime"] for row in csv.DictReader(stream)], [event["DataTime"], next_event["DataTime"]])
