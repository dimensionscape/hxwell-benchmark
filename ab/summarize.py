"""Compare base and tuned harness rounds: median req/s and p99 per case and workload."""
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

rps = defaultdict(list)
p99 = defaultdict(list)
cpu = defaultdict(list)
invalid = defaultdict(int)
cases, workloads = [], []
for directory in sorted(Path(sys.argv[1]).iterdir()):
    report = directory / "results.json"
    if not report.exists():
        continue
    variant = directory.name.split("-", 1)[1]
    for trial in json.loads(report.read_text())["trials"]:
        key = (trial["case_id"], trial["workload"], variant)
        if trial["case_id"] not in cases:
            cases.append(trial["case_id"])
        if trial["workload"] not in workloads:
            workloads.append(trial["workload"])
        if not trial.get("valid"):
            invalid[key] += 1
            continue
        rps[key].append(trial["rps"])
        p99[key].append(trial["wrk"]["latency_us"]["p99"] / 1000)
        cores = trial.get("server", {}).get("cpu_cores_used")
        if cores is not None:
            cpu[key].append(cores)


def median(values, digits=0):
    return f"{statistics.median(values):,.{digits}f}" if values else "-"


print("| Case | Workload | Base req/s | Tuned req/s | Change | Base p99 ms | Tuned p99 ms | Server cores base/tuned | Trials base/tuned |")
print("| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |")
for case in cases:
    for workload in workloads:
        b, t = (case, workload, "base"), (case, workload, "tuned")
        change = f"{(statistics.median(rps[t]) / statistics.median(rps[b]) - 1) * 100:+.1f}%" if rps[b] and rps[t] else "-"
        trials = f"{len(rps[b])}/{len(rps[t])}"
        if invalid[b] or invalid[t]:
            trials += f" (invalid {invalid[b]}/{invalid[t]})"
        print(f"| {case} | {workload} | {median(rps[b])} | {median(rps[t])} | {change} | {median(p99[b], 1)} | {median(p99[t], 1)} | {median(cpu[b], 2)}/{median(cpu[t], 2)} | {trials} |")
