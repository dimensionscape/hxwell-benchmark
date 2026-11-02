import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import tempfile
import time

from .config import ROOT, crossbyte_repository_name
from .dependencies import capture, command, local_haxelib, sha256, verify_repository


def build_libuv(deps, work, lock, log):
    """Build the pinned libuv statically so bundles need no host libuv package."""
    source = deps / "libuv"
    verify_repository(source, lock["repositories"]["libuv"])
    build_dir, prefix = work / "libuv-build", work / "native-deps/libuv"
    command(["cmake", "-S", source, "-B", build_dir, "-DCMAKE_BUILD_TYPE=Release",
             "-DLIBUV_BUILD_SHARED=OFF", "-DLIBUV_BUILD_TESTS=OFF", "-DLIBUV_BUILD_BENCH=OFF",
             "-DBUILD_TESTING=OFF", "-DCMAKE_INSTALL_LIBDIR=lib", f"-DCMAKE_INSTALL_PREFIX={prefix}"],
            stdout=log, stderr=subprocess.STDOUT, timeout=120)
    command(["cmake", "--build", build_dir, "--target", "uv_a", "--parallel", "2"],
            stdout=log, stderr=subprocess.STDOUT, timeout=300)
    command(["cmake", "--install", build_dir], stdout=log, stderr=subprocess.STDOUT, timeout=120)
    archive = prefix / "lib/libuv.a"
    if not archive.is_file() or not (prefix / "include/uv.h").is_file():
        raise ValueError("Expected a static libuv installation")
    return prefix, {"version": "1.51.0", "linkage": "static", "archive_sha256": sha256(archive),
                    "revision": lock["repositories"]["libuv"]["revision"]}


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
    if case.get("crossbyte_variant") == "jvm-virtual":
        args += ["-D", "crossbyte_jvm_virtual_threads"]
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
    elif target == "hl":
        args += ["-D", "hl-ver=" + lock["repositories"]["hashlink"]["version"], "--hl", "server.hl"]
        launch = ["hl", "server.hl"]
    elif target == "js":
        args += ["-lib", "hxnodejs", "-D", "js-es=6", "--js", "server.js"]
        launch = ["node", "--max-old-space-size=256", "server.js"]
    elif target == "cpp":
        args += ["-lib", "libhxcpp", "--cpp", "native"]
        launch = ["./server"]
    else:
        raise ValueError(f"Unsupported target: {target}")
    native_libraries = {}
    started = time.monotonic()
    with (work / "build.log").open("w") as log:
        if target == "cpp":
            # Every C++ case uses the same pinned OpenFL runtime and Haxe.
            for tool in ("run", "hxcpp"):
                command([haxe, "compile.hxml"], cwd=work / "toolchain/libhxcpp/tools" / tool,
                        stdout=log, stderr=subprocess.STDOUT, timeout=120)
        if case.get("poll_backend") == "libuv":
            prefix, native_libraries["libuv"] = build_libuv(deps, work, lock, log)
            args += ["-lib", "crossbyte-libuv", "-D", "bench_libuv", "-D", "crossbyte_libuv_native",
                     "-D", f"LIBUV_INCLUDE={prefix / 'include'}", "-D", f"LIBUV_LIB={prefix / 'lib'}"]
        (work / "command.json").write_text(json.dumps(args, indent=2) + "\n")
        command(args, cwd=work, stdout=log, stderr=subprocess.STDOUT, timeout=900)
    destination.mkdir()
    for directory in ("public", "cache", "session"):
        (destination / directory).mkdir()
    if target == "cpp":
        shutil.copy2(work / "native/HxWell", destination / "server")
    else:
        suffix = {"jvm": "jar", "hl": "hl", "js": "js"}[target]
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
                "repositories": lock["repositories"], "libraries": libraries, "native_libraries": native_libraries, "files": files,
                "build_command": args, "workloads_sha256": sha256(ROOT / "benchmark.json"),
                "crossbyte_repository": lock["repositories"][crossbyte_repository_name(case)] if case["driver"] == "crossbyte" else None}
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
