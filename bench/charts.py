"""Render a validated benchmark dashboard and its managed README section."""
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import statistics

from .config import load_config, select_cases

START = "<!-- benchmark-charts:start -->"
END = "<!-- benchmark-charts:end -->"
ASSET_PATH = "docs/benchmarks/latest"
ASSET_NAMES = ("throughput.svg", "latency.svg", "memory.svg", "manifest.json")
METRICS = (
    ("throughput.svg", "rps_median", "Requests per second", "requests / second · higher is better"),
    ("latency.svg", "p99_ms_median", "p99 response latency", "milliseconds · lower is better"),
    ("memory.svg", "peak_group_rss_mib", "Peak server memory", "sampled process-group RSS · MiB"),
)
COLORS = ("#2563eb", "#7c3aed", "#0891b2", "#d97706", "#be185d", "#059669", "#475569")


def run_metadata(repository, run_id, run_number, attempt, source_sha):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Invalid GitHub repository")
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("Expected a full source commit SHA")
    if any(type(value) is not int or value <= 0 for value in (run_id, run_number, attempt)):
        raise ValueError("Run identifiers must be positive integers")
    return {"repository": repository, "id": run_id, "number": run_number, "attempt": attempt,
            "source_sha": source_sha, "url": f"https://github.com/{repository}/actions/runs/{run_id}"}


def finite(value, positive=False):
    return type(value) in (int, float) and math.isfinite(value) and (value > 0 if positive else value >= 0)


def validate_trial(trial):
    if trial.get("valid") is not True:
        return False
    try:
        wrk = trial["wrk"]
        if not finite(wrk["duration_us"], True) or not finite(wrk["requests"], True):
            return False
        if int(wrk["requests"]) != wrk["requests"]:
            return False
        if any(type(wrk["errors"][key]) not in (int, float) or wrk["errors"][key] != 0
               for key in ("connect", "read", "write", "status", "timeout")):
            return False
        lat = wrk["latency_us"]
        if not all(finite(lat[key]) for key in ("p50", "p95", "p99", "max")):
            return False
        return lat["p50"] <= lat["p95"] <= lat["p99"] <= lat["max"] and not trial.get("server", {}).get("process_tree_changed", False)
    except (KeyError, TypeError, ValueError):
        return False


def dashboard(report, config, run, outcome, profile_name="ci", selection="all"):
    """Recompute groups from raw trials; never trust a stored summary/valid flag alone."""
    cases = [case["id"] for case in select_cases(config, selection)]
    profile = config["profiles"][profile_name]
    workloads = [workload["id"] for workload in config["workloads"]]
    expected = len(cases) * len(workloads) * len(profile["connections"]) * profile["repetitions"]
    value = {"schema_version": 1, "run": run, "outcome": outcome, "profile_name": profile_name, "profile": profile,
             "cases": cases, "workloads": workloads, "expected_trials": expected, "valid_trials": 0,
             "finished_at": None, "rows": [], "status": "unavailable", "message": "No benchmark report was produced."}
    if report is not None:
        try:
            if report.get("schema_version") != 1 or report["profile_name"] != profile_name or report["profile"] != profile:
                raise ValueError("Report profile does not match this run.")
            if [case["id"] for case in report["selected_cases"]] != cases:
                raise ValueError("Report case selection does not match this run.")
            trials = report["trials"]
            if not isinstance(trials, list) or len(trials) > expected * 2:
                raise ValueError("Invalid trial list.")
            if report.get("finished_at"):
                finished = datetime.fromisoformat(report["finished_at"])
                if finished.tzinfo is None:
                    raise ValueError("Report timestamp has no timezone.")
                value["finished_at"] = finished.astimezone(timezone.utc).isoformat()
            grouped = {}
            bad_shape = report.get("expected_trials") != expected
            for trial in trials:
                key = (trial.get("case_id"), trial.get("workload"), trial.get("connections"))
                repetition = trial.get("repetition")
                if (key[0] not in cases or key[1] not in workloads or key[2] not in profile["connections"]
                        or type(repetition) is not int or not 1 <= repetition <= profile["repetitions"]):
                    bad_shape = True
                    continue
                grouped.setdefault(key, []).append(trial)
            for connections in profile["connections"]:
                for workload in workloads:
                    for case in cases:
                        samples = grouped.get((case, workload, connections), [])
                        valid = (len(samples) == profile["repetitions"]
                                 and {sample["repetition"] for sample in samples} == set(range(1, profile["repetitions"] + 1))
                                 and all(validate_trial(sample) for sample in samples))
                        row = {"case": case, "workload": workload, "connections": connections,
                               "valid": valid, "samples": len(samples)}
                        if valid:
                            value["valid_trials"] += len(samples)
                            rates = [sample["wrk"]["requests"] / (sample["wrk"]["duration_us"] / 1e6) for sample in samples]
                            memory = [sample.get("server", {}).get("peak_group_rss_bytes") for sample in samples]
                            row.update(rps_median=statistics.median(rates), rps_min=min(rates), rps_max=max(rates),
                                       p99_ms_median=statistics.median(sample["wrk"]["latency_us"]["p99"] / 1000 for sample in samples),
                                       peak_group_rss_mib=max(memory) / 1048576 if all(finite(n) for n in memory) else None,
                                       client_saturated=any(sample.get("client_saturated", False) for sample in samples))
                        value["rows"].append(row)
            complete = not bad_shape and value["finished_at"] is not None and len(trials) == expected and value["valid_trials"] == expected and report.get("success") is True and outcome == "success"
            value["status"] = ("complete" if profile["performance_run"] else "smoke") if complete else "incomplete"
            value["message"] = ("All selected measurements passed." if complete else "Incomplete or failed run; invalid groups have no score.")
            if not profile["performance_run"]:
                value["message"] = "Smoke validation only; no performance bars are published."
            value["hardware"] = {key: report.get("hardware", {}).get(key) for key in ("os", "arch", "cpu_model", "logical_cpus")}
            value["affinity"] = report.get("affinity", {})
        except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
            value.update(status="unavailable", valid_trials=0, rows=[], message="Benchmark report was missing or invalid; no scores are published.")
    return value


def render(value, output, preview=False):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": "DejaVu Sans", "svg.fonttype": "none", "svg.hashsalt": "hxwell-benchmark-charts-v1",
                         "axes.spines.top": False, "axes.spines.right": False, "axes.spines.left": False,
                         "font.size": 9, "axes.edgecolor": "#cbd5e1", "text.color": "#0f172a", "axes.labelcolor": "#334155"})
    cases, workloads, profile = value["cases"], value["workloads"], value["profile"]
    lookup = {(row["case"], row["workload"], row["connections"]): row for row in value["rows"]}
    heights = max(2.6, len(cases) * 0.4)
    status = {"complete": "COMPLETE", "incomplete": "INCOMPLETE / FAILED", "smoke": "SMOKE · NOT A PERFORMANCE RUN", "unavailable": "NO VALID REPORT"}[value["status"]]
    warning = value["status"] != "complete"
    for filename, metric, title, unit in METRICS:
        fig, axes = plt.subplots(len(profile["connections"]), len(workloads), squeeze=False,
                                 figsize=(12.8, heights * len(profile["connections"]) + 1.65), facecolor="#f8fafc")
        fig.suptitle(title, fontsize=18, fontweight="bold", x=0.02, ha="left", y=0.98).set_in_layout(False)
        detail = (f"{status}  |  {value['profile_name']}  |  {value['valid_trials']}/{value['expected_trials']} valid trials  |  "
                  f"{profile['repetitions']} repeats × {profile['duration_seconds']} s")
        fig.text(0.02, 0.90 if len(profile["connections"]) == 1 else 0.93, detail, fontsize=10,
                 color="#b45309" if warning else "#475569")
        for row_index, connections in enumerate(profile["connections"]):
            for column, workload in enumerate(workloads):
                ax = axes[row_index][column]
                ax.set_facecolor("white")
                ax.set_title(f"{workload.upper()} · {connections} connections", loc="left", fontsize=10, fontweight="bold", pad=10)
                ax.set_yticks(range(len(cases)), cases if column == 0 else [""] * len(cases))
                ax.tick_params(axis="y", length=0, labelsize=9)
                ax.invert_yaxis()
                ax.set_axisbelow(True)
                ax.grid(axis="x", color="#e2e8f0", linewidth=0.7)
                numbers = []
                for index, case in enumerate(cases):
                    entry = lookup.get((case, workload, connections), {})
                    number = entry.get(metric)
                    if entry.get("valid") and profile["performance_run"] and finite(number):
                        numbers.append(number)
                        ax.barh(index, number, height=0.63, color=COLORS[index % len(COLORS)])
                        if metric == "rps_median":
                            ax.errorbar(number, index, xerr=[[number - entry["rps_min"]], [entry["rps_max"] - number]],
                                        fmt="none", ecolor="#334155", capsize=2, linewidth=0.9)
                            numbers.append(entry["rps_max"])
                        label = f"{number:,.0f}" if metric == "rps_median" else f"{number:,.2f}"
                        if entry.get("client_saturated"):
                            label += " *"
                        label_at = entry["rps_max"] if metric == "rps_median" else number
                        ax.annotate(label, (label_at, index), xytext=(5, 0), textcoords="offset points", va="center", fontsize=8)
                    else:
                        label = "SMOKE ONLY" if not profile["performance_run"] else ("N/A" if entry.get("valid") else "INVALID / MISSING")
                        ax.text(0.02, index, label, transform=ax.get_yaxis_transform(), va="center", fontsize=8, color="#92400e")
                maximum = max(numbers, default=0)
                ax.set_xlim(0, maximum * 1.32 if maximum else 1)
                if not numbers:
                    ax.set_xticks([])
                else:
                    ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:,.0f}" if x >= 100 else f"{x:g}"))
                ax.set_xlabel(unit if profile["performance_run"] else "Validation result only", fontsize=8)
        fig.tight_layout(rect=(0.015, 0.075, 0.995, 0.86 if len(profile["connections"]) == 1 else 0.90), w_pad=2.4, h_pad=2)
        note = {
            "rps_median": "Bars: median requests/second across repetitions. Whiskers: minimum to maximum across repetitions.",
            "p99_ms_median": "Bars: median of per-trial p99 values across repetitions; this is not a pooled latency percentile.",
            "peak_group_rss_mib": "Bars: maximum sampled process-group RSS across repetitions. Shared pages may be counted more than once.",
        }[metric]
        fig.text(0.02, 0.045, note, fontsize=8, color="#475569")
        fig.text(0.02, 0.015, f"Run #{value['run']['number']}.{value['run']['attempt']} · {value['run']['source_sha'][:7]} · "
                 f"{value['finished_at'] or 'report unavailable'} · * client possibly saturated", fontsize=8, color="#64748b")
        metadata = {"Date": value["finished_at"] or "", "Creator": "hxwell-benchmark"}
        fig.savefig(output / filename, format="svg", metadata=metadata)
        if preview:
            fig.savefig(output / filename.replace(".svg", ".png"), dpi=140)
        plt.close(fig)
    (output / "manifest.json").write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def readme_block(value):
    run = value["run"]
    status = {"complete": "Complete", "incomplete": "Incomplete / failed", "smoke": "Smoke validation only", "unavailable": "No valid report"}[value["status"]]
    key = f"{run['id']}-{run['attempt']}"
    lines = [START, "## Latest benchmark charts", "",
             f"**{status}** · [Run #{run['number']} (attempt {run['attempt']})]({run['url']}) · "
             f"profile `{value['profile_name']}` · {value['valid_trials']}/{value['expected_trials']} valid trials.",
             f"Source: [`{run['source_sha'][:7]}`](https://github.com/{run['repository']}/commit/{run['source_sha']}). "
             f"Finished: `{value['finished_at'] or 'not available'}`.", "", value["message"], "",
             f"![Requests per second]({ASSET_PATH}/throughput.svg?run={key})", "",
             f"![p99 response latency]({ASSET_PATH}/latency.svg?run={key})", "",
             f"![Peak server memory]({ASSET_PATH}/memory.svg?run={key})", "",
             f"[Chart data and provenance]({ASSET_PATH}/manifest.json) · Full raw results are in the linked run's `benchmark-results` artifact.",
             "Only the selected cases and connection levels from this run are shown; older runs are not mixed into these charts.", "", END]
    return "\n".join(lines)


def replace_readme(text, value):
    if text.count(START) != 1 or text.count(END) != 1 or text.index(START) >= text.index(END):
        raise ValueError("README must contain exactly one ordered benchmark chart marker pair")
    start, end = text.index(START), text.index(END) + len(END)
    return text[:start] + readme_block(value) + text[end:]


def read_report(path):
    path = Path(path)
    if not path.exists():
        return None
    if path.stat().st_size > 25 * 1024 * 1024:
        raise ValueError("Report exceeds the chart input size limit")
    return json.loads(path.read_text())


def arguments(parser):
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", choices=("ci", "full", "smoke"), default="ci")
    parser.add_argument("--cases", default="all")
    parser.add_argument("--outcome", choices=("success", "failure", "cancelled", "skipped", "unknown"), default="success")
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", "hxwell/hxwell-benchmark"))
    parser.add_argument("--run-id", type=int, default=int(os.environ.get("GITHUB_RUN_ID", "0")))
    parser.add_argument("--run-number", type=int, default=int(os.environ.get("GITHUB_RUN_NUMBER", "0")))
    parser.add_argument("--attempt", type=int, default=int(os.environ.get("GITHUB_RUN_ATTEMPT", "1")))
    parser.add_argument("--source-sha", default=os.environ.get("GITHUB_SHA", ""))


def from_arguments(args):
    config, _ = load_config()
    run = run_metadata(args.repository, args.run_id, args.run_number, args.attempt, args.source_sha)
    try:
        report = read_report(args.report)
    except (OSError, ValueError):
        report = None
    return dashboard(report, config, run, args.outcome, args.profile, args.cases)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    arguments(parser)
    parser.add_argument("--readme", type=Path)
    parser.add_argument("--preview", action="store_true")
    args = parser.parse_args()
    value = from_arguments(args)
    render(value, args.output, args.preview)
    if args.readme:
        args.readme.write_text(replace_readme(args.readme.read_text(), value))
    print(f"Charts: {value['status']}, {value['valid_trials']}/{value['expected_trials']} valid trials -> {args.output}")


if __name__ == "__main__":
    main()
