import tempfile
import unittest
from pathlib import Path

from tools.export_catalunya_static import (
    DEFAULT_MEETING_KEY,
    DEFAULT_SESSION_KEY,
    build_export_bundle,
    compute_degradation,
    compute_lap_deltas,
    fetch_by_driver,
    normalize_lap,
    write_static_bundle,
)


class CatalunyaStaticExportTests(unittest.TestCase):
    def test_default_target_is_2026_catalunya_race(self):
        self.assertEqual(DEFAULT_MEETING_KEY, 1287)
        self.assertEqual(DEFAULT_SESSION_KEY, 11307)

    def test_compute_lap_deltas_and_degradation(self):
        drivers = [
            {
                "session_key": 11307,
                "driver_number": 1,
                "name_acronym": "VER",
                "team_name": "Red Bull Racing",
            }
        ]
        laps = [
            {"session_key": 11307, "driver_number": 1, "lap_number": 1, "lap_duration": 80.0, "is_pit_out_lap": False},
            {"session_key": 11307, "driver_number": 1, "lap_number": 2, "lap_duration": 80.5, "is_pit_out_lap": False},
            {"session_key": 11307, "driver_number": 1, "lap_number": 3, "lap_duration": 81.0, "is_pit_out_lap": False},
        ]
        stints = [
            {
                "session_key": 11307,
                "driver_number": 1,
                "stint_number": 1,
                "compound": "MEDIUM",
                "lap_start": 1,
                "lap_end": 3,
            }
        ]

        deltas = compute_lap_deltas(laps, stints, drivers)
        self.assertEqual(deltas[1]["delta_to_prev"], 0.5)
        self.assertEqual(deltas[2]["delta_to_stint_start"], 1.0)
        self.assertEqual(deltas[0]["compound"], "MEDIUM")

        degradation = compute_degradation(laps, stints, drivers)
        self.assertEqual(len(degradation), 1)
        self.assertAlmostEqual(degradation[0]["deg_rate_sec_per_lap"], 0.5)
        self.assertEqual(degradation[0]["stint_length"], 3)

    def test_normalize_lap_preserves_openf1_detail_fields(self):
        lap = normalize_lap(
            {
                "session_key": 11307,
                "driver_number": 81,
                "lap_number": 12,
                "lap_duration": 80.123,
                "duration_sector_1": 26.1,
                "duration_sector_2": 29.2,
                "duration_sector_3": 24.8,
                "i1_speed": 291,
                "i2_speed": 302,
                "st_speed": 327,
                "segments_sector_1": [2048, 2048, 2049],
                "segments_sector_2": [2048, 2048],
                "segments_sector_3": [2049, 2048],
            }
        )

        self.assertEqual(lap["i1_speed"], 291)
        self.assertEqual(lap["i2_speed"], 302)
        self.assertEqual(lap["st_speed"], 327)
        self.assertEqual(lap["segments_sector_1"], [2048, 2048, 2049])
        self.assertEqual(lap["segments_sector_2"], [2048, 2048])
        self.assertEqual(lap["segments_sector_3"], [2049, 2048])

    def test_fetch_by_driver_backfills_missing_driver_rows(self):
        class FakeClient:
            def __init__(self):
                self.calls = []

            def get(self, endpoint, **params):
                self.calls.append((endpoint, params))
                return [
                    {
                        "session_key": params["session_key"],
                        "driver_number": params["driver_number"],
                        "lap_number": 1,
                    }
                ]

        client = FakeClient()
        rows = fetch_by_driver(
            client,
            "laps",
            11307,
            [1, 44, 81],
            existing_rows=[{"session_key": 11307, "driver_number": 1, "lap_number": 1}],
        )

        self.assertEqual([call[1]["driver_number"] for call in client.calls], [44, 81])
        self.assertEqual([row["driver_number"] for row in rows], [1, 44, 81])

    def test_build_bundle_and_write_static_contract(self):
        openf1_bundle = {
            "meetings": [
                {
                    "meeting_key": 1287,
                    "meeting_name": "Barcelona Grand Prix",
                    "location": "Barcelona",
                    "country_name": "Spain",
                    "circuit_short_name": "Catalunya",
                    "year": 2026,
                    "date_start": "2026-06-12T11:30:00+00:00",
                }
            ],
            "sessions": [
                {
                    "session_key": 11307,
                    "meeting_key": 1287,
                    "session_name": "Race",
                    "session_type": "Race",
                    "date_start": "2026-06-14T13:00:00+00:00",
                    "date_end": "2026-06-14T15:00:00+00:00",
                }
            ],
            "drivers": [
                {
                    "session_key": 11307,
                    "driver_number": 1,
                    "full_name": "Max Verstappen",
                    "name_acronym": "VER",
                    "team_name": "Red Bull Racing",
                    "team_colour": "1E5BC6",
                }
            ],
            "laps": [
                {
                    "session_key": 11307,
                    "driver_number": 1,
                    "lap_number": 1,
                    "lap_duration": 80.0,
                    "is_pit_out_lap": False,
                    "date_start": "2026-06-14T13:05:00+00:00",
                },
                {
                    "session_key": 11307,
                    "driver_number": 1,
                    "lap_number": 2,
                    "lap_duration": 80.4,
                    "is_pit_out_lap": False,
                    "date_start": "2026-06-14T13:06:20+00:00",
                },
            ],
            "stints": [
                {
                    "session_key": 11307,
                    "driver_number": 1,
                    "stint_number": 1,
                    "compound": "MEDIUM",
                    "lap_start": 1,
                    "lap_end": 2,
                }
            ],
            "radio": [
                {
                    "session_key": 11307,
                    "driver_number": 1,
                    "date": "2026-06-14T13:06:40+00:00",
                    "recording_url": "https://livetiming.formula1.com/radio.mp3",
                }
            ],
            "pit_stops": [],
            "positions": [],
            "race_control": [],
        }

        bundle = build_export_bundle(2026, 1287, 11307, openf1_bundle, fastf1_bundle=None)
        self.assertEqual(bundle["manifest"]["default_session_key"], 11307)
        self.assertEqual(bundle["manifest"]["sessions"][0]["files"]["radio"]["records"], 1)
        self.assertEqual(bundle["summary"]["data_sources"]["laps"], "openf1")
        self.assertEqual(bundle["radio"][0]["translation_status"], "audio_only")
        self.assertEqual(bundle["radio"][0]["approx_lap_number"], 2)

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            write_static_bundle(out, bundle)
            self.assertTrue((out / "manifest.json").exists())
            self.assertTrue((out / "sessions" / "11307" / "radio.json").exists())


if __name__ == "__main__":
    unittest.main()
