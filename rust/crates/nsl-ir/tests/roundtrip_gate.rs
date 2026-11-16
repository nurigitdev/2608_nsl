use std::{
    collections::BTreeSet,
    fs,
    path::{Path, PathBuf},
};

use nsl_ir::{
    NsoCodec, NsoCodecError, NsoLimits, NsoValidationError, artifact_sha256, source_manifest_sha256,
};

const ORACLE: &str = include_str!("fixtures/roundtrip-oracle.tsv");
const LIMITS: NsoLimits = NsoLimits {
    max_bytes: 24_000,
    max_depth: 64,
    max_nodes: 2_000,
};

fn corpus_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../compatibility/v0_2_1")
}

fn rows() -> impl Iterator<Item = Vec<&'static str>> {
    ORACLE
        .lines()
        .filter(|line| !line.starts_with('#'))
        .map(|line| line.split('\t').collect())
}

#[test]
fn all_admitted_frozen_nsos_roundtrip_with_oracle_bytes_and_hashes() {
    let root = corpus_root();
    let mut versions = BTreeSet::new();
    for row in rows().filter(|row| row[0] == "roundtrip") {
        let ["roundtrip", path, version, artifact, manifest, semantic] = row.as_slice() else {
            panic!("invalid round-trip row: {row:?}");
        };
        let bytes = fs::read(root.join(path)).unwrap();
        let model =
            NsoCodec::decode(&bytes, LIMITS).unwrap_or_else(|error| panic!("{path}: {error}"));
        let encoded = NsoCodec::encode(&model, LIMITS.max_bytes).unwrap();
        assert_eq!(model.ir_version.as_str(), *version, "{path}");
        assert_eq!(encoded, bytes, "{path}");
        assert_eq!(artifact_sha256(&encoded).as_str(), *artifact, "{path}");
        assert_eq!(
            source_manifest_sha256(&model.build.sources)
                .unwrap()
                .as_str(),
            *manifest,
            "{path}"
        );
        assert_eq!(
            model.build.compute_source_bundle_sha256().unwrap().as_str(),
            *manifest,
            "{path}"
        );
        assert_eq!(
            model.build.source_bundle_sha256.as_str(),
            *manifest,
            "{path}"
        );
        assert_eq!(
            model
                .compute_semantic_sha256(LIMITS.max_bytes)
                .unwrap()
                .as_str(),
            *semantic,
            "{path}"
        );
        assert_eq!(model.semantic_hash.as_str(), *semantic, "{path}");
        versions.insert(*version);
    }
    assert_eq!(versions, BTreeSet::from(["1.0", "1.1"]));
}

fn decode_hex(hex: &str) -> Vec<u8> {
    if hex == "-" {
        return Vec::new();
    }
    assert!(hex.len().is_multiple_of(2), "odd fixture hex length");
    (0..hex.len())
        .step_by(2)
        .map(|start| u8::from_str_radix(&hex[start..start + 2], 16).unwrap())
        .collect()
}

#[test]
fn public_decoder_matches_oracle_rejection_boundaries() {
    let root = corpus_root();
    for row in rows().filter(|row| row[0] == "reject") {
        let [
            "reject",
            label,
            path,
            kind,
            field,
            artifact,
            start,
            removed,
            replacement,
        ] = row.as_slice()
        else {
            panic!("invalid rejection row: {row:?}");
        };
        let mut bytes = fs::read(root.join(path)).unwrap();
        let start = start.parse::<usize>().unwrap();
        let end = start + removed.parse::<usize>().unwrap();
        bytes.splice(start..end, decode_hex(replacement));
        assert_eq!(artifact_sha256(&bytes).as_str(), *artifact, "{label}");
        let error = NsoCodec::decode(&bytes, LIMITS)
            .expect_err("the frozen Oracle rejected these exact NSO bytes");
        match (*kind, &error) {
            ("schema", NsoCodecError::Validation(NsoValidationError::Schema(error))) => {
                assert_eq!(error.path, *field, "{label}");
            }
            (
                "credential",
                NsoCodecError::Validation(NsoValidationError::CredentialMaterial(_)),
            ) => {
                assert_eq!(
                    error.to_string(),
                    "credential material is forbidden",
                    "{label}"
                );
            }
            ("bundle", NsoCodecError::InconsistentSourceBundle)
            | ("manifest", NsoCodecError::SourceManifestMismatch)
            | ("semantic", NsoCodecError::SemanticHashMismatch) => {}
            _ => panic!("{label}: expected {kind} at {field}, got {error:?}"),
        }
    }
}

#[test]
fn every_frozen_nso_has_an_oracle_gate_expectation() {
    let root = corpus_root();
    let expected: BTreeSet<PathBuf> = rows()
        .map(|row| match row.as_slice() {
            ["roundtrip", path, ..] | ["reject", _, path, ..] => PathBuf::from(path),
            _ => panic!("unknown fixture row: {row:?}"),
        })
        .collect();
    let mut found = BTreeSet::new();
    let mut folders = vec![root.clone()];
    while let Some(folder) = folders.pop() {
        for entry in fs::read_dir(folder).unwrap() {
            let entry = entry.unwrap();
            let path = entry.path();
            if entry.file_type().unwrap().is_dir() {
                folders.push(path);
            } else if path.extension().is_some_and(|value| value == "nso") {
                found.insert(path.strip_prefix(&root).unwrap().to_owned());
            }
        }
    }
    assert_eq!(found, expected);
}
