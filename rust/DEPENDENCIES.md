# Rust dependency baseline

## Dependency review

- Add a dependency when its owning crate needs it. Check the standard library and existing workspace dependencies first. The Slice Plan lists `serde`, `serde_json`, `sha2`, `bigdecimal`, `tokio`, `ed25519-dalek`, and property-test and fuzzing crates as candidates. Review exact packages and versions when an implementation Slice needs them.
- Enable only the features the crate uses. Review default features and disable them when they add unnecessary network, filesystem, or unsafe-code surface. Use `cargo tree --edges features` to check which dependency enables each feature. Keep test and fuzzing dependencies scoped to their verification targets.
- Review direct and transitive packages for their source and version, upstream repository and ownership, SPDX license and obligations, known security advisories, and relevant unsafe code or build scripts. Record the review with the dependency change. Check advisories against the resolved versions in [`Cargo.lock`](Cargo.lock) using the [RustSec advisory database](https://rustsec.org/).
- Review local path dependencies against the workspace membership and architecture. Git dependencies, out-of-workspace path dependencies, and alternate registries or source overrides require explicit review before use.
- Commit the Cargo-generated [`Cargo.lock`](Cargo.lock) with dependency changes. Keep dependency updates in a separate change from semantic work and run the relevant regressions. Record the resolved inventory and lockfile SHA-256 so the build can be traced to the reviewed dependency set.

## R1-S02 inventory and verification

The lockfile is format `4` and lists the 14 workspace members, all at `0.2.1`. On Rust and Cargo `1.99.1` (`x86_64-unknown-linux-gnu`), the resolved graph contained no dependency edges, external packages, Git or path dependency declarations, or enabled features. The default and all-feature trees were identical across the targets reported by Cargo. Third-party license, provenance, and advisory reviews had no packages to assess in this baseline.

```text
Cargo.lock SHA-256
eda03806b4e1724152009be617842bdcbddd01c92e423274a0b0d6d9581f20a8
```

From `rust/`, run the following with the pinned toolchain. Cargo's [`--frozen`](https://doc.rust-lang.org/cargo/commands/cargo-build.html#manifest-options) combines the lockfile requirement of `--locked` with `--offline`. Compare the default and all-feature trees, inspect the resolved package sources and feature sets in the metadata, and confirm the lockfile digest is identical before and after the build.

```bash
sha256sum Cargo.lock
cargo tree --workspace --frozen --target all --edges normal,build,dev,features
cargo tree --workspace --frozen --target all --all-features --edges normal,build,dev,features
cargo metadata --format-version 1 --frozen --all-features
cargo build --workspace --frozen
sha256sum Cargo.lock
```

The workspace build passed from an empty target directory and the lockfile digest matched before and after.

## R2-S03 Decimal dependency review

`nsl-core` uses `bigdecimal =0.4.11` behind its private Decimal representation. Only `std` is enabled explicitly. Cargo resolves the following crates.io packages. The lockfile records the registry checksums.

| Package | Version | SPDX | Upstream | Use |
| --- | --- | --- | --- | --- |
| `bigdecimal` | `0.4.11` | `MIT OR Apache-2.0` | [akubera/bigdecimal-rs](https://github.com/akubera/bigdecimal-rs) | Decimal storage and explicit rounding |
| `num-bigint` | `0.4.8` | `MIT OR Apache-2.0` | [rust-num/num-bigint](https://github.com/rust-num/num-bigint) | Arbitrary-precision integer arithmetic |
| `num-integer` | `0.1.47` | `MIT OR Apache-2.0` | [rust-num/num-integer](https://github.com/rust-num/num-integer) | Transitive integer operations |
| `num-traits` | `0.2.19` | `MIT OR Apache-2.0` | [rust-num/num-traits](https://github.com/rust-num/num-traits) | Transitive numeric traits |
| `autocfg` | `1.5.1` | `MIT OR Apache-2.0` | [cuviper/autocfg](https://github.com/cuviper/autocfg) | Build-time compiler probes |
| `libm` | `0.2.16` | `MIT` | [rust-lang/compiler-builtins](https://github.com/rust-lang/compiler-builtins) | Unconditional upstream dependency with its default `arch` feature |

Preserve the applicable license notices when redistributing these packages. The `bigdecimal` build script generates defaults and `num-traits` probes the compiler. NSL selects its own rounding rule. The resolved graph includes upstream unsafe code, including architecture-specific `libm` implementations and integer intrinsics in the numeric dependencies. The NSL wrapper uses integer operations and the workspace unsafe-code policy still applies to all NSL crates. The graph enables no `serde` support. The [RustSec snapshot](https://github.com/RustSec/advisory-db/tree/af7b3ec33460b8e2de8d422e16d628518891a519) contained no entries for the six resolved packages at review time.

```text
Cargo.lock SHA-256
d63414cd359436c1992d238f3946fe584cd057dbb55f57f444588356171b15d1
```

## R3-S01 workspace dependency

`nsl-ir` uses the existing `nsl-core` workspace member for its typed values and metadata. The resolved graph adds the permitted internal edge `nsl-ir → nsl-core`, with the same external packages, versions, and features as R2-S03.

```text
Cargo.lock SHA-256
8d1716d47e289ee0ea3243bcf2feafc0f0b6df8b393509075c8b45fcfe5e3641
```

## R3-S04 SHA-256 dependency review

`nsl-ir` pins `sha2 =0.11.1` with default features disabled. The resolved crates.io graph uses the digest block API and target-specific CPU detection. Cargo records the registry checksums. Redistributed packages carry the applicable license notices.

| Package | Version | SPDX | Upstream |
| --- | --- | --- | --- |
| `sha2` | `0.11.1` | `MIT OR Apache-2.0` | [RustCrypto/hashes](https://github.com/RustCrypto/hashes) |
| `digest` | `0.11.3` | `MIT OR Apache-2.0` | [RustCrypto/traits](https://github.com/RustCrypto/traits) |
| `crypto-common` | `0.2.2` | `MIT OR Apache-2.0` | [RustCrypto/traits](https://github.com/RustCrypto/traits) |
| `block-buffer` | `0.12.1` | `MIT OR Apache-2.0` | [RustCrypto/utils](https://github.com/RustCrypto/utils) |
| `cpufeatures` | `0.3.1` | `MIT OR Apache-2.0` | [RustCrypto/utils](https://github.com/RustCrypto/utils) |
| `hybrid-array` | `0.4.15` | `MIT OR Apache-2.0` | [RustCrypto/hybrid-array](https://github.com/RustCrypto/hybrid-array) |
| `typenum` | `1.20.1` | `MIT OR Apache-2.0` | [paholg/typenum](https://github.com/paholg/typenum) |
| `cfg-if` | `1.0.5` | `MIT OR Apache-2.0` | [rust-lang/cfg-if](https://github.com/rust-lang/cfg-if) |
| `libc` | `0.2.191` | `MIT OR Apache-2.0` | [rust-lang/libc](https://github.com/rust-lang/libc) |

The resolved implementation includes upstream unsafe code for fixed-size buffers, target-specific instructions and discovery, and platform FFI. `nsl-ir` uses the safe `Sha256` API with no network or filesystem access. The workspace forbid policy remains in force. The [RustSec snapshot](https://github.com/RustSec/advisory-db/tree/02e2517f457a359fc6d3440ea4560c8ea188fee8) includes [RUSTSEC-2021-0100](https://rustsec.org/advisories/RUSTSEC-2021-0100.html), patched in `sha2` versions `>=0.9.8`, and no advisory affecting these locked versions.

```text
Cargo.lock SHA-256
d847b84574c4f686b3c07956a9a06c2738af0427a16efbe167e145ff3fd6746c
```

## R4-S01 source identity dependency

`nsl-source` also uses the reviewed `sha2 =0.11.1` with default features disabled. The SourceId calculation hashes the logical path and decoded text in the source crate. The graph adds one edge to the existing SHA-256 packages and resolves the same external versions and features as R3-S04.

```text
Cargo.lock SHA-256
6ed77779be94a6555d6dac87951699825e4ed0e999a113d7004aaa42c70c8524
```

## R5-S02 Tool Contract identity dependency

`nsl-contract` hashes canonical Tool Contract bytes with the reviewed `sha2 =0.11.1` pin and default features disabled. The resolved external inventory is the [R3-S04 SHA-256 graph](#r3-s04-sha-256-dependency-review). Contract and NSO JSON encoding share the Core writer.

```text
Cargo.lock SHA-256
b51495d0822b93a4de74258457a4c356fcac54188fa5c2b185ca5dbc684956d6
```

## R5-S04 static type checker dependency

`nsl-compiler` adds the internal workspace edge to `nsl-contract` for exact required-tool resolution and input/output typing. The contract crate reaches Core and the already reviewed SHA-256 graph; it does not reach Runtime, Compiler, or Source. External packages, versions, checksums, and features are unchanged. The locked workspace check, frozen build, and architecture guard pass.

```text
Cargo.lock SHA-256
39236a3b5fddd19421b37400ae8f22386cd569cd452e845348fd4997951fd5bc
```

## R5-S05 exact bound arithmetic dependency

`nsl-compiler` uses `num-bigint =0.4.8` directly, with default features disabled and only `std` enabled, for exact resource-count sums and products. This is the package already reviewed in R2-S03 (`MIT OR Apache-2.0`, [rust-num/num-bigint](https://github.com/rust-num/num-bigint)); the same redistribution notices and reviewed upstream integer implementation apply. No new package, version, registry checksum, or resolved external feature is introduced. Comparing locked all-feature metadata before and after this Slice confirms that all 15 external package inventories and feature sets are identical. The architecture guard and frozen build pass.

```text
Cargo.lock SHA-256
416ce7d68486a3e05612ade0a014124d6aba1bc25fa714eb9fa615801d5c8238
```

## R6-S02 asynchronous execution dependency review

Runtime adds the permitted `nsl-contract → nsl-core` graph and reuses `sha2 =0.11.1` with default features disabled. Tokio is pinned to **`=1.54.1`**, with only `rt`, `sync`, and `time` in production and `test-util` in development for virtual-clock tests. No network, filesystem, process, signal, multi-thread runtime, macro, or provider transport feature is enabled. These dependency-only changes are committed before the execution implementation.

| New package | Version | SPDX | Upstream | Purpose |
| --- | --- | --- | --- | --- |
| `tokio` | `1.54.1` | `MIT` | [tokio-rs/tokio](https://github.com/tokio-rs/tokio) | Private timer and cooperative cancellation notification |
| `pin-project-lite` | `0.2.17` | `Apache-2.0 OR MIT` | [taiki-e/pin-project-lite](https://github.com/taiki-e/pin-project-lite) | Tokio's transitive safe pin-projection facade |

Both packages come from crates.io with checksums in `Cargo.lock`; preserve their applicable notices on redistribution. Neither package has a build script. Upstream uses unsafe code for scheduler/synchronization/timer internals and pin projection; NSL uses only their safe APIs and retains `forbid(unsafe_code)`. Tokio-specific types do not enter the public Tool Execution Port, identities, or result model.

The [RustSec snapshot `233155b4`](https://github.com/RustSec/advisory-db/tree/233155b4025b854c68a4b199c0e32d7fd53291a2) lists seven Tokio advisories, all patched in the locked version. In particular [RUSTSEC-2026-0384](https://rustsec.org/advisories/RUSTSEC-2026-0384.html) requires `>=1.54.1`, and [RUSTSEC-2026-0386](https://rustsec.org/advisories/RUSTSEC-2026-0386.html) is patched by `>=1.53.3`. There is no `pin-project-lite` advisory. The prior 15 package identities/checksums are unchanged; the graph now contains 17 external packages. Locked metadata, regression tests and the frozen build verify the resolved graph.

```text
Cargo.lock SHA-256
628268a256b8144d2e6f26a9db9835b620896fed75eec7a3a92b05fae7dfe562
```

## Architecture dependency guard

From `rust/`, run `python tools/check_architecture.py` with Python 3.12 and the pinned Rust toolchain. The guard reads Cargo's locked, all-feature graph for all target platforms. It follows dependencies by resolved package ID, including build and development edges, so renaming a crate or depending on it indirectly still exposes the dependency chain.

`nsl-core` has no permitted workspace dependencies. `nsl-ir` may depend on `nsl-core` and reach the reviewed Decimal packages through it. External packages are checked against the allowlist of the workspace crate that introduces them. The `nsl-ir` allowlist contains the reviewed SHA-256 graph above. `nsl-compiler` cannot reach `nsl-runtime`. `nsl-runtime` cannot reach `nsl-compiler` or `nsl-source`, which owns the AST.

Every member must inherit the workspace `unsafe_code = "forbid"` lint. Cargo's compile checks enforce that lint on workspace targets. Run `python -m unittest discover -s tools` to exercise the guard against temporary Cargo workspaces and verify compiler rejection of unsafe integration-test code.

## R8-S01 WRITE identity graph

`nsl-write` now depends on workspace `nsl-core` / `nsl-ir` and the already reviewed
`sha2 = 0.11.1` with default features disabled. Cargo.lock changes only its local
workspace dependency edges; all **17 external package identities, versions and
checksums remain unchanged**. The guard now forbids WRITE from reaching Runtime,
Compiler or Source, including transitive/renamed edges. Its **8 tests** include a
new temporary-workspace negative case for those reverse dependencies.

Runtime receives no WRITE-to-Runtime reverse edge: the builder uses a borrowed
Core/IR context projection. Locked metadata and the frozen workspace build verify
the graph. Current Cargo.lock SHA-256:

```text
d45307992ead2ecdf9f4e770060912e951d0bb75bf885c53320341e81bea6e28
```


## R8-S02 idempotency contract graph

`nsl-write` adds the permitted internal workspace edge to `nsl-contract` for
current canonical Tool Contract hash and durable retention semantics. Core/IR
models remain shared, with no dependency on Runtime, Compiler or Source. All
**17 external packages, versions, checksums and resolved features are unchanged**
from R8-S01. Cargo.lock changes only the internal WRITE dependency list. The
locked check, architecture guard and frozen workspace build pass; lockfile bytes
match before and after the build.

```text
Cargo.lock SHA-256
0c9622b402712dd5cdd5a79f2bca9b4ec8a7ae4f2e2804586f3acc1e14a8870a
```


## R8-S03 current-policy guard graph

The Guard reuses Core/IR/Contract through the existing `nsl-write` graph. Borrowed
principal and execution-evidence projections preserve the prohibition on WRITE
reaching Runtime, Compiler or Source; no existing Runtime model is moved and no
external package/feature or workspace edge is added. All **17 external packages**
and Cargo.lock bytes remain unchanged. Locked metadata, the dependency guard and
the frozen build pass. Cargo.lock SHA-256 remains:

```text
0c9622b402712dd5cdd5a79f2bca9b4ec8a7ae4f2e2804586f3acc1e14a8870a
```


## R8-S04 durable requested-evidence graph

Request/record/receipt assembly reuses the existing Core/IR/Contract and SHA-256
dependencies of `nsl-write`. The async storage boundary uses standard-library
Future/Pin/Box types; borrowed identity/checkpoint projections preserve the
prohibition on WRITE reaching Runtime, Compiler or Source. No package, feature,
version or workspace edge changes. The **17 external packages** and Cargo.lock
bytes remain unchanged. Locked check, architecture guard and frozen build pass.
Cargo.lock SHA-256 remains:

```text
0c9622b402712dd5cdd5a79f2bca9b4ec8a7ae4f2e2804586f3acc1e14a8870a
```

## R8-S05 integration gate development graph

`nsl-integration` adds development-only paths to existing `nsl-core`, `nsl-ir`,
`nsl-contract`, `nsl-runtime` and `nsl-write`, and reuses reviewed `sha2 =0.11.1`
with default features disabled for corpus inventory checks. Its production
library/dependency graph remains empty. WRITE still cannot reach Runtime,
Compiler or Source. No external package, feature, version or checksum changes:
comparison with the previous lock confirms all **17 external package records**
are identical. Cargo.lock changes only the integration crate's declared list.
Locked workspace check, all-target/all-feature Clippy, dependency guard and frozen
build pass; the lock digest is stable before/after the build.

```text
Cargo.lock SHA-256
5dfa85682c9050eb3eda9fb72946ab36134367e4af675b9575f36947e24e2314
```
