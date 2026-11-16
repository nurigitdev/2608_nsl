use super::{ir_1_0, ir_1_1, project};
use nsl_core::ValueData;
use nsl_ir::{NsoValidationError, validate_nso_document};
use std::collections::BTreeMap;

struct Case {
    name: &'static str,
    version: &'static str,
    edits: Vec<Edit>,
    expected: Outcome,
}

enum Edit {
    Set(&'static str, ValueData),
    Copy(&'static str, &'static str),
    Remove(&'static str),
}

#[derive(Debug)]
enum Outcome {
    Accept,
    Schema(Option<&'static str>),
    Credential,
}

include!("../fixtures/schema_cases.rs");

fn get<'a>(mut doc: &'a ValueData, path: &str) -> &'a ValueData {
    for segment in path.split('/') {
        doc = match doc {
            ValueData::Object(fields) => &fields[segment],
            ValueData::Array(items) => &items[segment.parse::<usize>().unwrap()],
            _ => panic!("invalid fixture pointer: {path}"),
        };
    }
    doc
}

fn apply(doc: &mut ValueData, edit: Edit) {
    let (path, replacement) = match edit {
        Edit::Set(path, value) => (path, Some(value)),
        Edit::Copy(source, path) => (path, Some(get(doc, source).clone())),
        Edit::Remove(path) => (path, None),
    };
    let mut parent = doc;
    let mut segments = path.split('/').peekable();
    let field = loop {
        let segment = segments.next().unwrap();
        if segments.peek().is_none() {
            break segment;
        }
        parent = match parent {
            ValueData::Object(fields) => fields.get_mut(segment).unwrap(),
            ValueData::Array(items) => &mut items[segment.parse::<usize>().unwrap()],
            _ => panic!("invalid fixture pointer: {path}"),
        };
    };
    match parent {
        ValueData::Object(fields) => {
            if let Some(value) = replacement {
                fields.insert(field.into(), value);
            } else {
                assert!(fields.remove(field).is_some());
            }
        }
        ValueData::Array(items) => {
            let index = field.parse::<usize>().unwrap();
            match replacement {
                Some(value) if index == items.len() => items.push(value),
                Some(value) => items[index] = value,
                None => {
                    items.remove(index);
                }
            }
        }
        _ => panic!("invalid fixture pointer: {path}"),
    }
}

#[test]
fn structured_admission_matches_frozen_oracle() {
    let read = project(&ir_1_0());
    let write = project(&ir_1_1());
    for case in cases() {
        let mut doc = match case.version {
            "1.0" => read.clone(),
            "1.1" => write.clone(),
            _ => panic!("unknown fixture version"),
        };
        for edit in case.edits {
            apply(&mut doc, edit);
        }
        let result = validate_nso_document(&doc);
        match (&case.expected, &result) {
            (Outcome::Accept, Ok(())) => {}
            (Outcome::Schema(path), Err(NsoValidationError::Schema(error))) => {
                if let Some(path) = path {
                    assert_eq!(error.path, *path, "{}: {error}", case.name);
                }
            }
            (Outcome::Credential, Err(NsoValidationError::CredentialMaterial(error))) => {
                assert_eq!(error.to_string(), "credential material is forbidden");
            }
            _ => panic!(
                "{}: expected {:?}, got {result:?}",
                case.name, case.expected
            ),
        }
    }
}

#[test]
fn schema_error_does_not_echo_caller_controlled_fields() {
    let mut doc = project(&ir_1_0());
    apply(
        &mut doc,
        Edit::Set("customer-supplied-field", "customer-supplied-text".into()),
    );
    let error = validate_nso_document(&doc).unwrap_err();
    assert_eq!(
        error.to_string(),
        "invalid NSO schema at $: unexpected fields"
    );
}
