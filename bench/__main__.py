import argparse
import json
import os
from pathlib import Path
import sys

from .config import load_config, select_cases
from .dependencies import fetch
from .build import build, unpack
from .run import run
from .hashlink import build_runtime
from .vendor import snapshot


def main():
    config, lock = load_config()
    parser = argparse.ArgumentParser(description="Build and benchmark pinned HxWell targets/drivers")
    sub = parser.add_subparsers(dest="command", required=True)
    matrix = sub.add_parser("matrix")
    matrix.add_argument("--cases", default="all")
    matrix.add_argument("--github-output", type=Path)
    download = sub.add_parser("fetch")
    download.add_argument("--deps", type=Path, default=Path("dependencies"))
    download.add_argument("--names", default="hxwell,hxwell-crossbyte,crossbyte,emitter,libhxcpp,crossbyte-libuv,libuv,hashlink,wrk,maven")
    compile = sub.add_parser("build")
    compile.add_argument("--case", required=True)
    compile.add_argument("--deps", type=Path, default=Path("dependencies"))
    compile.add_argument("--out", type=Path, default=Path("out/build"))
    compile.add_argument("--haxe", default="haxe")
    hashlink = sub.add_parser("hashlink", help="Build the pinned Linux x86-64 HashLink JIT runtime")
    hashlink.add_argument("--deps", type=Path, default=Path("dependencies"))
    hashlink.add_argument("--out", type=Path, default=Path("out/hashlink"))
    vendor = sub.add_parser("vendor-crossbyte")
    vendor.add_argument("--source", type=Path, required=True)
    extract = sub.add_parser("unpack")
    extract.add_argument("--archives", type=Path, required=True)
    extract.add_argument("--out", type=Path, default=Path("out/build"))
    benchmark = sub.add_parser("run")
    benchmark.add_argument("--cases", default="all")
    benchmark.add_argument("--bundles", type=Path, default=Path("out/build"))
    benchmark.add_argument("--out", type=Path, required=True)
    benchmark.add_argument("--profile", choices=list(config["profiles"]), default="ci")
    benchmark.add_argument("--wrk", default="wrk")
    benchmark.add_argument("--seed", type=int, default=1337)
    benchmark.add_argument("--workers", type=int)
    benchmark.add_argument("--no-affinity", action="store_true")
    args = parser.parse_args()
    if args.command == "matrix":
        value = json.dumps({"include": select_cases(config, args.cases)}, separators=(",", ":"))
        print(value)
        if args.github_output:
            with args.github_output.open("a") as file:
                file.write("matrix=" + value + "\n")
    elif args.command == "fetch":
        names = args.names.split(",")
        if any(name not in set(lock["repositories"]) | {"maven"} for name in names):
            parser.error("Unknown dependency name")
        fetch(lock, args.deps, names)
    elif args.command == "build":
        cases = select_cases(config, args.case)
        if len(cases) != 1:
            parser.error("build expects one case")
        build(config, lock, cases[0], args.deps, args.out, args.haxe)
    elif args.command == "hashlink":
        build_runtime(lock, args.deps, args.out)
    elif args.command == "unpack":
        unpack(args.archives, args.out)
    elif args.command == "vendor-crossbyte":
        print(snapshot(lock, args.source))
    elif args.command == "run":
        run(config, lock, select_cases(config, args.cases), args.bundles, args.out, args.profile,
            args.wrk, args.seed, not args.no_affinity, args.workers)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
