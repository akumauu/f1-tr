from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from acceptance_check_parquet import acceptance_check, float32_time_tolerance_ns
from convert_tracinginsights_to_parquet import (
    PARQUET_SCHEMA,
    PARQUET_SCHEMA_V4,
    PARQUET_SCHEMA_V5,
    SCHEMA_V4_VERSION,
    SCHEMA_V5_VERSION,
    SCHEMA_VERSION,
    audit_parquet_file,
    empty_source_audit,
    finalize_source_audit,
    telemetry_table,
)


def telemetry_payload() -> dict[str, object]:
    return {
        "time": [0.0, 0.5, 1.0, 1.5],
        "rpm": [9000, 9100, 9200, 9300],
        "speed": [100.0, None, float("nan"), float("inf")],
        "gear": [1, 128, 70_000, 2],
        "throttle": [50.0, "bad", float("-inf"), 51.0],
        "brake": [0, 1, 2, 0],
        "drs": [0, 14, 99, 40_000],
        "distance": [0.0, 10.0, 20.0, 30.0],
        "rel_distance": [0.0, 0.1, 0.2, 0.3],
        "DriverAhead": [None, "44", "44", "44"],
        "DistanceToDriverAhead": [None, 20.0, 19.0, 18.0],
        "acc_x": [0.0, 0.1, 0.2, 0.3],
        "acc_y": [0.0, 0.1, 0.2, 0.3],
        "acc_z": [0.0, 0.1, 0.2, 0.3],
        "x": [1.0, 2.0, 3.0, 4.0],
        "y": [3.0, 4.0, 5.0, 6.0],
        "z": [5.0, 6.0, 7.0, 8.0],
        "dataKey": "2026-Test-Race-VER-1",
        "future_state": [float("nan"), {"mode": "x"}, 3, None],
    }


def lap_metadata(lap_start: str = "2026-03-06T01:30:51.344000000") -> dict[str, object]:
    return {
        "time": 90.0,
        "compound": "ULTRASOFT",
        "stint": 1,
        "life": 2.0,
        "fresh": True,
        "team": "Test Team",
        "pos": 1,
        "status": 12,
        "del": False,
        "iacc": True,
        "lSD": lap_start,
        "wR": "maybe",
    }


class TracingInsightsSchemaV5Test(unittest.TestCase):
    def write_lap(self, directory: str, fields: dict[str, object] | None = None) -> Path:
        path = Path(directory) / "Meeting" / "Race" / "VER" / "1_tel.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"tel": fields or telemetry_payload()}, allow_nan=True),
            encoding="utf-8",
        )
        return path

    def test_v4_remains_the_default_and_v5_is_an_append_only_contract(self) -> None:
        self.assertEqual(SCHEMA_VERSION, SCHEMA_V4_VERSION)
        self.assertIs(PARQUET_SCHEMA, PARQUET_SCHEMA_V4)
        self.assertEqual(
            PARQUET_SCHEMA_V5.names[: len(PARQUET_SCHEMA_V4.names)],
            PARQUET_SCHEMA_V4.names,
        )
        for field in PARQUET_SCHEMA_V4:
            self.assertEqual(PARQUET_SCHEMA_V5.field(field.name).type, field.type)
        self.assertEqual(PARQUET_SCHEMA_V5.field("drs_raw").type.bit_width, 64)

    def test_v5_preserves_raw_states_and_does_not_fabricate_missing_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_lap(directory)
            source_audit = empty_source_audit()
            table = telemetry_table(
                2026,
                "Meeting",
                "Race",
                1,
                "VER",
                1,
                path,
                "abc",
                lap_metadata(),
                SCHEMA_V5_VERSION,
                source_audit,
            )

        speed = table["speed"].to_pylist()
        self.assertIsNone(speed[1])
        self.assertTrue(np.isnan(speed[2]))
        self.assertTrue(np.isposinf(speed[3]))
        self.assertIsNone(table["throttle"][1].as_py())
        self.assertTrue(np.isneginf(table["throttle"][2].as_py()))
        self.assertEqual(table["drs_raw"].to_pylist(), [0, 14, 99, 40_000])
        self.assertIsNone(table["drs"][3].as_py())
        self.assertEqual(table["drs_raw_source"][0].as_py(), "tel.drs")
        self.assertEqual(table["track_status_raw"][0].as_py(), "12")
        self.assertEqual(table["lap_start_raw"][0].as_py(), "2026-03-06T01:30:51.344000000")
        self.assertEqual(table["absolute_time_source"][0].as_py(), "laptimes.lSD+tel.time")
        self.assertEqual(
            (table["sample_time"][1].as_py() - table["lap_start_time"][1].as_py()).total_seconds(),
            0.5,
        )
        self.assertIsNone(table["rainfall"][0].as_py())
        extra = json.loads(table["extra_telemetry_json"][0].as_py())
        self.assertEqual(extra["future_state"], {"__non_finite__": "NaN"})

        finalized = finalize_source_audit(source_audit)
        self.assertIn("future_state", finalized["unknown_telemetry_fields"])
        self.assertEqual(finalized["coercion_failures"]["tel.throttle"]["count"], 1)
        self.assertEqual(finalized["coercion_failures"]["laptimes.wR"]["count"], 1)

    def test_v5_audits_null_nan_infinity_ranges_and_unknown_enums(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_lap(directory)
            source_audit = empty_source_audit()
            table = telemetry_table(
                2026, "Meeting", "Race", 1, "VER", 1, path, "abc", lap_metadata(),
                SCHEMA_V5_VERSION, source_audit,
            )
            parquet_path = Path(directory) / "telemetry.parquet"
            pq.write_table(table, parquet_path)
            audit = audit_parquet_file(
                parquet_path,
                source_audit,
                schema_version=SCHEMA_V5_VERSION,
            )

        speed = audit["columns"]["speed"]
        self.assertEqual(speed["null_count"], 1)
        self.assertEqual(speed["nan_count"], 1)
        self.assertEqual(speed["positive_infinity_count"], 1)
        self.assertEqual(speed["finite_count"], 1)
        self.assertEqual(speed["min"], 100.0)
        self.assertEqual(speed["max"], 100.0)
        self.assertEqual(audit["columns"]["throttle"]["negative_infinity_count"], 1)
        self.assertEqual(
            {item["value"] for item in audit["unknown_enum_values"]["drs_raw"]},
            {99, 40_000},
        )
        self.assertEqual(
            {item["value"] for item in audit["unknown_enum_values"]["gear"]},
            {128},
        )
        self.assertEqual(
            audit["unknown_enum_values"]["compound"][0]["value"],
            "ULTRASOFT",
        )

    def test_invalid_raw_timestamp_is_retained_but_no_absolute_time_is_invented(self) -> None:
        fields = telemetry_payload()
        fields["speed"] = [100.0, 101.0, 102.0, 103.0]
        fields["throttle"] = [50.0, 51.0, 52.0, 53.0]
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_lap(directory, fields)
            source_audit = empty_source_audit()
            table = telemetry_table(
                2026, "Meeting", "Race", 1, "VER", 1, path, "abc",
                lap_metadata("not-a-time"), SCHEMA_V5_VERSION, source_audit,
            )
        self.assertEqual(table["lap_start_raw"][0].as_py(), "not-a-time")
        self.assertIsNone(table["lap_start_time"][0].as_py())
        self.assertIsNone(table["sample_time"][0].as_py())
        self.assertIsNone(table["absolute_time_source"][0].as_py())
        self.assertEqual(source_audit["parse_failures"]["lap_start_raw"]["count"], 1)

    def test_real_style_source_time_uses_raw_precision_before_float32_storage(self) -> None:
        fields = telemetry_payload()
        fields["time"] = [0.0, 0.045, 64.213, 1028.612]
        fields["speed"] = [100.0, 101.0, 102.0, 103.0]
        fields["throttle"] = [50.0, 51.0, 52.0, 53.0]
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_lap(directory, fields)
            table = telemetry_table(
                2026, "Meeting", "Race", 1, "VER", 1, path, "abc", lap_metadata(),
                SCHEMA_V5_VERSION, empty_source_audit(),
            )

        stored_time = table["time"][3].as_py()
        lap_start_ns = table["lap_start_time"].cast(pa.int64())[3].as_py()
        sample_time_ns = table["sample_time"].cast(pa.int64())[3].as_py()
        expected_lap_start_ns = int(
            np.datetime64("2026-03-06T01:30:51.344000000", "ns").astype(np.int64)
        )
        source_offset_ns = round(1028.612 * 1_000_000_000.0)
        stored_offset_ns = round(stored_time * 1_000_000_000.0)
        quantization_error_ns = abs(source_offset_ns - stored_offset_ns)
        self.assertEqual(lap_start_ns, expected_lap_start_ns)
        self.assertEqual(sample_time_ns - lap_start_ns, source_offset_ns)
        self.assertGreater(quantization_error_ns, 2_000)
        self.assertLessEqual(
            quantization_error_ns,
            float32_time_tolerance_ns(stored_time),
        )

    def test_converter_output_passes_v5_acceptance(self) -> None:
        fields = telemetry_payload()
        fields["time"] = [0.0, 0.045, 64.213, 1028.612]
        fields["speed"] = [100.0, 101.0, 102.0, 103.0]
        fields["throttle"] = [50.0, 51.0, 52.0, 53.0]
        fields["gear"] = [1, 2, 3, 4]
        fields["drs"] = [0, 8, 10, 14]
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            raw = base / "raw"
            path = self.write_lap(str(raw), fields)
            session_dir = path.parents[1]
            (session_dir / "drivers.json").write_text(
                json.dumps({"drivers": [{"driver": "VER", "dn": 1}]}),
                encoding="utf-8",
            )
            meta = lap_metadata()
            meta["lap"] = 1
            (path.parent / "laptimes.json").write_text(
                json.dumps({key: [value] for key, value in meta.items()}),
                encoding="utf-8",
            )
            output = base / "normalized"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "convert_tracinginsights_to_parquet.py"),
                    "--year", "2026",
                    "--root", str(raw),
                    "--commit", "0123456789abcdef",
                    "--out", str(output),
                    "--schema-version", SCHEMA_V5_VERSION,
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            schema_root = output / f"schema={SCHEMA_V5_VERSION}"
            report = acceptance_check(schema_root, sample_partitions=0)
        self.assertEqual(report["verdict"], "PASS", report["errors"])
        self.assertEqual(report["v5_semantic_checks"][0]["rows_checked"], len(fields["time"]))


if __name__ == "__main__":
    unittest.main()
