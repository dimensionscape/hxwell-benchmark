import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def load_config():
    config = json.loads((ROOT / "benchmark.json").read_text())
    lock = json.loads((ROOT / "dependencies.lock.json").read_text())
    ids = [case["id"] for case in config["cases"]]
    if len(ids) != len(set(ids)) or any(not re.fullmatch(r"[a-z][a-z0-9-]+", value) for value in ids):
        raise ValueError("Case IDs must be unique safe names")
    for case in config["cases"]:
        if case["target"] not in ("jvm", "hl", "js", "cpp"):
            raise ValueError(f"Unsupported benchmark target: {case['target']}")
        if "poll_backend" in case and (case["target"] != "cpp" or case["driver"] != "crossbyte"
                                       or case["poll_backend"] not in ("builtin", "libuv")):
            raise ValueError("Explicit poll backends require a C++ Crossbyte case")
    for name, repo in lock["repositories"].items():
        if not re.fullmatch(r"[0-9a-f]{40}", repo["revision"]):
            raise ValueError(f"Repository {name} must use a full immutable commit")
    return config, lock


def select_cases(config, selection="all"):
    names = [case["id"] for case in config["cases"]] if selection == "all" else [s.strip() for s in selection.split(",")]
    allowed = {case["id"]: case for case in config["cases"]}
    if not names or any(name not in allowed for name in names) or len(names) != len(set(names)):
        raise ValueError(f"Choose unique cases from: {', '.join(allowed)}")
    return [allowed[name] for name in names]
