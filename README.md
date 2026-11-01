# HxWell target/driver benchmarks

<!-- benchmark-charts:start -->
## Summary

**Incomplete / failed** · [Run #12 (attempt 1)](https://github.com/hxwell/hxwell-benchmark/actions/runs/41349655994) · profile `stress` · 74/81 valid trials.
Source: [`83ea99a`](https://github.com/hxwell/hxwell-benchmark/commit/83ea99a50c66bf64350979cb01e93b3bffad8e19). Finished: `2026-11-01T21:26:20.892858+00:00`.

Incomplete or failed run; invalid groups have no score.

![Summary: all targets, throughput, p99 latency and memory](docs/benchmarks/latest/summary.svg?run=41349655994-1&layout=4)

Each chart shows throughput, p99 latency and peak RSS for plaintext, JSON and echo. Case colors are consistent across views; each target uses its own axis scales for detail. Workloads and connection levels remain separate.

72 valid trials belong to complete groups and are eligible for scoring; a group with a missing or failed repetition has no bar.

## JVM

![JVM benchmark results](docs/benchmarks/latest/target-jvm.svg?run=41349655994-1&layout=4)

## Neko

![Neko benchmark results](docs/benchmarks/latest/target-neko.svg?run=41349655994-1&layout=4)

## JavaScript / Node

![JavaScript / Node benchmark results](docs/benchmarks/latest/target-js.svg?run=41349655994-1&layout=4)

## C++ / libhxcpp

![C++ / libhxcpp benchmark results](docs/benchmarks/latest/target-cpp.svg?run=41349655994-1&layout=4)

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
| `jvm-crossbyte` | JVM | Crossbyte | Virtual threads, single I/O runtime |
| `hl-socket` | HashLink / HL-JIT | HxWell Socket | Platform-thread pool |
| `hl-crossbyte` | HashLink / HL-JIT | Crossbyte | Worker pool, separate I/O runtimes |
| `js-nodehttp` | JavaScript/Node | NodeHTTP | Cluster workers |
| `cpp-socket` | C++/libhxcpp | HxWell Socket | Platform-thread pool |
| `cpp-crossbyte` | C++/libhxcpp | Crossbyte | Worker pool, built-in polling |
| `cpp-crossbyte-libuv` | C++/libhxcpp | Crossbyte + libuv | Worker pool, libuv polling |

The actual target, driver, worker count and JVM virtual-thread state are checked
through `/health`; a mismatched build is never measured. Every C++ case must
report the pinned `libhxcpp` version. Crossbyte C++ cases inspect the actual
request I/O runtime before and after each trial: the built-in case must stay on
the built-in poller, and the libuv case must stay on libuv. A libuv fallback or
wrong linked libuv version invalidates the measurement. No driver source is
patched for benchmarking. PHP's development server and unsupported
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
5. Generate one combined summary SVG, followed by separate JVM, HashLink,
   JavaScript/Node and C++ charts for the selected targets. Each chart includes
   throughput, p99 latency and peak RSS, with separate workload/connection panels
   and consistent case colors across views. Target charts use their own axis scales
   to keep within-target differences visible. A separate publishing job
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

JVM Crossbyte uses `maxConnections=10000`, `maxConcurrentRequests=2000`,
`poolSize=16` and `runtimeCount=0`, with per-address rate limiting disabled.
Connections stay on the listener's I/O runtime, avoiding the bounded connection
handoff queue under connection bursts; HxWell requests still run on virtual
threads. The JVM driver does not use the configured pool size to execute
requests. Other Crossbyte targets retain the configured worker pool and separate
I/O runtimes.

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

| Profile | Warmup per trial | Measurement | Repetitions | Connections | Request timeout | Use |
| --- | ---: | ---: | ---: | --- | ---: | --- |
| `smoke` | 1 s | 1 s | 1 | 4 | 2 s | Harness validation only; used for PRs |
| `ci` | 3 s | 5 s | 3 | 32 | 2 s | Default push/manual run |
| `full` | 5 s | 10 s | 3 | 16, 128 | 2 s | Longer manual comparison |
| `stress` | 5 s | 10 s | 3 | 1000 | 10 s | High-concurrency manual run |

Defaults are in `benchmark.json`. Full runs are bounded by the workflow's
50-minute timeout. CI benchmarks can consume several runner minutes; workflows
do not run on a recurring schedule. In manual dispatch, choose a profile and
`all` or a comma-separated list of the case IDs above.

`stress` maintains **1,000 concurrent HTTP/1.1 connections** for every selected
case and workload. Throughput is measured independently in requests/second.
The longer request timeout allows queueing under this load; errors/timeouts still
invalidate a trial. Running all nine cases produces 81 trials and about 20 minutes
of warmup/load, plus startup and reporting. The README and charts are updated by
the selected profile's completed main-branch run.

Before starting the servers and clients, the harness checks the process file
descriptor limit and raises its soft limit when the hard limit permits it.
Insufficient hard limits fail with an explicit error. Requested connections are
divided exactly across client threads, and descriptor limits, timeout, CPU use
and possible client saturation are recorded in the report. Warmup measurements
and post-error backend diagnostics are retained; failed trials are not retried
into a passing score. This active-connection
load measures the complete HTTP path; backend-only costs and many idle sockets
require a separate poll benchmark.

For a focused C++ poll-backend comparison in GitHub Actions:

```sh
gh workflow run benchmark.yml --ref main -f profile=stress \
  -f cases=cpp-crossbyte,cpp-crossbyte-libuv
```

Choose `cases=all` to run the stress profile across every target/driver.

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
OpenFL libhxcpp, crossbyte-libuv, native libuv, wrk and Haxelib versions. All three
C++ cases use the same [OpenFL libhxcpp commit](https://github.com/openfl/libhxcpp/tree/04f436bc68c0f6f119ca1863c12974693ae8bcc7).
Each build prepares a separate runtime copy and registers both `libhxcpp` and
Haxe's compatibility name `hxcpp` to that copy. Build metadata and the live
health endpoint identify the runtime as libhxcpp 6.0.0.

The [crossbyte-libuv extension](https://github.com/DimensionscapeOrg/crossbyte-libuv/tree/49344cefa751569a20a56e530a793fab958da2fa)
is installed before the first Crossbyte runtime exists and enabled only for
`cpp-crossbyte-libuv`. CMake builds libuv 1.51.0 from its pinned source into a
static archive in the build directory; the executable needs no system libuv
package. Its source revision, archive hash and linked runtime version are
recorded. The other C++ case keeps Crossbyte's built-in poll backend.
On macOS, the libuv build preincludes the SDK's `uuid_t` definition to avoid a
case-insensitive collision with Haxe's generated `uuid/Uuid.h`; dependency
sources remain unchanged.

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

HashLink uses HL/JIT bytecode (`.hl`) with the official 1.16.0 runtime pinned to
[`c3f2a15`](https://github.com/HaxeFoundation/hashlink/tree/c3f2a1563cf7aa9f4076b0808a7eaf40e18e8f14).
The measurement runner builds the VM, core library, `fmt.hdll`, `ssl.hdll`, `mysql.hdll` and `sqlite.hdll`
from that source on Linux x86-64. PCRE2 and mbedTLS come from the pinned source;
image/audio dependencies of `fmt.hdll` and SQLite use Ubuntu system libraries.
The SQL modules satisfy HxWell imports; these workloads perform no database I/O. The harness
verifies the executable version and SHA-256 of every runtime file, records the
provenance, and checks the live HashLink target and JIT mode through `/health`.
See the [official HashLink target documentation](https://haxe.org/manual/target-hl-getting-started.html).

The other source dependencies are fetched at immutable commits. A dirty or
different existing dependency checkout is refused. Application artifacts include
hashes and are transferred as tar archives to preserve executable modes.

## Run locally

Use Python 3.12+, Haxe **4.3.7**, Git, the selected runtimes and wrk 4.2.0 on a
POSIX host. Node builds use Node 22.13.0 and npm; JVM runs require Java 21+.
C++ builds require a C++ compiler, and the libuv case also requires CMake.
`bench fetch` downloads the locked libhxcpp and libuv sources; `bench build`
prepares the runtime tools and static libuv library automatically.

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

# HashLink JIT on Linux x86-64 (build once, then use the same VM for both drivers):
# Requires build-essential, libpng-dev, libturbojpeg0-dev, libvorbis-dev, zlib1g-dev and libsqlite3-dev.
python3 -m bench hashlink --out out/hashlink
export PATH="$PWD/out/hashlink:$PATH"
python3 -m bench build --case hl-socket --haxe /path/to/haxe-4.3.7/haxe
python3 -m bench build --case hl-crossbyte --haxe /path/to/haxe-4.3.7/haxe
python3 -m bench run --cases hl-socket,hl-crossbyte \
  --profile ci --wrk "$PWD/dependencies/wrk/wrk" --out out/local-hashlink

# Native C++ comparison, including the Crossbyte driver:
python3 -m bench build --case cpp-socket --haxe /path/to/haxe-4.3.7/haxe
python3 -m bench build --case cpp-crossbyte --haxe /path/to/haxe-4.3.7/haxe
python3 -m bench build --case cpp-crossbyte-libuv --haxe /path/to/haxe-4.3.7/haxe
python3 -m bench run --cases cpp-socket,cpp-crossbyte,cpp-crossbyte-libuv \
  --profile ci --wrk "$PWD/dependencies/wrk/wrk" --out out/local-cpp
```

On macOS, supply the installed OpenSSL prefix to `WITH_OPENSSL` (for example
`/opt/homebrew/opt/openssl@3`) and use GNU make if needed. A smoke run validates
behavior only. Choose a fresh output directory for each measurement; the harness
refuses to overwrite existing results or bundles. All traffic is loopback, and
cleanup signals only the process groups created by the harness, never `pkill`
or unrelated Java/Node/HashLink processes.

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
