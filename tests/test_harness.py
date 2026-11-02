import io
import json
import math
import os
from pathlib import Path
import resource
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
from bench.run import MARKER, ensure_file_limit, parse_wrk, preflight, run, stop_group, validate_health
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
        self.assertEqual(10, len(select_cases(config)))
        self.assertEqual({"jvm", "hl", "js", "cpp"}, {case["target"] for case in config["cases"]})
        self.assertEqual(["jvm-socket", "hl-crossbyte", "cpp-crossbyte", "cpp-crossbyte-libuv"],
                         [c["id"] for c in select_cases(config, "jvm-socket,hl-crossbyte,cpp-crossbyte,cpp-crossbyte-libuv")])
        for text in ("", "jvm-socket,jvm-socket", "jvm-unknown", "neko-socket", "neko-crossbyte", "../../tmp", "all;echo bad"):
            with self.assertRaises(ValueError):
                select_cases(config, text)
        self.assertEqual(40, len(lock["repositories"]["wrk"]["revision"]))

    def test_high_concurrency_raises_only_the_soft_descriptor_limit(self):
        with patch("bench.run.resource.getrlimit", return_value=(256, 8192)), patch("bench.run.resource.setrlimit") as change:
            limits = ensure_file_limit(1000)
        kind, (soft, hard) = change.call_args.args
        self.assertEqual(resource.RLIMIT_NOFILE, kind)
        self.assertGreaterEqual(soft, 2000)
        self.assertEqual(8192, hard)
        self.assertEqual(soft, limits["soft_after"])
        self.assertEqual(256, limits["soft_before"])

    def test_descriptor_limit_never_lowers_available_capacity(self):
        for before in (8192, resource.RLIM_INFINITY):
            with patch("bench.run.resource.getrlimit", return_value=(before, resource.RLIM_INFINITY)), patch("bench.run.resource.setrlimit") as change:
                limits = ensure_file_limit(1000)
                change.assert_not_called()
            self.assertEqual("unlimited" if before == resource.RLIM_INFINITY else before, limits["soft_after"])

    def test_insufficient_hard_descriptor_limit_fails_before_loading(self):
        with patch("bench.run.resource.getrlimit", return_value=(256, 1024)), patch("bench.run.resource.setrlimit") as change:
            with self.assertRaisesRegex(ValueError, "hard limit"):
                ensure_file_limit(1000)
            change.assert_not_called()

    def test_request_timeout_is_bounded_and_finite(self):
        config, lock = load_config()
        for timeout in (0, -1, float("nan"), float("inf"), True, 61):
            config["profiles"]["stress"]["request_timeout_seconds"] = timeout
            with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(ValueError, "Request timeout"):
                run(config, lock, [], folder, Path(folder) / "out", "stress", "unused-wrk")

    def test_libuv_configuration_requires_native_crossbyte(self):
        config, lock = load_config()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dependencies.lock.json").write_text(json.dumps(lock))
            for key, bad in (("target", "hl"), ("driver", "socket"), ("poll_backend", "unknown")):
                changed = json.loads(json.dumps(config))
                changed["cases"][-1][key] = bad
                (root / "benchmark.json").write_text(json.dumps(changed))
                with patch("bench.config.ROOT", root), self.assertRaises(ValueError):
                    load_config()

    def test_health_rejects_libuv_fallback_and_wrong_native_library(self):
        case = {"id": "cpp-crossbyte-libuv", "target": "cpp", "driver": "crossbyte", "poll_backend": "libuv"}
        metadata = {"libraries": {"libhxcpp": "6.0.0"}, "native_libraries": {"libuv": {"version": "1.51.0"}}}
        health = {"run_id": "owned", "case_id": case["id"], "target": "cpp", "driver": "crossbyte", "workers": 1,
                  "virtual_thread": False, "cpp_runtime": "libhxcpp", "cpp_runtime_version": "6.0.0",
                  "poll_backend": "libuv", "libuv_version": "1.51.0"}
        validate_health(health, case, 1, "owned", metadata)
        for key, bad in (("poll_backend", "builtin-fallback"), ("poll_backend", None), ("libuv_version", "1.48.0"),
                         ("cpp_runtime", "hxcpp"), ("cpp_runtime_version", "4.3.0"), ("virtual_thread", True),
                         ("run_id", "another-process")):
            with self.assertRaises(RuntimeError):
                validate_health({**health, key: bad}, case, 1, "owned", metadata)

    def test_all_cpp_drivers_require_libhxcpp_and_builtin_remains_distinct(self):
        config, _ = load_config()
        metadata = {"libraries": {"libhxcpp": "6.0.0"}, "native_libraries": {"libuv": {"version": "1.51.0"}}}
        for case in (c for c in config["cases"] if c["target"] == "cpp"):
            health = {"run_id": "owned", "case_id": case["id"], "target": "cpp", "driver": case["driver"], "workers": 1,
                      "virtual_thread": False, "cpp_runtime": "libhxcpp", "cpp_runtime_version": "6.0.0",
                      "poll_backend": case.get("poll_backend", "not-applicable"), "libuv_version": "1.51.0"}
            validate_health(health, case, 1, "owned", metadata)
            with self.assertRaises(RuntimeError):
                validate_health({**health, "cpp_runtime": "hxcpp"}, case, 1, "owned", metadata)
            if case.get("poll_backend") == "builtin":
                with self.assertRaises(RuntimeError):
                    validate_health({**health, "poll_backend": "libuv"}, case, 1, "owned", metadata)

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
