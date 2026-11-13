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

## Architecture dependency guard

From `rust/`, run `python tools/check_architecture.py` with Python 3.12 and the pinned Rust toolchain. The guard reads Cargo's locked, all-feature graph for all target platforms. It follows dependencies by resolved package ID, including build and development edges, so renaming a crate or depending on it indirectly still exposes the dependency chain.

`nsl-core` has no permitted workspace dependencies. `nsl-ir` may depend on `nsl-core`. Both may reach only explicitly reviewed external sources and packages, and their external allowlists are currently empty. `nsl-compiler` cannot reach `nsl-runtime`. `nsl-runtime` cannot reach `nsl-compiler` or `nsl-source`, which owns the AST.

Every member must inherit the workspace `unsafe_code = "forbid"` lint. Cargo's compile checks enforce that lint on workspace targets. Run `python -m unittest discover -s tools` to exercise the guard against temporary Cargo workspaces and verify compiler rejection of unsafe integration-test code.
