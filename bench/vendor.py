"""Versioned runtime snapshot for the private hxwell-crossbyte dependency."""
import json
from pathlib import Path
import subprocess

from .config import ROOT
from .dependencies import sha256, capture


def snapshot(lock, source):
    name = "hxwell-crossbyte"
    spec = lock["repositories"][name]
    revision = spec["revision"]
    source = Path(source).resolve()
    capture(["git", "cat-file", "-e", revision + "^{commit}"], cwd=source)
    files = capture(["git", "ls-tree", "-r", "--name-only", revision], cwd=source).splitlines()
    selected = [name for name in files if name.startswith("src/") or name in ("LICENSE", "haxelib.json", "extraParams.hxml")]
    destination = ROOT / "vendor" / name
    if destination.exists():
        raise ValueError(f"Refusing to overwrite {destination}; review/remove the old snapshot before refreshing")
    destination.mkdir(parents=True)
    hashes = {}
    for relative in selected:
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(subprocess.check_output(["git", "show", revision + ":" + relative], cwd=source))
        hashes[relative] = sha256(path)
    (destination / "snapshot.json").write_text(json.dumps({"repository": spec["url"], "revision": revision, "files": hashes}, indent=2) + "\n")
    return destination


def verify_snapshot(spec):
    destination = ROOT / "vendor/hxwell-crossbyte"
    metadata = json.loads((destination / "snapshot.json").read_text())
    if metadata["revision"] != spec["revision"] or metadata["repository"] != spec["url"]:
        raise ValueError("Vendored Crossbyte driver does not match dependencies.lock.json")
    actual = {str(path.relative_to(destination)) for path in destination.rglob("*") if path.is_file()} - {"snapshot.json"}
    if actual != set(metadata["files"]):
        raise ValueError("Unexpected or missing file in the Crossbyte driver snapshot")
    for name, expected in metadata["files"].items():
        path = (destination / name).resolve()
        if not path.is_relative_to(destination.resolve()) or sha256(path) != expected:
            raise ValueError(f"Driver snapshot checksum mismatch: {name}")
    return destination
