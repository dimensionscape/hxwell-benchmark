import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from bench.build import unpack
from bench.config import load_config, select_cases
from bench.metrics import parse_stat
from bench.report import summarize, write_reports
from bench.run import MARKER, parse_wrk, preflight, stop_group
from bench.vendor import verify_snapshot
from bench.dependencies import sha256


def measurement():
    return {"duration_us": 2_000_000, "requests": 200, "bytes": 3000,
            "latency_us": {"p50": 100, "p95": 200, "p99": 300, "max": 400, "mean": 120},
            "errors": {"connect": 0, "read": 0, "write": 0, "status": 0, "timeout": 0}}


def trial(rate, valid=True):
    return {"case_id": "jvm-socket", "workload": "plaintext", "connections": 4, "valid": valid,
            "rps": rate, "wrk": measurement(), "server": {"available": False}, "error": "timeout" if not valid else ""}


class HarnessTests(unittest.TestCase):
    def test_private_driver_snapshot_rejects_tampering_and_extra_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "vendor/hxwell-crossbyte"
            snapshot.mkdir(parents=True)
            file = snapshot / "haxelib.json"
            file.write_text('{}')
            spec = {"url": "https://example.invalid/repo.git", "revision": "a" * 40}
            (snapshot / "snapshot.json").write_text(json.dumps({"repository": spec["url"], "revision": spec["revision"],
                                                              "files": {"haxelib.json": sha256(file)}}))
            with patch("bench.vendor.ROOT", root):
                self.assertEqual(snapshot, verify_snapshot(spec))
                file.write_text('{"changed":true}')
                with self.assertRaises(ValueError):
                    verify_snapshot(spec)
                file.write_text('{}')
                (snapshot / "unexpected.hx").write_text('class Unexpected {}')
                with self.assertRaises(ValueError):
                    verify_snapshot(spec)
    def test_preflight_validates_payload_and_reuses_real_connection(self):
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            def log_message(self, *_):
                pass
            def do_GET(self):
                body = b"Hello, World!\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
        thread.start()
        try:
            config, _ = load_config()
            result = preflight(server.server_port, config["workloads"][0])
            self.assertTrue(result["keep_alive_verified"])
            self.assertEqual(14, result["bytes"])
            wrong = {**config["workloads"][0], "expected_text": "wrong"}
            with self.assertRaises(ValueError):
                preflight(server.server_port, wrong)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    def test_matrix_is_explicit_and_rejects_unknown_or_duplicate_cases(self):
        config, lock = load_config()
        self.assertEqual(7, len(select_cases(config)))
        self.assertEqual(["jvm-socket", "neko-crossbyte"], [c["id"] for c in select_cases(config, "jvm-socket,neko-crossbyte")])
        for text in ("", "jvm-socket,jvm-socket", "jvm-unknown", "../../tmp", "all;echo bad"):
            with self.assertRaises(ValueError):
                select_cases(config, text)
        self.assertEqual(40, len(lock["repositories"]["wrk"]["revision"]))

    def test_wrk_structured_units_and_error_counters(self):
        data = measurement()
        parsed = parse_wrk("ordinary wrk output\n" + MARKER + json.dumps(data) + "\n")
        self.assertEqual(100, parsed["requests"] / (parsed["duration_us"] / 1e6))
        self.assertEqual(300, parsed["latency_us"]["p99"])
        data["errors"]["timeout"] = 2
        self.assertEqual(2, parse_wrk(MARKER + json.dumps(data))["errors"]["timeout"])

    def test_wrk_empty_malformed_duplicate_and_nonfinite_results_fail(self):
        value = measurement()
        for text in ("", MARKER + "{}", MARKER + json.dumps(value) + "\n" + MARKER + json.dumps(value)):
            with self.assertRaises((ValueError, KeyError)):
                parse_wrk(text)
        for mutate in (lambda v: v.update(requests=0), lambda v: v.update(duration_us=0),
                       lambda v: v["latency_us"].update(p99=math.nan), lambda v: v["latency_us"].update(p50=500),
                       lambda v: v["errors"].update(timeout=-1), lambda v: v["errors"].update(timeout=None)):
            value = measurement()
            mutate(value)
            with self.assertRaises((ValueError, TypeError)):
                parse_wrk(MARKER + json.dumps(value))

    def test_summary_never_scores_partial_or_errorful_groups(self):
        good = summarize([trial(10), trial(30), trial(20)], 3)[0]
        self.assertTrue(good["valid"])
        self.assertEqual(20, good["rps_median"])
        self.assertEqual(0.3, good["p99_ms_median"])
        for values in ([trial(10)], [trial(10), trial(20, False), trial(30)]):
            row = summarize(values, 3)[0]
            self.assertFalse(row["valid"])
            self.assertNotIn("rps_median", row)

    def test_reports_preserve_invalid_trials_and_label_smoke(self):
        with tempfile.TemporaryDirectory() as folder:
            report = {"trials": [trial(10, False)], "profile": {"repetitions": 1, "performance_run": False},
                      "profile_name": "smoke", "hardware": {}, "affinity": {}, "versions": {}, "seed": 1}
            write_reports(report, folder)
            text = (Path(folder) / "summary.md").read_text()
            self.assertIn("Smoke run", text)
            self.assertIn("INVALID", text)
            self.assertEqual("timeout", json.loads((Path(folder) / "results.json").read_text())["trials"][0]["error"])
            self.assertTrue((Path(folder) / "summary.csv").exists())

    def test_proc_stat_with_spaces_and_parentheses_in_command(self):
        fields = ["S", "1", "456"] + ["0"] * 30
        fields[11], fields[12], fields[17], fields[19], fields[21] = "20", "7", "4", "1234", "500"
        value = parse_stat("123 (worker (name)) " + " ".join(fields))
        self.assertEqual({"pid": 123, "group": 456, "cpu_ticks": 27, "threads": 4, "start": 1234, "rss_pages": 500}, value)

    def test_artifact_extraction_rejects_parent_traversal(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive_path = root / "bad.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                info = tarfile.TarInfo("../escape")
                info.size = 3
                archive.addfile(info, io.BytesIO(b"bad"))
            with self.assertRaises(tarfile.FilterError):
                unpack(root, root / "output")
            self.assertFalse((root / "escape").exists())

    @unittest.skipUnless(os.name == "posix", "Process groups require POSIX")
    def test_cleanup_stops_owned_group_without_killing_other_processes(self):
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
        owned = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
        try:
            stop_group(owned)
            self.assertIsNotNone(owned.poll())
            self.assertIsNone(other.poll())
        finally:
            stop_group(other)


if __name__ == "__main__":
    unittest.main()
