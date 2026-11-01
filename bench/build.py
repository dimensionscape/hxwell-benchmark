import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import tempfile
import time

from .config import ROOT
from .dependencies import capture, command, local_haxelib, sha256


def build(config, lock, case, deps, output, haxe="haxe"):
    deps, output = Path(deps).resolve(), Path(output).resolve()
    if capture([haxe, "--version"]) != config["haxe_version"]:
        raise ValueError(f"This locked suite requires Haxe {config['haxe_version']}")
    destination = output / case["id"]
    if destination.exists():
        raise ValueError(f"Refusing to overwrite {destination}; choose a fresh output directory")
    output.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f"build-{case['id']}-", dir=output))
    for directory in ("resources/views", "public"):
        (work / directory).mkdir(parents=True)
    libraries = local_haxelib(work, deps, lock, case)
    target = case["target"]
    args = [haxe, "-lib", "hxwell-crossbyte" if case["driver"] == "crossbyte" else "hxwell",
            "-cp", str(ROOT / "app/src"), "-D", "disable_env", "-D", "disable_cli", "-D", "analyzer-optimize",
            "-D", f"bench_case={case['id']}", "-D", f"bench_driver={case['driver']}", "-D", f"bench_{case['driver']}"]
    jars = []
    if case["driver"] == "undertow":
        for artifact in lock["maven"]:
            path = deps / "maven" / Path(artifact["path"]).name
            if not path.exists() or sha256(path) != artifact["sha256"]:
                raise ValueError(f"Missing/unverified jar {path}; run fetch first")
            jars.append(path)
            args += ["--java-lib", str(path)]
    if target == "jvm":
        args += ["-lib", "hxjava", "--jvm", "server.jar"]
        launch = ["java", "-Xms256m", "-Xmx256m", "-cp", "server.jar" + (os.pathsep + "lib/*" if jars else ""), "hx.well.HxWell"]
    elif target == "neko":
        args += ["--neko", "server.n"]
        launch = ["neko", "server.n"]
    elif target == "js":
        args += ["-lib", "hxnodejs", "-D", "js-es=6", "--js", "server.js"]
        launch = ["node", "--max-old-space-size=256", "server.js"]
    elif target == "cpp":
        args += ["--cpp", "native"]
        launch = ["./server"]
    else:
        raise ValueError(f"Unsupported target: {target}")
    (work / "command.json").write_text(json.dumps(args, indent=2) + "\n")
    started = time.monotonic()
    with (work / "build.log").open("w") as log:
        command(args, cwd=work, stdout=log, stderr=subprocess.STDOUT, timeout=900)
    destination.mkdir()
    for directory in ("public", "cache", "session"):
        (destination / directory).mkdir()
    if target == "cpp":
        shutil.copy2(work / "native/HxWell", destination / "server")
    else:
        suffix = {"jvm": "jar", "neko": "n", "js": "js"}[target]
        shutil.copy2(work / f"server.{suffix}", destination / f"server.{suffix}")
    if jars:
        (destination / "lib").mkdir()
        for path in jars:
            shutil.copy2(path, destination / "lib" / path.name)
    if target == "js":
        for name in ("package.json", "package-lock.json"):
            shutil.copy2(ROOT / "app" / name, destination / name)
        with (work / "npm.log").open("w") as log:
            command(["npm", "ci", "--no-audit", "--no-fund"], cwd=destination, stdout=log, stderr=subprocess.STDOUT, timeout=180)
    files = {str(path.relative_to(destination)): sha256(path) for path in destination.rglob("*") if path.is_file()}
    metadata = {"schema_version": 1, "case": case, "command": launch, "haxe_version": config["haxe_version"],
                "build_seconds": time.monotonic() - started, "build_os": platform.system(), "build_arch": platform.machine(),
                "repositories": lock["repositories"], "libraries": libraries, "files": files,
                "build_command": args, "workloads_sha256": sha256(ROOT / "benchmark.json")}
    (destination / "build.json").write_text(json.dumps(metadata, indent=2) + "\n")
    shutil.copy2(work / "build.log", destination / "build.log")
    with tarfile.open(output / f"{case['id']}.tar.gz", "w:gz") as archive:
        archive.add(destination, arcname=case["id"])
    print(f"Built {case['id']} -> {destination}", flush=True)
    return destination


def unpack(archives, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for path in sorted(Path(archives).rglob("*.tar.gz")):
        with tarfile.open(path) as archive:
            archive.extractall(destination, filter="data")
