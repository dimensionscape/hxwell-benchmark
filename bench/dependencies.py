import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import urllib.request

from .config import ROOT, crossbyte_repository_name


def command(args, **kwargs):
    return subprocess.run([str(v) for v in args], check=True, **kwargs)


def capture(args, **kwargs):
    return subprocess.check_output([str(v) for v in args], text=True, **kwargs).strip()


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(data)
    return digest.hexdigest()


def verify_repository(path, spec):
    actual = capture(["git", "rev-parse", "HEAD"], cwd=path)
    if actual != spec["revision"] or capture(["git", "status", "--porcelain"], cwd=path):
        raise ValueError(f"{path} is not a clean checkout of {spec['revision']}; use a fresh dependencies directory")


def fetch_repository(path, spec):
    path = Path(path)
    if not path.exists():
        command(["git", "init", "--quiet", path])
        command(["git", "remote", "add", "origin", spec["url"]], cwd=path)
        command(["git", "fetch", "--depth", "1", "origin", spec["revision"]], cwd=path)
        command(["git", "checkout", "--quiet", "--detach", "FETCH_HEAD"], cwd=path)
    verify_repository(path, spec)


def download(path, url, expected_sha256):
    if not expected_sha256:
        raise ValueError(f"Missing SHA-256 for {url}")
    path = Path(path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".part")
        try:
            with urllib.request.urlopen(url, timeout=60) as response, temporary.open("wb") as output:
                while data := response.read(1024 * 1024):
                    output.write(data)
            if sha256(temporary) != expected_sha256:
                raise ValueError(f"Checksum mismatch for {url}")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    if sha256(path) != expected_sha256:
        raise ValueError(f"Checksum mismatch: {path}")


def fetch(lock, directory, names):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        if name == "maven":
            for artifact in lock["maven"]:
                download(directory / "maven" / Path(artifact["path"]).name,
                         "https://repo.maven.apache.org/maven2/" + artifact["path"], artifact.get("sha256"))
        else:
            spec = lock["repositories"][name]
            if spec.get("vendored"):
                from .vendor import verify_snapshot
                verify_snapshot(spec)
            else:
                fetch_repository(directory / name, spec)


def library_path(name, version):
    repository = Path(capture(["haxelib", "config"], cwd=ROOT))
    path = repository / name.replace(".", ",") / version.replace(".", ",")
    if not path.exists():
        path = Path(capture(["haxelib", "libpath", name], cwd=ROOT))
    metadata = json.loads((path / "haxelib.json").read_text())
    if metadata["version"] != version:
        raise ValueError(f"Need {name} {version}; found {metadata['version']} at {path}")
    return path.resolve()


def local_haxelib(work, deps, lock, case):
    names = ["haxe-concurrent", "uuid", "crypto"]
    if case["target"] == "jvm":
        names += ["hxjava"]
    if case["target"] == "js":
        names += ["hxnodejs"]
    libraries = {name: library_path(name, lock["haxelib"][name]) for name in names}
    repositories = ["hxwell"]
    if case["target"] == "cpp":
        repositories += ["libhxcpp"]
    if case["driver"] == "crossbyte":
        repositories += ["hxwell-crossbyte", "crossbyte", "emitter"]
    if case.get("poll_backend") == "libuv":
        repositories += ["crossbyte-libuv"]
    for name in repositories:
        source_name = crossbyte_repository_name(case) if name == "crossbyte" else name
        spec = lock["repositories"][source_name]
        if spec.get("vendored"):
            from .vendor import verify_snapshot
            libraries[name] = verify_snapshot(spec)
            continue
        path = deps / source_name
        verify_repository(path, spec)
        if name == "libhxcpp":
            # Bootstrapping replaces the tracked run.n. Keep the immutable
            # checkout pristine and isolate compiler output between builds.
            runtime = work / "toolchain/libhxcpp"
            shutil.copytree(path, runtime, ignore=shutil.ignore_patterns(".git", "hxcpp.n", "obj", "__pch"))
            path = runtime
        libraries[name] = path
    command(["haxelib", "newrepo"], cwd=work, stdout=subprocess.DEVNULL)
    registrations = dict(libraries)
    if case["target"] == "cpp":
        # Haxe invokes the runtime by the compatibility name hxcpp. Both
        # names resolve to this build's same pinned OpenFL libhxcpp copy.
        registrations["hxcpp"] = libraries["libhxcpp"]
    for name, path in registrations.items():
        folder = work / ".haxelib" / name
        folder.mkdir(parents=True)
        version = json.loads((path / "haxelib.json").read_text())["version"]
        (folder / ".dev").write_text(str(path) + "\n")
        (folder / version.replace(".", ",")).symlink_to(path, target_is_directory=True)
        (folder / ".current").write_text(version + "\n")
    return {name: json.loads((path / "haxelib.json").read_text())["version"] for name, path in libraries.items()}
