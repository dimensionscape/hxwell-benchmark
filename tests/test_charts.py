import base64
import argparse
import copy
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from bench.charts import ASSET_PATH, START, END, arguments, asset_names, chart_views, dashboard, render, replace_readme, run_metadata, target_filename
from bench.publish import ApiError, newer_than, publish, trusted_environment


def config():
    return {"cases": [{"id": "jvm-socket", "target": "jvm"}, {"id": "hl-socket", "target": "hl"}], "workloads": [{"id": "plaintext"}, {"id": "json"}],
            "profiles": {"ci": {"warmup_seconds": 1, "duration_seconds": 2, "repetitions": 2, "connections": [16], "performance_run": True},
                         "smoke": {"warmup_seconds": 1, "duration_seconds": 1, "repetitions": 1, "connections": [4], "performance_run": False},
                         "full": {"warmup_seconds": 1, "duration_seconds": 2, "repetitions": 2, "connections": [16, 64], "performance_run": True},
                         "stress": {"warmup_seconds": 1, "duration_seconds": 2, "repetitions": 2, "connections": [1000], "request_timeout_seconds": 10, "performance_run": True}}}


def report(profile="ci", selection=None):
    cfg = config()
    profile_data = cfg["profiles"][profile]
    cases = cfg["cases"] if selection is None else [case for case in cfg["cases"] if case["id"] in selection]
    trials = []
    for case in cases:
        for workload in cfg["workloads"]:
            for count in profile_data["connections"]:
                for repetition in range(1, profile_data["repetitions"] + 1):
                    trials.append({"case_id": case["id"], "workload": workload["id"], "connections": count, "repetition": repetition,
                        "valid": True, "rps": 999999, "wrk": {"requests": 100 * repetition, "duration_us": 1_000_000,
                        "errors": {"connect": 0, "read": 0, "write": 0, "status": 0, "timeout": 0},
                        "latency_us": {"p50": 10, "p95": 20, "p99": 30 * repetition, "max": 100}},
                        "server": {"peak_group_rss_bytes": repetition * 1048576}, "client_saturated": False})
    return {"schema_version": 1, "profile_name": profile, "profile": profile_data, "selected_cases": cases,
            "expected_trials": len(trials), "trials": trials, "success": True, "finished_at": "2026-11-01T03:35:07+00:00",
            "hardware": {"cpu_model": "fixture"}, "summary": [{"rps_median": 999999}]}


def metadata(number=2, attempt=1):
    return run_metadata("hxwell/hxwell-benchmark", 100 + number, number, attempt, "a" * 40)


def value(profile="ci"):
    return dashboard(report(profile), config(), metadata(), "success", profile)


class ChartTests(unittest.TestCase):
    def test_stress_profile_is_accepted_and_connection_count_is_rendered(self):
        parser = argparse.ArgumentParser()
        arguments(parser)
        args = parser.parse_args(["--report", "results.json", "--output", "charts", "--profile", "stress"])
        self.assertEqual("stress", args.profile)
        data = value("stress")
        self.assertEqual("complete", data["status"])
        self.assertEqual({1000}, {row["connections"] for row in data["rows"]})
        with tempfile.TemporaryDirectory() as folder:
            render(data, folder)
            self.assertIn("1000 connections", (Path(folder) / "summary.svg").read_text())

    def test_recomputes_metrics_in_correct_units_from_raw_trials(self):
        data = value()
        self.assertEqual("complete", data["status"])
        self.assertEqual(8, data["valid_trials"])
        self.assertEqual(150, data["rows"][0]["rps_median"])
        self.assertEqual(0.045, data["rows"][0]["p99_ms_median"])
        self.assertEqual(2, data["rows"][0]["peak_group_rss_mib"])

    def test_missing_and_duplicate_repetitions_have_no_score(self):
        for modify in (lambda r: r["trials"].pop(), lambda r: r["trials"][1].update(repetition=1)):
            source = report()
            modify(source)
            data = dashboard(source, config(), metadata(), "success")
            self.assertEqual("incomplete", data["status"])
            invalid = [row for row in data["rows"] if not row["valid"]]
            self.assertTrue(invalid)
            self.assertNotIn("rps_median", invalid[0])

    def test_error_counters_and_nonfinite_numbers_override_valid_flags(self):
        for modify in (lambda t: t["wrk"]["errors"].update(timeout=1),
                       lambda t: t["wrk"].update(duration_us=float("nan")),
                       lambda t: t["wrk"]["latency_us"].update(p99=-1),
                       lambda t: t["server"].update(process_tree_changed=True)):
            source = report()
            modify(source["trials"][0])
            data = dashboard(source, config(), metadata(), "success")
            self.assertFalse(data["rows"][0]["valid"])
            self.assertEqual("incomplete", data["status"])

    def test_failed_job_is_not_published_as_complete(self):
        data = dashboard(report(), config(), metadata(), "failure")
        self.assertEqual("incomplete", data["status"])
        self.assertTrue(all(row["valid"] for row in data["rows"]))

    def test_warmup_errors_never_receive_a_performance_score(self):
        source = report()
        source["trials"][0]["warmup"] = {"wrk": {"errors": {"connect": 0, "read": 22, "write": 0, "status": 0, "timeout": 0}}}
        data = dashboard(source, config(), metadata(), "success")
        self.assertEqual("incomplete", data["status"])
        self.assertEqual(7, data["valid_trials"])
        self.assertEqual(6, data["scored_trials"])
        self.assertEqual(1, data["rows"][0]["valid_samples"])
        self.assertFalse(data["rows"][0]["valid"])
        self.assertNotIn("rps_median", data["rows"][0])

    def test_missing_or_mismatched_report_produces_unavailable_dashboard(self):
        for source in (None, {}, {**report(), "profile_name": "other"}, {**report(), "selected_cases": []}):
            data = dashboard(source, config(), metadata(), "failure")
            self.assertEqual("unavailable", data["status"])
            self.assertEqual([], data["rows"])

    def test_partial_case_selection_and_connection_levels_remain_separate(self):
        data = dashboard(report(selection=["jvm-socket"]), config(), metadata(), "success", selection="jvm-socket")
        self.assertEqual(["jvm-socket"], data["cases"])
        self.assertEqual(4, data["expected_trials"])
        full = value("full")
        self.assertEqual(8, len(full["rows"]))
        self.assertEqual({16, 64}, {row["connections"] for row in full["rows"]})

    def test_readme_replacement_preserves_human_text_and_cache_busts_images(self):
        text = "Human intro\n" + START + "\nold\n" + END + "\nHuman ending\n"
        changed = replace_readme(text, value())
        self.assertTrue(changed.startswith("Human intro\n"))
        self.assertTrue(changed.endswith("\nHuman ending\n"))
        self.assertIn("summary.svg?run=102-1", changed)
        self.assertLess(changed.index("## Summary"), changed.index("## JVM"))
        self.assertLess(changed.index("## JVM"), changed.index("## HashLink / HL-JIT"))
        self.assertIn("target-jvm.svg?run=102-1", changed)
        self.assertIn("target-hl.svg?run=102-1", changed)
        self.assertEqual(1, changed.count(START))
        self.assertEqual(changed, replace_readme(changed, value()))
        for invalid in ("no markers", START + START + END, END + START):
            with self.assertRaises(ValueError):
                replace_readme(invalid, value())

    def test_svg_render_and_smoke_status_are_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            render(value(), root / "one")
            render(value(), root / "two")
            for filename in asset_names(value()):
                self.assertEqual((root / "one" / filename).read_bytes(), (root / "two" / filename).read_bytes())
                if filename.endswith(".svg"):
                    ET.parse(root / "one" / filename)
            render(value("smoke"), root / "smoke")
            text = (root / "smoke/summary.svg").read_text()
            self.assertIn("SMOKE ONLY", text)
            self.assertIn("NOT A PERFORMANCE RUN", text)
            self.assertNotIn(">150<", text)

    def test_target_views_group_configured_targets_and_reject_unsafe_names(self):
        cfg = config()
        # Group by explicit configuration, not a guessed case-ID prefix.
        cfg["cases"][1]["target"] = "jvm"
        data = dashboard(report(), cfg, metadata(), "success")
        views = chart_views(data)
        self.assertEqual(["summary.svg", "target-jvm.svg"], [v["filename"] for v in views])
        self.assertEqual(["jvm-socket", "hl-socket"], views[1]["cases"])
        for name in ("../README", "jvm/other", "", "a.svg", "x\\n"):
            with self.assertRaises(ValueError):
                target_filename(name)

    def test_target_svgs_contain_only_their_cases_and_all_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            render(value(), directory)
            for target, other in (("jvm", "hl"), ("hl", "jvm")):
                text = (Path(directory) / f"target-{target}.svg").read_text()
                self.assertIn(f"{target}-socket", text)
                self.assertNotIn(f"{other}-socket", text)
                for label in ("RPS", "p99", "RSS", "PLAINTEXT", "JSON"):
                    self.assertIn(label, text)

    def test_subset_replaces_previous_target_charts_without_removing_unrelated_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            render(value(), root)
            (root / "notes.txt").write_text("human notes")
            (root / "throughput.svg").write_text("legacy plot")
            subset = dashboard(report(selection=["jvm-socket"]), config(), metadata(), "success", selection="jvm-socket")
            render(subset, root)
            self.assertEqual({"summary.svg", "target-jvm.svg", "manifest.json", "notes.txt"}, {p.name for p in root.iterdir()})
            self.assertNotIn("## HashLink / HL-JIT", replace_readme(START + END, subset))
            self.assertEqual("human notes", (root / "notes.txt").read_text())

    def test_unavailable_memory_is_not_zero(self):
        source = report()
        for trial in source["trials"]:
            trial["server"] = {"available": False}
        data = dashboard(source, config(), metadata(), "success")
        self.assertIsNone(data["rows"][0]["peak_group_rss_mib"])


class FakeApi:
    def __init__(self, previous=None, conflict=False, existing=()):
        self.previous = previous
        self.conflict = conflict
        self.existing = existing
        self.calls = []
        self.head = "head-a"
        self.readmes = {"head-a": "Before\n" + START + "\nold\n" + END + "\nAfter\n",
                        "head-b": "Human edit\n" + START + "\nold\n" + END + "\nAfter\n"}

    def request(self, method, path, payload=None):
        self.calls.append((method, path, copy.deepcopy(payload)))
        if method == "GET" and path.endswith("git/ref/heads/main"):
            return {"object": {"sha": self.head}}
        if method == "GET" and "/contents/" in path:
            if f"/contents/{ASSET_PATH}?" in path:
                return [{"name": name, "type": "file"} for name in self.existing]
            revision = path.split("?ref=")[1]
            if "README.md" in path:
                text = self.readmes[revision]
            elif self.previous is None:
                raise ApiError(404, method, path)
            else:
                text = json.dumps(self.previous)
            return {"type": "file", "encoding": "base64", "content": base64.b64encode(text.encode()).decode()}
        if method == "GET" and "/git/commits/" in path:
            return {"tree": {"sha": "base-tree-" + self.head}}
        if method == "POST":
            return {"sha": "created-" + self.head}
        if method == "PATCH":
            assert payload["force"] is False
            if self.conflict and self.head == "head-a":
                self.head = "head-b"
                raise ApiError(422, method, path)
            return {}
        raise AssertionError((method, path))


class PublishTests(unittest.TestCase):
    def assets(self, directory, data=None):
        for name in asset_names(data or value()):
            (Path(directory) / name).write_text("generated")

    def test_only_trusted_main_jobs_are_allowed(self):
        env = {"GITHUB_ACTIONS": "true", "GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_NAME": "push", "GITHUB_REPOSITORY": "hxwell/hxwell-benchmark"}
        self.assertTrue(trusted_environment(env, "hxwell/hxwell-benchmark"))
        for key, bad in (("GITHUB_REF", "refs/heads/feature"), ("GITHUB_EVENT_NAME", "pull_request"), ("GITHUB_ACTIONS", "false")):
            self.assertFalse(trusted_environment({**env, key: bad}, "hxwell/hxwell-benchmark"))
        self.assertFalse(trusted_environment(env, "someone/other"))

    def test_newer_run_wins_and_same_attempt_is_idempotent(self):
        current = {"run": metadata(3)}
        self.assertFalse(newer_than(current, {"run": metadata(2, 10)}))
        self.assertFalse(newer_than(current, {"run": metadata(3)}))
        self.assertTrue(newer_than(current, {"run": metadata(3, 2)}))

    def test_stale_publication_does_not_write_anything(self):
        api = FakeApi(previous={"run": metadata(3)})
        with tempfile.TemporaryDirectory() as directory:
            self.assets(directory)
            result = publish(api, value(), directory)
        self.assertEqual("skipped", result["status"])
        self.assertFalse(any(method != "GET" for method, _, _ in api.calls))

    def test_atomic_publication_only_changes_allowlisted_paths(self):
        api = FakeApi()
        with tempfile.TemporaryDirectory() as directory:
            self.assets(directory)
            result = publish(api, value(), directory)
        self.assertEqual("published", result["status"])
        tree = next(payload for method, path, payload in api.calls if method == "POST" and path.endswith("/git/trees"))
        self.assertEqual({"README.md"} | {f"{ASSET_PATH}/{name}" for name in asset_names(value())}, {entry["path"] for entry in tree["tree"]})

    def test_publication_removes_only_existing_obsolete_generated_charts(self):
        subset = dashboard(report(selection=["jvm-socket"]), config(), metadata(), "success", selection="jvm-socket")
        api = FakeApi(existing=("summary.svg", "target-jvm.svg", "target-neko.svg", "throughput.svg", "notes.svg", "manifest.json"))
        with tempfile.TemporaryDirectory() as directory:
            self.assets(directory, subset)
            publish(api, subset, directory)
        tree = next(payload for method, path, payload in api.calls if method == "POST" and path.endswith("/git/trees"))
        removed = {entry["path"] for entry in tree["tree"] if entry.get("sha", "present") is None}
        self.assertEqual({f"{ASSET_PATH}/target-neko.svg", f"{ASSET_PATH}/throughput.svg"}, removed)
        self.assertFalse(any(entry["path"].endswith("notes.svg") for entry in tree["tree"]))

    def test_non_fast_forward_retry_preserves_concurrent_readme_edit(self):
        api = FakeApi(conflict=True)
        with tempfile.TemporaryDirectory() as directory:
            self.assets(directory)
            result = publish(api, value(), directory)
        self.assertEqual("published", result["status"])
        trees = [payload for method, path, payload in api.calls if method == "POST" and path.endswith("/git/trees")]
        self.assertEqual(2, len(trees))
        readme = next(entry["content"] for entry in trees[-1]["tree"] if entry["path"] == "README.md")
        self.assertTrue(readme.startswith("Human edit\n"))
        self.assertEqual("base-tree-head-b", trees[-1]["base_tree"])


if __name__ == "__main__":
    unittest.main()
