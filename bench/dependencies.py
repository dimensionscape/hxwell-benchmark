import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request

from .config import ROOT


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


def fetch_repository(path, spec):
    path = Path(path)
    if not path.exists():
        command(["git", "init", "--quiet", path])
        command(["git", "remote", "add", "origin", spec["url"]], cwd=path)
        command(["git", "fetch", "--depth", "1", "origin", spec["revision"]], cwd=path)
        command(["git", "checkout", "--quiet", "--detach", "FETCH_HEAD"], cwd=path)
    actual = capture(["git", "rev-parse", "HEAD"], cwd=path)
    if actual != spec["revision"] or capture(["git", "status", "--porcelain"], cwd=path):
        raise ValueError(f"{path} is not a clean checkout of {spec['revision']}; use a fresh dependencies directory")


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
    if case["target"] == "cpp":
        names += ["hxcpp"]
    libraries = {name: library_path(name, lock["haxelib"][name]) for name in names}
    repositories = ["hxwell"]
    if case["driver"] == "crossbyte":
        repositories += ["hxwell-crossbyte", "crossbyte", "emitter"]
    for name in repositories:
        if lock["repositories"][name].get("vendored"):
            from .vendor import verify_snapshot
            libraries[name] = verify_snapshot(lock["repositories"][name])
            continue
        path = deps / name
        if capture(["git", "rev-parse", "HEAD"], cwd=path) != lock["repositories"][name]["revision"]:
            raise ValueError(f"Wrong dependency revision: {name}")
        if capture(["git", "status", "--porcelain"], cwd=path):
            raise ValueError(f"Dirty dependency: {name}")
        libraries[name] = path
    command(["haxelib", "newrepo"], cwd=work, stdout=subprocess.DEVNULL)
    for name, path in libraries.items():
        folder = work / ".haxelib" / name
        folder.mkdir(parents=True)
        version = json.loads((path / "haxelib.json").read_text())["version"]
        (folder / ".dev").write_text(str(path) + "\n")
        (folder / version.replace(".", ",")).symlink_to(path, target_is_directory=True)
        (folder / ".current").write_text(version + "\n")
    return {name: json.loads((path / "haxelib.json").read_text())["version"] for name, path in libraries.items()}
