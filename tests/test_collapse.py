"""The quarter collapse has to survive a schema change and a failure day."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analysis.process_data import collapse_csvs  # noqa: E402


class CollapseTests(unittest.TestCase):
    def test_schema_change_failure_day_and_blank_serial(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "a.csv").write_text(
                "date,serial_number,model,capacity_bytes,failure,smart_9_raw,datacenter,is_legacy_format\n"
                "2025-01-01,aaa,ST16000NM001G,16000900661248,0,1000,sac,0\n"
                "2025-01-02,aaa,ST16000NM001G,16000900661248,0,1024,sac,0\n"
                "2025-01-03,aaa,st16000nm001g,16000900661248,1,1048,sac,0\n"
                "2025-01-04,aaa,ST16000NM001G,16000900661248,0,1100,sac,0\n"
                "2025-01-01,bbb,MG08ACA16TE,16000900661248,0,20000,phx,0\n"
                "2025-01-02,,ST1000,1,0,5,sac,0\n",
                encoding="utf-8",
            )
            # Second file has no location columns. One row is not a record.
            (folder / "b.csv").write_text(
                "date,serial_number,model,capacity_bytes,failure,smart_9_raw\n"
                "2025-01-01,ccc,WUH721816ALE6L4,18000123456789,0,5000\n"
                "2025-01-02,ddd,ST1000,not-a-capacity,0,10\n"
                "\"unterminated\n",
                encoding="utf-8",
            )
            out = folder / "quarter.parquet"
            summary = collapse_csvs(sorted(folder.glob("*.csv")), "2025Q1", out)
            frame = duckdb.execute(f"SELECT * FROM read_parquet('{out}') ORDER BY serial_number").fetchdf()

        models = dict(zip(frame["serial_number"], frame["model"]))
        self.assertEqual(models["AAA"], "ST16000NM001G")
        aaa = frame[frame["serial_number"] == "AAA"].iloc[0]
        self.assertEqual(int(aaa["failed"]), 1)
        self.assertEqual(int(aaa["drive_days"]), 3)
        self.assertEqual(int(aaa["post_failure_days"]), 1)
        self.assertEqual(int(aaa["smart9_entry"]), 1000)
        self.assertEqual(int(aaa["smart9_exit"]), 1048)
        # A bad capacity must not drop the drive-day. The cast becomes null.
        self.assertEqual(set(frame["serial_number"]), {"AAA", "BBB", "CCC", "DDD"})
        ddd = frame[frame["serial_number"] == "DDD"].iloc[0]
        self.assertTrue(pd.isna(ddd["capacity_bytes"]))
        self.assertGreaterEqual(summary["dropped_identity_rows"], 1)


if __name__ == "__main__":
    unittest.main()
