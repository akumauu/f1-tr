from __future__ import annotations

import sys
import json
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from catalog_tracinginsights_dataset import classify_path, validate_parallel_arrays, validate_telemetry
from ingest_tracinginsights_telemetry import normalize_session
from convert_tracinginsights_to_parquet import telemetry_table
from build_training_cache import group_boundaries

import numpy as np


class TracingInsightsCatalogTest(unittest.TestCase):
    def test_classifies_telemetry_lap(self) -> None:
        result = classify_path("Bahrain Grand Prix/Qualifying/VER/12_tel.json")
        self.assertEqual(
            result,
            ("telemetry_lap", "Bahrain Grand Prix", "Qualifying", "VER", 12),
        )

    def test_normalizes_legacy_sprint_name(self) -> None:
        result = classify_path("Austrian Grand Prix/Sprint Shootout/VER/3_tel.json")
        self.assertEqual(result[2], "Sprint Qualifying")
        self.assertEqual(normalize_session("Sprint Shootout"), "Sprint Qualifying")

    def test_validates_parallel_telemetry_arrays(self) -> None:
        fields = {
            "time": [0.0, 0.1],
            "speed": [100, 101],
            "throttle": [50, 51],
            "brake": [0, 0],
            "gear": [3, 3],
            "rpm": [9000, 9100],
            "drs": [0, 0],
            "x": [1.0, 2.0],
            "y": [3.0, 4.0],
            "z": [5.0, 6.0],
        }
        count, names, duration = validate_telemetry({"tel": fields})
        self.assertEqual(count, 2)
        self.assertIn("speed", names)
        self.assertAlmostEqual(duration or 0.0, 0.1)

    def test_rejects_unequal_arrays(self) -> None:
        with self.assertRaisesRegex(ValueError, "数组长度不一致"):
            validate_parallel_arrays({"lap": [1, 2], "time": [90.0]})

    def test_parquet_table_uses_float32_and_relative_provenance(self) -> None:
        fields = {
            "time": [0.0, 0.1], "rpm": [9000, 9100], "speed": [100, 101],
            "gear": [3, 128], "throttle": [50, 51], "brake": [0, 0],
            "drs": [0, 128], "distance": [0.0, 10.0], "rel_distance": [0.0, 0.1],
            "DriverAhead": [None, None], "DistanceToDriverAhead": [None, None],
            "acc_x": [0.0, 0.1], "acc_y": [0.0, 0.1], "acc_z": [0.0, 0.1],
            "x": [1.0, 2.0], "y": [3.0, 4.0], "z": [5.0, 6.0], "dataKey": "x",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Meeting" / "Race" / "VER" / "1_tel.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"tel": fields}), encoding="utf-8")
            table = telemetry_table(2024, "Meeting", "Race", 1, "VER", 1, path, "abc", {})
        self.assertEqual(str(table.schema.field("speed").type), "float")
        self.assertEqual(str(table.schema.field("drs").type), "int16")
        self.assertEqual(table["drs"][1].as_py(), 128)
        self.assertEqual(str(table.schema.field("gear").type), "int16")
        self.assertEqual(table["gear"][1].as_py(), 128)
        self.assertEqual(table["source_file"][0].as_py(), "Meeting/Race/VER/1_tel.json")

    def test_training_cache_group_boundaries(self) -> None:
        arrays = {
            "year": np.asarray([2024] * 4),
            "meeting": np.asarray(["A"] * 4),
            "session": np.asarray(["Race"] * 4),
            "driver_number": np.asarray([1, 1, 1, 1]),
            "lap": np.asarray([1, 1, 2, 2]),
        }
        self.assertEqual(group_boundaries(arrays, 4), [(0, 2), (2, 4)])


if __name__ == "__main__":
    unittest.main()
