# HxWell target/driver benchmarks

<!-- benchmark-charts:start -->
## Summary

**Complete** · [Run #5 (attempt 1)](https://github.com/hxwell/hxwell-benchmark/actions/runs/41274494312) · profile `ci` · 72/72 valid trials.
Source: [`b26dc9c`](https://github.com/hxwell/hxwell-benchmark/commit/b26dc9c496ae55c3f91db0acde89d6f5b43fe520). Finished: `2026-11-01T05:07:47.881863+00:00`.

All selected measurements passed.

![Summary: all targets, throughput, p99 latency and memory](docs/benchmarks/latest/summary.svg?run=41274494312-1)

Each chart shows throughput, p99 latency and peak RSS for plaintext, JSON and echo. Summary and target charts use matching scales and colors; workloads and connection levels remain separate.

## JVM

![JVM benchmark results](docs/benchmarks/latest/target-jvm.svg?run=41274494312-1)

## Neko

![Neko benchmark results](docs/benchmarks/latest/target-neko.svg?run=41274494312-1)

## JavaScript / Node

![JavaScript / Node benchmark results](docs/benchmarks/latest/target-js.svg?run=41274494312-1)

## C++ / hxcpp

![C++ / hxcpp benchmark results](docs/benchmarks/latest/target-cpp.svg?run=41274494312-1)

[Chart data and provenance](docs/benchmarks/latest/manifest.json) · Full raw results are in the linked run's `benchmark-results` artifact.
Only the selected targets, cases and connection levels from this run are shown; older runs are not mixed into these charts.

<!-- benchmark-charts:end -->

CI benchmarks for the same HxWell application across supported compiler targets
and HTTP drivers. The suite builds release applications, checks their responses,
then measures them with a pinned `wrk` client. This is a benchmark harness, not a
set of claimed performance numbers.

## Included cases

| Case | Target | Driver | Request execution |
| --- | --- | --- | --- |
| `jvm-socket` | JVM | HxWell Socket | Platform-thread pool |
| `jvm-undertow` | JVM | Undertow | XNIO worker pool |
| `jvm-crossbyte` | JVM | Crossbyte | Virtual threads, separate I/O runtimes |
| `neko-socket` | Neko | HxWell Socket | Platform-thread pool |
| `neko-crossbyte` | Neko | Crossbyte | Worker pool, separate I/O runtimes |
| `js-nodehttp` | JavaScript/Node | NodeHTTP | Cluster workers |
| `cpp-socket` | C++/hxcpp | HxWell Socket | Platform-thread pool |
| `cpp-crossbyte` | C++/hxcpp | Crossbyte | Worker pool, separate I/O runtimes |

The actual target, driver, worker count and JVM virtual-thread state are checked
through `/health`; a mismatched build is never measured. No driver source is
patched for benchmarking. PHP's development server, HashLink and unsupported
target/driver combinations are not silently substituted for these cases.

The common workloads are GET plaintext, GET freshly serialized JSON and POST
JSON parsing/echo. They use the ordinary `hx.well.HxWell` application startup,
configuration, providers, routes and driver interfaces. Middleware is explicitly
empty; there is no database or session I/O in these transport-focused workloads.

## CI and measurement method

[The workflow](.github/workflows/benchmark.yml) runs on pushes to `main`, pull
requests, and manual dispatch:

1. Test the harness and validate the requested matrix.
2. Build cases in parallel, using pinned source/library versions.
3. Download the binaries to **one measurement job** and run cases sequentially.
   Thus the comparison is not assembled from differently loaded matrix runners.
4. Warm up each workload, measure repeated trials, and preserve raw results plus
   a JSON/CSV/Markdown summary in the `benchmark-results` artifact and job summary.
5. Generate one combined summary SVG, followed by separate JVM, Neko,
   JavaScript/Node and C++ charts for the selected targets. Each chart includes
   throughput, p99 latency and peak RSS, with separate workload/connection panels
   and matching scales and case colors across views. A separate publishing job
   updates the managed README section and `docs/benchmarks/latest/` for each
   completed `main` push or manual run, including unsuccessful runs.

The latest run replaces the chart snapshot; selected subsets and connection
levels are labeled and never merged with older measurements. Invalid/missing
groups have no bar, and smoke runs show validation status rather than performance
scores. A run link, source commit and UTC completion timestamp accompany the plots.
PR/other-branch runs produce chart artifacts without changing `main`; cancelled
runs do not replace the snapshot. Older runs/retries cannot overwrite newer ones.

Only the publishing job has `contents: write`. It regenerates plots from validated
raw data and updates only the README, generated SVGs and manifest. Obsolete target
charts are removed when a run selects fewer targets; unrelated files are preserved.
The publisher uses a non-forced, fast-forward Git reference update. On a concurrent edit it rereads the latest README and preserves
text outside the chart markers. Its `GITHUB_TOKEN` commit does not start another
benchmark; generated README/chart paths are also excluded from push triggers.

On Linux, available CPUs are split into disjoint server and client sets using
`taskset`; every child, including Node cluster workers, inherits its set. The
server worker budget equals the size of its CPU set unless explicitly overridden.
Client threads are adjusted so `wrk`'s requested connection count is exact.
The allocation, machine information, runtime versions, commands and revisions are
recorded. Without Linux affinity support, the report explicitly says pinning was
unavailable. The orchestrator itself is not CPU-isolated.

Each repetition starts a fresh server process group; case order is seeded and
shuffled between repetitions. Readiness is polled over HTTP using a unique run
token. Every workload has payload, status, compression and persistent-connection
preflights before and after load. The application suppresses Haxe per-request
traces and Crossbyte logging, and disables response compression and Crossbyte's
default per-address rate limiter. Crossbyte's keep-alive request-count cap is
disabled for this load test. All cases request HTTP/1.1 keep-alive and identity
encoding; this is not an HTTP/2, TLS, WebSocket or full middleware benchmark.

The JVM heap is fixed at 256 MiB; Node's old-space limit is 256 MiB per process.
These are runtime settings, not equivalent total-memory limits. Socket pool,
XNIO pool, virtual threads and Node processes are different concurrency models;
the results retain those labels rather than pretending the same worker count
means the same implementation.

`wrk` performs native request generation. Its Lua script sets a fixed method/body
and writes the final statistics; it has **no per-request or response callback**.
Response correctness is checked outside the measured interval. `wrk` reports
HTTP status errors for responses above 399; redirects are rejected by the
preflights but are not individually inspected during load. See the upstream
[wrk scripting API](https://github.com/wg/wrk/blob/4.2.0/SCRIPTING).

## Profiles

| Profile | Warmup per trial | Measurement | Repetitions | Connections | Use |
| --- | ---: | ---: | ---: | --- | --- |
| `smoke` | 1 s | 1 s | 1 | 4 | Harness validation only; used for PRs |
| `ci` | 3 s | 5 s | 3 | 32 | Default push/manual run |
| `full` | 5 s | 10 s | 3 | 16, 128 | Longer manual comparison |

Defaults are in `benchmark.json`. Full runs are bounded by the workflow's
50-minute timeout. CI benchmarks can consume several runner minutes; workflows
do not run on a recurring schedule. In manual dispatch, choose a profile and
`all` or a comma-separated list of the case IDs above.

## Results and interpretation

- `results.json`: every trial, errors, preflights, build provenance, commands,
  latency distributions, server/client CPU and peak process-group RSS samples.
- `summary.csv` and `summary.md`: median/min/max requests per second and medians
  of each repetition's p50/p95/p99. A median of per-trial p99 is **not** a pooled
  latency percentile.
- Per-trial warmup/client logs and per-server logs, including failed samples.

Zero completed requests, wrk errors/timeouts, failed preflights, server exits,
changed process groups and missing builds fail the run. An incomplete or invalid
group is displayed as `INVALID` without a performance score. Other cases still
run when one case fails. There is no arbitrary throughput pass/fail threshold.

Linux `/proc` measurements sample the whole server process group, including Node
workers. CPU is reported in seconds and core equivalents. Peak RSS is a sampled
sum across processes, not PSS: shared pages may be counted more than once. Client
CPU saturation is flagged because the client can limit observed throughput. On
other OSes these `/proc` fields are unavailable, not zero. The load is closed-loop
and has no fixed offered arrival rate. Hosted CI hardware, noisy neighbors, JIT,
GC and CPU frequency make these trend/comparison observations, not production
capacity or latency-SLO guarantees. Compare only runs with compatible settings,
workloads and hardware; retain the raw trials.

## Reproducible dependencies

`dependencies.lock.json` pins HxWell, the Crossbyte adapter, Crossbyte, Emitter,
hxcpp, wrk and Haxelib versions. Both C++ cases use the same immutable commit of
the [Dimensionscape hxcpp production runtime](https://github.com/dimensionscape/hxcpp/tree/308afd7a20b143338598d084bf2345d1349a22c8).
Crossbyte requires its native socket/TLS support; the Haxelib hxcpp 4.3.2 release
does not contain the required build files. The harness builds the runtime tools
with the pinned Haxe compiler and registers it only inside each isolated build.
Undertow jars have SHA-256 hashes verified against the
official Maven artifacts. Node's native `deasync` dependency uses
`app/package-lock.json`. Builds create a local Haxelib repository in a temporary
build directory; they do not change global dev registrations.

`hxwell-crossbyte` is a private dependency. Its exact runtime sources at the
locked commit are stored under `vendor/hxwell-crossbyte`, with the original
license and per-file SHA-256 provenance in `snapshot.json`. This lets this private
benchmark repo run CI without a cross-repository access token. The vendored
adapter is compiled as its own Haxelib package. Changes to it are made upstream,
then refreshed as a reviewed snapshot:

```sh
# First update the locked revision, then remove the old vendor directory after review.
python3 -m bench vendor-crossbyte --source /path/to/hxwell-crossbyte
```

The other source dependencies are fetched at immutable commits. A dirty or
different existing dependency checkout is refused. Application artifacts include
hashes and are transferred as tar archives to preserve executable modes.

## Run locally

Use Python 3.12+, Haxe **4.3.7**, Git, the selected runtimes and wrk 4.2.0 on a
POSIX host. Node builds use Node 22.13.0 and npm; JVM runs require Java 21+.
C++ builds require a C++ compiler; `bench fetch` downloads the locked hxcpp
runtime and `bench build` prepares its tools automatically.

```sh
haxelib install haxe-concurrent 5.1.5
haxelib install uuid 2.4.1
haxelib install crypto 1.3.0
haxelib install hxjava 4.2.0
# For the corresponding optional targets:
haxelib install hxnodejs 12.2.0

python3 -m pip install -r requirements-charts.txt
python3 -m unittest discover -s tests -v
python3 -m bench fetch
make -C dependencies/wrk -j2 WITH_OPENSSL=/usr VER=4.2.0

python3 -m bench build --case jvm-socket --haxe /path/to/haxe-4.3.7/haxe
python3 -m bench build --case jvm-crossbyte --haxe /path/to/haxe-4.3.7/haxe
python3 -m bench run --cases jvm-socket,jvm-crossbyte \
  --profile smoke --wrk "$PWD/dependencies/wrk/wrk" --out out/local-smoke

# Native C++ comparison, including the Crossbyte driver:
python3 -m bench build --case cpp-socket --haxe /path/to/haxe-4.3.7/haxe
python3 -m bench build --case cpp-crossbyte --haxe /path/to/haxe-4.3.7/haxe
python3 -m bench run --cases cpp-socket,cpp-crossbyte \
  --profile ci --wrk "$PWD/dependencies/wrk/wrk" --out out/local-cpp
```

On macOS, supply the installed OpenSSL prefix to `WITH_OPENSSL` (for example
`/opt/homebrew/opt/openssl@3`) and use GNU make if needed. A smoke run validates
behavior only. Choose a fresh output directory for each measurement; the harness
refuses to overwrite existing results or bundles. All traffic is loopback, and
cleanup signals only the process groups created by the harness, never `pkill`
or unrelated Java/Node/Neko processes.

## Render charts locally

```sh
python3 -m pip install -r requirements-charts.txt
python3 -m bench.charts --report out/local-run/results.json --output out/charts \
  --repository hxwell/hxwell-benchmark --run-id 123 --run-number 1 \
  --source-sha FULL_BENCHMARK_COMMIT_SHA --profile ci --cases all --preview
```

Use the actual run identifiers/source commit and the same profile/case selection
as the report. `--preview` additionally creates PNGs for inspection. The publisher
is restricted to trusted `main` GitHub Actions jobs; local rendering never writes
to GitHub. Install the chart requirements before running the Python test suite.
