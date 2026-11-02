import contextlib
from datetime import datetime, timezone
import http.client
import json
import math
import os
from pathlib import Path
import platform
import random
import resource
import shutil
import signal
import socket
import subprocess
import time
import uuid

from .config import ROOT, crossbyte_repository_name, load_config
from .dependencies import sha256
from .metrics import ProcessMeter
from .hashlink import verify_runtime
from .report import write_reports

MARKER = "HXWELL_BENCH_JSON "


def version(command):
    try:
        process = subprocess.run(command, capture_output=True, text=True, timeout=10)
        return (process.stdout + process.stderr).strip().splitlines()[0]
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return None


def hardware():
    result = {"os": platform.system(), "release": platform.release(), "arch": platform.machine(), "logical_cpus": os.cpu_count()}
    if Path("/proc/cpuinfo").exists():
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                result["cpu_model"] = line.split(":", 1)[1].strip()
                break
        result["memory"] = next((line for line in Path("/proc/meminfo").read_text().splitlines() if line.startswith("MemTotal:")), None)
        for name in ("cpu.max", "memory.max", "cpuset.cpus.effective"):
            path = Path("/sys/fs/cgroup") / name
            if path.exists():
                result[name] = path.read_text().strip()
    return result


def affinity(enabled=True):
    if enabled and hasattr(os, "sched_getaffinity") and shutil.which("taskset"):
        cpus = sorted(os.sched_getaffinity(0))
        if len(cpus) >= 2:
            boundary = max(1, len(cpus) // 2)
            return {"enabled": True, "server": cpus[:boundary], "client": cpus[boundary:]}
    return {"enabled": False, "server": [], "client": [], "reason": "CPU pinning unavailable or disabled"}


def pinned(command, cpus):
    return ["taskset", "-c", ",".join(map(str, cpus)), *command] if cpus else command


def ensure_file_limit(connections):
    """Reserve socket/control descriptors in this process and its future children."""
    before, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    required = max(1024, connections * 2 + 256)
    after = before
    if before != resource.RLIM_INFINITY and before < required:
        if hard != resource.RLIM_INFINITY and hard < required:
            raise ValueError(f"Need at least {required} file descriptors for {connections} connections; hard limit is {hard}")
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (required, hard))
        except (OSError, ValueError) as error:
            raise ValueError(f"Cannot raise the file descriptor limit to {required}: {error}") from error
        after = required
    def label(value):
        return "unlimited" if value == resource.RLIM_INFINITY else value
    return {"soft_before": label(before), "soft_after": label(after), "hard": label(hard), "required": required}


def stop_group(process):
    # Kill only the session created by this harness, including Node workers.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=5)


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def request(port, path, method="GET", body=None, connection=None):
    own = connection is None
    connection = connection or http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        headers = {"Accept-Encoding": "identity", "Connection": "keep-alive"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read()
        if response.status != 200:
            raise ValueError(f"{path}: expected status 200, got {response.status}")
        if response.getheader("Content-Encoding") not in (None, "identity"):
            raise ValueError("Unexpected compression in the HTTP/1.1 identity benchmark")
        return data, dict((name.lower(), value) for name, value in response.getheaders())
    finally:
        if own:
            connection.close()


def preflight(port, workload):
    with contextlib.closing(http.client.HTTPConnection("127.0.0.1", port, timeout=3)) as connection:
        data, headers = request(port, workload["path"], workload["method"], workload.get("body"), connection)
        first_socket = connection.sock
        if workload["content_type"] not in headers.get("content-type", ""):
            raise ValueError("Wrong Content-Type")
        if "expected_text" in workload and data.decode() != workload["expected_text"]:
            raise ValueError("Response body failed plaintext validation")
        if "expected_json" in workload and json.loads(data) != workload["expected_json"]:
            raise ValueError("Response body failed JSON validation")
        again, _ = request(port, workload["path"], workload["method"], workload.get("body"), connection)
        if first_socket is None or connection.sock is not first_socket or again != data:
            raise ValueError("Persistent connection preflight failed")
        return {"bytes": len(data), "sha256": __import__("hashlib").sha256(data).hexdigest(), "keep_alive_verified": True}


def verify_bundle(bundle, case):
    metadata = json.loads((bundle / "build.json").read_text())
    if metadata["case"] != case:
        raise ValueError("Build metadata does not match requested case")
    if metadata["build_os"] != platform.system() or metadata["build_arch"] != platform.machine():
        raise ValueError("Build OS/architecture does not match measurement host")
    if metadata["workloads_sha256"] != sha256(ROOT / "benchmark.json"):
        raise ValueError("Benchmark configuration changed after building")
    if case["driver"] == "crossbyte":
        _, lock = load_config()
        if metadata.get("crossbyte_repository") != lock["repositories"][crossbyte_repository_name(case)]:
            raise ValueError("Crossbyte source provenance does not match the requested implementation")
    for name, expected in metadata["files"].items():
        path = (bundle / name).resolve()
        if not path.is_relative_to(bundle.resolve()) or sha256(path) != expected:
            raise ValueError(f"Build artifact checksum mismatch: {name}")
    return metadata


def validate_health(health, case, workers, token, metadata):
    if health.get("run_id") != token or health.get("case_id") != case["id"]:
        raise RuntimeError("Port belongs to a different process/build; refusing to benchmark it")
    if health.get("target") != case["target"] or health.get("driver") != case["driver"] or health.get("workers") != workers:
        raise RuntimeError("Server identity/settings do not match the benchmark")
    expected_virtual = case["target"] == "jvm" and case["driver"] == "crossbyte"
    if health.get("virtual_thread") is not expected_virtual:
        raise RuntimeError("Unexpected request thread dispatch mode")
    if case["target"] == "jvm" and case["driver"] == "crossbyte":
        expected_io_virtual = case.get("io_threads", "platform") == "virtual"
        if health.get("io_virtual_thread") is not expected_io_virtual:
            raise RuntimeError("Crossbyte request I/O runtime has the wrong actual JVM thread type")
    if case["target"] == "hl" and health.get("hashlink_mode") != "jit":
        raise RuntimeError("HashLink benchmark did not use the expected JIT mode")
    if case["target"] == "cpp":
        version = metadata.get("libraries", {}).get("libhxcpp")
        if not version or health.get("cpp_runtime") != "libhxcpp" or health.get("cpp_runtime_version") != version:
            raise RuntimeError("C++ benchmark did not use the pinned libhxcpp runtime")
    if "poll_backend" in case and health.get("poll_backend") != case["poll_backend"]:
        raise RuntimeError("Poll backend mismatch or fallback; refusing to score this run")
    if case.get("poll_backend") == "libuv":
        version = metadata.get("native_libraries", {}).get("libuv", {}).get("version")
        if not version or health.get("libuv_version") != version:
            raise RuntimeError("Native libuv version does not match the build")


def check_health(port, case, workers, token, metadata):
    data, _ = request(port, "/health")
    health = json.loads(data)
    validate_health(health, case, workers, token, metadata)
    return health


@contextlib.contextmanager
def server(bundle, case, workers, capacity, cpus, log_path):
    metadata = verify_bundle(bundle, case)
    port, token = free_port(), uuid.uuid4().hex
    env = dict(os.environ)
    for name in ("JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS", "NODE_OPTIONS"):
        env.pop(name, None)
    env.update(BENCH_PORT=str(port), BENCH_WORKERS=str(workers), BENCH_MAX_CONNECTIONS=str(capacity), BENCH_RUN_ID=token)
    with log_path.open("w") as log:
        process = subprocess.Popen(pinned(metadata["command"], cpus), cwd=bundle, env=env,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            deadline = time.monotonic() + 30
            health = None
            last_error = ""
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"Server exited with {process.returncode}; see {log_path}")
                try:
                    health = check_health(port, case, workers, token, metadata)
                    break
                except (OSError, ValueError, http.client.HTTPException) as error:
                    last_error = str(error)
                    time.sleep(0.1)
            else:
                raise TimeoutError(f"Readiness timed out: {last_error}; see {log_path}")
            yield process, port, health, metadata
        finally:
            stop_group(process)


def parse_wrk(text):
    markers = [line[len(MARKER):] for line in text.splitlines() if line.startswith(MARKER)]
    if len(markers) != 1:
        raise ValueError("wrk did not produce exactly one structured result")
    result = json.loads(markers[0])
    for key in ("requests", "bytes"):
        value = result[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or int(value) != value:
            raise ValueError("Invalid wrk request/byte counters")
    if not math.isfinite(result["duration_us"]) or result["duration_us"] <= 0 or result["requests"] <= 0:
        raise ValueError("wrk completed no requests or reported an invalid duration")
    for key in ("p50", "p95", "p99", "mean", "max"):
        value = result["latency_us"][key]
        if value is None or not math.isfinite(value) or value < 0:
            raise ValueError("Invalid latency statistics")
    for key in ("connect", "read", "write", "status", "timeout"):
        value = result["errors"][key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or int(value) != value:
            raise ValueError("Invalid wrk error counters")
    lat = result["latency_us"]
    if not lat["p50"] <= lat["p95"] <= lat["p99"] <= lat["max"]:
        raise ValueError("Latency percentiles are not ordered")
    return result


def load(wrk, process, port, workload, connections, threads, seconds, cpus, logfile, timeout_seconds=2):
    args = [str(wrk), "-t", str(threads), "-c", str(connections), "-d", f"{seconds}s", "--timeout", f"{timeout_seconds:g}s", "--latency",
            "-s", str(ROOT / "load/report.lua"), f"http://127.0.0.1:{port}{workload['path']}"]
    env = {**os.environ, "BENCH_SCENARIO": workload["id"]}
    server_meter = ProcessMeter(process.pid)
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    baseline_cpu = usage.ru_utime + usage.ru_stime
    started = time.monotonic()
    with logfile.open("w") as log:
        client = subprocess.Popen(pinned(args, cpus), env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        client_meter = ProcessMeter(client.pid, existing=False)
        try:
            deadline = started + seconds + timeout_seconds + 15
            while client.poll() is None:
                if time.monotonic() > deadline:
                    raise TimeoutError("wrk exceeded its bounded runtime")
                if process.poll() is not None:
                    raise RuntimeError("Server exited during load")
                server_meter.sample()
                client_meter.sample()
                time.sleep(0.2)
            server_meter.sample()
            if client.returncode:
                raise RuntimeError(f"wrk exited with code {client.returncode}; see {logfile}")
        finally:
            stop_group(client)
    elapsed = time.monotonic() - started
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    client_cpu = max(0, usage.ru_utime + usage.ru_stime - baseline_cpu)
    result = parse_wrk(logfile.read_text())
    duration = result["duration_us"] / 1_000_000
    client_result = client_meter.result(duration)
    client_result.update(cpu_seconds=client_cpu, cpu_cores_used=client_cpu / duration, cpu_accounting="waited-child resource usage")
    return {"wrk": result, "server": server_meter.result(duration), "client": client_result, "wall_seconds": elapsed,
            "command": pinned(args, cpus), "rps": result["requests"] / duration,
            "client_saturated": client_cpu / duration >= 0.95 * max(1, len(cpus) or threads)}


def run(config, lock, cases, bundles, output, profile_name, wrk, seed=1337, pin=True, workers=None):
    profile = config["profiles"][profile_name]
    timeout_seconds = profile.get("request_timeout_seconds", 2)
    if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 60:
        raise ValueError("Request timeout must be a finite number in (0, 60] seconds")
    output, bundles = Path(output).resolve(), Path(bundles).resolve()
    output.mkdir(parents=True, exist_ok=False)
    wrk_version = version([str(wrk), "--version"])
    if not wrk_version or "4.2.0" not in wrk_version:
        raise ValueError(f"Need pinned wrk 4.2.0; found {wrk_version}")
    hashlink = verify_runtime(lock) if any(case["target"] == "hl" for case in cases) else None
    cpu = affinity(pin)
    workers = workers or (len(cpu["server"]) if cpu["enabled"] else 2)
    if workers < 1 or workers > 256:
        raise ValueError("workers must be in 1..256")
    threads = min(len(cpu["client"]) if cpu["enabled"] else 2, min(profile["connections"]))
    # wrk divides connections across threads; keep the requested count exact.
    while any(count % threads for count in profile["connections"]):
        threads -= 1
    file_limits = ensure_file_limit(max(profile["connections"]))
    report = {"schema_version": 1, "started_at": datetime.now(timezone.utc).isoformat(), "profile_name": profile_name,
              "profile": profile, "seed": seed, "hardware": hardware(), "affinity": cpu,
              "settings": {"server_workers": workers, "client_threads": threads, "protocol": "HTTP/1.1", "middleware": "none", "compression": "identity",
                           "request_timeout_seconds": timeout_seconds, "file_descriptor_limits": file_limits},
              "versions": {"wrk": wrk_version, "java": version(["java", "-version"]), "hashlink": hashlink,
                           "node": version(["node", "--version"]), "repositories": lock["repositories"]},
              "scripts": {"load/report.lua": sha256(ROOT / "load/report.lua"), "benchmark.json": sha256(ROOT / "benchmark.json")},
              "selected_cases": cases, "expected_trials": len(cases) * len(config["workloads"]) * len(profile["connections"]) * profile["repetitions"],
              "success": False, "builds": {}, "trials": []}
    try:
        for repetition in range(profile["repetitions"]):
            order = list(cases)
            random.Random(seed + repetition).shuffle(order)
            for case in order:
                logdir = output / f"{case['id']}-r{repetition + 1}"
                logdir.mkdir()
                combinations = [(work, count) for work in config["workloads"] for count in profile["connections"]]
                random.Random(seed + repetition).shuffle(combinations)
                completed = set()
                try:
                    with server(bundles / case["id"], case, workers, max(1024, max(profile["connections"]) * 4), cpu["server"], logdir / "server.log") as (process, port, health, metadata):
                        report["builds"][case["id"]] = metadata
                        for workload, connections in combinations:
                            label = f"{workload['id']}-c{connections}"
                            trial = {"case_id": case["id"], "workload": workload["id"], "connections": connections,
                                     "repetition": repetition + 1, "valid": False, "health": health}
                            try:
                                trial["health"] = check_health(port, case, workers, health["run_id"], metadata)
                                trial["preflight"] = preflight(port, workload)
                                warmup = load(wrk, process, port, workload, connections, threads, profile["warmup_seconds"], cpu["client"], logdir / f"{label}-warmup.log", timeout_seconds)
                                trial["warmup"] = warmup
                                if any(warmup["wrk"]["errors"].values()):
                                    raise ValueError(f"Warmup errors: {warmup['wrk']['errors']}")
                                trial.update(load(wrk, process, port, workload, connections, threads, profile["duration_seconds"], cpu["client"], logdir / f"{label}-measure.log", timeout_seconds))
                                if any(trial["wrk"]["errors"].values()):
                                    raise ValueError(f"Measurement errors: {trial['wrk']['errors']}")
                                if trial["server"].get("process_tree_changed"):
                                    raise ValueError("Server process group changed during measurement")
                                preflight(port, workload)
                                trial["health_after"] = check_health(port, case, workers, health["run_id"], metadata)
                                trial["valid"] = True
                                print(f"OK {case['id']} {label} r{repetition + 1}: {trial['rps']:.1f} req/s", flush=True)
                            except (OSError, ValueError, RuntimeError, TimeoutError, http.client.HTTPException) as error:
                                trial["error"] = str(error)
                                # Keep the first load error, and capture backend
                                # identity even for a rejected stress trial.
                                try:
                                    data, _ = request(port, "/health")
                                    trial["health_after"] = json.loads(data)
                                    validate_health(trial["health_after"], case, workers, health["run_id"], metadata)
                                except (OSError, ValueError, RuntimeError, TimeoutError, http.client.HTTPException) as health_error:
                                    trial["health_after_error"] = str(health_error)
                                print(f"INVALID {case['id']} {label}: {error}", flush=True)
                            report["trials"].append(trial)
                            completed.add((workload["id"], connections))
                            write_reports(report, output)
                except (OSError, ValueError, RuntimeError, TimeoutError) as error:
                    for workload, connections in combinations:
                        if (workload["id"], connections) not in completed:
                            report["trials"].append({"case_id": case["id"], "workload": workload["id"], "connections": connections,
                                                    "repetition": repetition + 1, "valid": False, "error": str(error)})
                    print(f"INVALID {case['id']}: {error}", flush=True)
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["success"] = len(report["trials"]) == report["expected_trials"] and all(trial["valid"] for trial in report["trials"])
        write_reports(report, output)
    if not report["success"]:
        raise RuntimeError(f"Some benchmarks were invalid; inspect {output / 'summary.md'}")
    return output
