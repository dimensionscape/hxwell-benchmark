"""Build and verify the pinned Linux x86-64 HashLink JIT runtime."""
import json
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile

from .dependencies import capture, command, sha256, verify_repository


RUNTIME_FILES = ("hl", "hl-bin", "libhl.so", "fmt.hdll", "ssl.hdll", "mysql.hdll", "sqlite.hdll")
LAUNCHER = '''#!/bin/sh
HASHLINK_BENCH_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export LD_LIBRARY_PATH="$HASHLINK_BENCH_DIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export HLPATH="$HASHLINK_BENCH_DIR"
exec "$HASHLINK_BENCH_DIR/hl-bin" "$@"
'''


def verify_runtime(lock, executable=None):
    executable = executable or shutil.which("hl")
    if not executable:
        raise ValueError("HashLink is missing; build the pinned runtime with python -m bench hashlink and add it to PATH")
    executable = Path(executable).resolve()
    root = executable.parent
    spec = lock["repositories"]["hashlink"]
    try:
        manifest = json.loads((root / "manifest.json").read_text())
    except (OSError, ValueError) as error:
        raise ValueError("HashLink needs the pinned runtime manifest") from error
    if (manifest.get("version") != spec["version"] or manifest.get("revision") != spec["revision"]
            or manifest.get("mode") != "jit" or set(manifest.get("files", {})) != set(RUNTIME_FILES)):
        raise ValueError("HashLink runtime provenance does not match the lock")
    for name, expected in manifest["files"].items():
        path = root / name
        if not path.is_file() or sha256(path) != expected:
            raise ValueError(f"HashLink runtime checksum mismatch: {name}")
    if capture([executable, "--version"], timeout=10, stderr=subprocess.STDOUT) != spec["version"]:
        raise ValueError("HashLink executable version does not match the lock")
    return manifest


def build_runtime(lock, dependencies, output):
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "AMD64"):
        raise ValueError("The pinned HashLink JIT builder requires Linux x86-64; CI builds and runs it on Ubuntu")
    spec = lock["repositories"]["hashlink"]
    source, output = Path(dependencies).resolve() / "hashlink", Path(output).resolve()
    verify_repository(source, spec)
    output.mkdir(parents=True, exist_ok=False)
    # Build outside the immutable checkout, just as the C++ runtime builds do.
    work = Path(tempfile.mkdtemp(prefix="hashlink-build-", dir=output.parent))
    shutil.copytree(source, work, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git"))
    with (output / "build.log").open("w") as log:
        command(["make", "-j2", "hl", "fmt.hdll", "ssl.hdll", "mysql.hdll", "sqlite.hdll", "SSL_STATIC=1"],
                cwd=work, stdout=log, stderr=subprocess.STDOUT, timeout=600)
    shutil.copy2(work / "hl", output / "hl-bin")
    for name in RUNTIME_FILES[2:]:
        shutil.copy2(work / name, output / name)
    (output / "hl").write_text(LAUNCHER)
    (output / "hl").chmod(0o755)
    manifest = {"version": spec["version"], "revision": spec["revision"], "mode": "jit",
                "files": {name: sha256(output / name) for name in RUNTIME_FILES}}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    verify_runtime(lock, output / "hl")
    print(f"Built verified HashLink {spec['version']} JIT runtime -> {output}", flush=True)
    return output
