import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bench.config import load_config
from bench.dependencies import sha256
from bench.hashlink import RUNTIME_FILES, verify_runtime
from bench.run import validate_health


class HashLinkTests(unittest.TestCase):
    def fixture(self, root, lock):
        for name in RUNTIME_FILES:
            (root / name).write_text("fixture " + name)
        spec = lock["repositories"]["hashlink"]
        manifest = {"version": spec["version"], "revision": spec["revision"], "mode": "jit",
                    "files": {name: sha256(root / name) for name in RUNTIME_FILES}}
        (root / "manifest.json").write_text(json.dumps(manifest))
        return manifest

    def test_hashlink_rejects_modified_runtime_and_incomplete_provenance(self):
        _, lock = load_config()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = self.fixture(root, lock)
            with patch("bench.hashlink.capture", return_value=expected["version"]):
                self.assertEqual(expected, verify_runtime(lock, root / "hl"))
                (root / "ssl.hdll").write_text("changed")
                with self.assertRaisesRegex(ValueError, "checksum"):
                    verify_runtime(lock, root / "hl")
                manifest = self.fixture(root, lock)
                manifest["files"].pop("ssl.hdll")
                (root / "manifest.json").write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, "provenance"):
                    verify_runtime(lock, root / "hl")

    def test_hashlink_rejects_wrong_vm_version_and_source_revision(self):
        _, lock = load_config()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.fixture(root, lock)
            with patch("bench.hashlink.capture", return_value="0.0.0"):
                with self.assertRaisesRegex(ValueError, "version"):
                    verify_runtime(lock, root / "hl")
            manifest["revision"] = "f" * 40
            (root / "manifest.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "provenance"):
                verify_runtime(lock, root / "hl")

    def test_hashlink_health_requires_actual_target_and_jit_mode(self):
        config, _ = load_config()
        for case in (case for case in config["cases"] if case["target"] == "hl"):
            health = {"run_id": "owned", "case_id": case["id"], "target": "hl", "driver": case["driver"],
                      "workers": 2, "virtual_thread": False, "hashlink_mode": "jit"}
            validate_health(health, case, 2, "owned", {})
            for key, bad in (("target", "neko"), ("hashlink_mode", "c"), ("hashlink_mode", None), ("virtual_thread", True)):
                with self.assertRaises(RuntimeError):
                    validate_health({**health, key: bad}, case, 2, "owned", {})
