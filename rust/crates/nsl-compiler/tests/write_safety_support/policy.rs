use super::ContractSafety;

fn decode(value: &str) -> String {
    if value == "-" {
        return String::new();
    }
    String::from_utf8(
        (0..value.len())
            .step_by(2)
            .map(|index| u8::from_str_radix(&value[index..index + 2], 16).unwrap())
            .collect(),
    )
    .unwrap()
}

#[test]
fn contract_policy_matches_frozen_oracle_injected_metadata() {
    let mut count = 0;
    for row in include_str!("../fixtures/write-safety-policy-oracle.tsv")
        .lines()
        .filter(|row| !row.starts_with('#'))
    {
        let columns = row.split('\t').collect::<Vec<_>>();
        let metadata = ContractSafety {
            capability: columns[1].parse().ok(),
            operation: columns[2].parse().ok(),
            atomicity: columns[3].parse().ok(),
            idempotency_required: match columns[4] {
                "true" => Some(true),
                "false" => Some(false),
                _ => None,
            },
            risk: columns[5].parse().ok(),
        };
        if let Some(kind) = metadata.violation() {
            assert_eq!(kind.code(), columns[6], "{}", columns[0]);
            assert_eq!(
                kind.message("TEST.WRITE"),
                decode(columns[7]),
                "{}",
                columns[0]
            );
        } else {
            assert_eq!(columns[6], "ok", "{}", columns[0]);
        }
        count += 1;
    }
    assert_eq!(count, 53);
}
