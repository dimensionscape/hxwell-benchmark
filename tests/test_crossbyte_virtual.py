import json
from pathlib import Path
import platform
import tempfile
import unittest
from unittest.mock import patch

from bench.config import crossbyte_repository_name, load_config
from bench.dependencies import sha256
from bench.run import validate_health, verify_bundle


class VirtualCrossbyteTests(unittest.TestCase):
    def cases(self):
        config, lock = load_config()
        return config, lock, [case for case in config["cases"]
                              if case["target"] == "jvm" and case["driver"] == "crossbyte"]

    def test_health_checks_io_thread_separately_from_virtual_request_thread(self):
        _, _, cases = self.cases()
        self.assertEqual(2, len(cases))
        for case in cases:
            expected = case["io_threads"] == "virtual"
            health = {"run_id": "owned", "case_id": case["id"], "target": "jvm", "driver": "crossbyte",
                      "workers": 2, "virtual_thread": True, "io_virtual_thread": expected}
            validate_health(health, case, 2, "owned", {})
            for wrong in (not expected, None, "true"):
                with self.assertRaisesRegex(RuntimeError, "actual JVM thread"):
                    validate_health({**health, "io_virtual_thread": wrong}, case, 2, "owned", {})

    def test_stock_and_fork_sources_cannot_be_swapped_in_a_bundle(self):
        _, lock, cases = self.cases()
        self.assertEqual({"crossbyte", "crossbyte-jvm-virtual"}, {crossbyte_repository_name(case) for case in cases})
        for case in cases:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "server.jar").write_bytes(b"fixture, never executed")
                source = crossbyte_repository_name(case)
                metadata = {"case": case, "build_os": platform.system(), "build_arch": platform.machine(),
                            "workloads_sha256": sha256(Path(__file__).resolve().parents[1] / "benchmark.json"),
                            "files": {"server.jar": sha256(root / "server.jar")},
                            "crossbyte_repository": lock["repositories"][source]}
                (root / "build.json").write_text(json.dumps(metadata))
                verify_bundle(root, case)
                other = "crossbyte" if source == "crossbyte-jvm-virtual" else "crossbyte-jvm-virtual"
                metadata["crossbyte_repository"] = lock["repositories"][other]
                (root / "build.json").write_text(json.dumps(metadata))
                with self.assertRaisesRegex(ValueError, "source provenance"):
                    verify_bundle(root, case)

    def test_virtual_fork_cannot_be_selected_for_other_targets_or_thread_modes(self):
        config, lock, cases = self.cases()
        fork = next(case for case in cases if case["io_threads"] == "virtual")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dependencies.lock.json").write_text(json.dumps(lock))
            variants = [{**fork, field: wrong} for field, wrong in
                        (("target", "hl"), ("driver", "socket"), ("crossbyte_variant", "unknown"), ("io_threads", "platform"))]
            variants.append({key: value for key, value in fork.items() if key != "io_threads"})
            for bad in variants:
                (root / "benchmark.json").write_text(json.dumps({**config, "cases": [bad]}))
                with patch("bench.config.ROOT", root), self.assertRaises(ValueError):
                    load_config()
