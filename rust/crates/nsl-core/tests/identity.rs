use nsl_core::{
    ApprovalRequestId, BusinessOperationId, IdempotencyKey, IdentityError, PayloadHash,
};

#[test]
fn opaque_identifiers_reject_blank_input_and_preserve_text() {
    for input in ["", " \t\r\n", "\u{a0}", "\u{1c}\u{1f}"] {
        assert_eq!(
            input.parse::<BusinessOperationId>(),
            Err(IdentityError::Blank)
        );
        assert_eq!(
            input.parse::<ApprovalRequestId>(),
            Err(IdentityError::Blank)
        );
    }
    for input in ["x", "business-42", " 승인-42 "] {
        let business = input.parse::<BusinessOperationId>().unwrap();
        let approval = input.parse::<ApprovalRequestId>().unwrap();
        assert_eq!(business.as_str(), input);
        assert_eq!(approval.as_str(), input);
        assert_eq!(business.to_string(), input);
        assert_eq!(approval.to_string(), input);
    }
}

#[test]
fn idempotency_key_validates_prefix_and_character_count() {
    for suffix in [
        "0a".repeat(32),
        "A".repeat(64),
        "z".repeat(64),
        "한".repeat(64),
    ] {
        let input = format!("idem:sha256:{suffix}");
        let key = input.parse::<IdempotencyKey>().unwrap();
        assert_eq!(key.as_str(), input);
        assert_eq!(key.to_string(), input);
    }
    for input in [
        String::new(),
        "idem:sha256:".to_owned(),
        format!("idem:sha256:{}", "a".repeat(63)),
        format!("idem:sha256:{}", "a".repeat(65)),
        format!("idem:sha256:{}", "한".repeat(63)),
        format!("sha256:{}", "a".repeat(64)),
        format!("IDEM:sha256:{}", "a".repeat(64)),
    ] {
        assert_eq!(
            input.parse::<IdempotencyKey>(),
            Err(IdentityError::InvalidIdempotencyKey)
        );
    }
}

#[test]
fn payload_hash_requires_lowercase_ascii_hexadecimal() {
    let input = format!("sha256:{}", "0123456789abcdef".repeat(4));
    let hash = input.parse::<PayloadHash>().unwrap();
    assert_eq!(hash.as_str(), input);
    assert_eq!(hash.to_string(), input);
    let digest = [
        0x01, 0x23, 0x45, 0x67, 0x89, 0xab, 0xcd, 0xef, 0x01, 0x23, 0x45, 0x67, 0x89, 0xab, 0xcd,
        0xef, 0x01, 0x23, 0x45, 0x67, 0x89, 0xab, 0xcd, 0xef, 0x01, 0x23, 0x45, 0x67, 0x89, 0xab,
        0xcd, 0xef,
    ];
    assert_eq!(PayloadHash::from_digest(digest), hash);
    for input in [
        String::new(),
        "sha256:".to_owned(),
        format!("sha256:{}", "a".repeat(63)),
        format!("sha256:{}", "a".repeat(65)),
        format!("sha256:{}", "A".repeat(64)),
        format!("sha256:{}", "z".repeat(64)),
        format!("sha256:{}", "한".repeat(64)),
        format!("idem:sha256:{}", "a".repeat(64)),
        format!("SHA256:{}", "a".repeat(64)),
    ] {
        assert_eq!(
            input.parse::<PayloadHash>(),
            Err(IdentityError::InvalidPayloadHash)
        );
    }
}

#[test]
fn identity_errors_omit_the_rejected_value() {
    for (error, text) in [
        (
            " ".parse::<BusinessOperationId>().unwrap_err(),
            "identifier must contain a non-whitespace character",
        ),
        (
            "bad-key".parse::<IdempotencyKey>().unwrap_err(),
            "idempotency key must start with idem:sha256: and contain a 64-character suffix",
        ),
        (
            "bad-digest".parse::<PayloadHash>().unwrap_err(),
            "payload hash must start with sha256: and contain 64 lowercase hexadecimal digits",
        ),
    ] {
        assert_eq!(error.to_string(), text);
    }
}
