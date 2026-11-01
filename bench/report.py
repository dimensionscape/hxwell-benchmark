import csv
import json
from pathlib import Path
import statistics


def summarize(trials, expected_repetitions):
    groups = {}
    for trial in trials:
        key = (trial["case_id"], trial["workload"], trial["connections"])
        groups.setdefault(key, []).append(trial)
    rows = []
    for (case, workload, connections), samples in sorted(groups.items()):
        valid = len(samples) == expected_repetitions and all(sample.get("valid") for sample in samples)
        row = {"case": case, "workload": workload, "connections": connections, "valid": valid,
               "samples": len(samples), "expected_samples": expected_repetitions}
        if valid:
            rates = [sample["rps"] for sample in samples]
            row.update(rps_median=statistics.median(rates), rps_min=min(rates), rps_max=max(rates),
                       p50_ms_median=statistics.median(sample["wrk"]["latency_us"]["p50"] / 1000 for sample in samples),
                       p95_ms_median=statistics.median(sample["wrk"]["latency_us"]["p95"] / 1000 for sample in samples),
                       p99_ms_median=statistics.median(sample["wrk"]["latency_us"]["p99"] / 1000 for sample in samples))
            memory = [sample["server"].get("peak_group_rss_bytes") for sample in samples]
            row["peak_group_rss_mib"] = max(memory) / 1048576 if all(value is not None for value in memory) else None
            row["client_saturated"] = any(sample.get("client_saturated", False) for sample in samples)
        else:
            row["errors"] = "; ".join(sample.get("error", "") for sample in samples if not sample.get("valid")) or "missing repetitions"
        rows.append(row)
    return rows


def write_reports(report, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    rows = summarize(report["trials"], report["profile"]["repetitions"])
    report["summary"] = rows
    (output / "results.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    columns = ["case", "workload", "connections", "valid", "samples", "expected_samples", "rps_median", "rps_min", "rps_max",
               "p50_ms_median", "p95_ms_median", "p99_ms_median", "peak_group_rss_mib", "client_saturated", "errors"]
    with (output / "summary.csv").open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    lines = ["# HxWell benchmark", "", f"Profile: **{report['profile_name']}**. Protocol: HTTP/1.1, loopback, keep-alive requested.",
             "All cases in this report ran sequentially on the same host. Build time is excluded.", "",
             "RPS and latency columns are medians across repetitions; a median of per-trial p99 is not a pooled p99.",
             "Invalid or incomplete groups have no performance score. `*` flags a possibly saturated load generator.", ""]
    if not report["profile"]["performance_run"]:
        lines += ["**Smoke run: validates the harness only; do not interpret these short measurements as performance results.**", ""]
    lines += ["| Case | Workload | Connections | Samples | Median req/s | p50 ms | p95 ms | p99 ms | Peak RSS MiB | Status |",
              "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    for row in rows:
        def number(key):
            value = row.get(key)
            return "—" if value is None else f"{value:.2f}"
        status = "OK" + ("*" if row.get("client_saturated") else "") if row["valid"] else "INVALID"
        lines.append(f"| {row['case']} | {row['workload']} | {row['connections']} | {row['samples']}/{row['expected_samples']} | "
                     + " | ".join(number(key) for key in ["rps_median", "p50_ms_median", "p95_ms_median", "p99_ms_median", "peak_group_rss_mib"])
                     + f" | {status} |")
    lines += ["", "## Run configuration", "", "```json", json.dumps({"profile": report["profile"], "hardware": report["hardware"],
              "affinity": report["affinity"], "versions": report["versions"], "seed": report["seed"]}, indent=2), "```", "",
              "Raw per-trial metrics, error counters, preflight responses, CPU/process-group measurements, dependency revisions and commands are in `results.json`.",
              "Server/client logs are retained beside this report. Hosted-runner results are noisy and are not production capacity or SLO guarantees.", ""]
    (output / "summary.md").write_text("\n".join(lines))
    return rows
