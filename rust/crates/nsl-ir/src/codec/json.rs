use super::{NsoCodecError, NsoLimit, NsoLimits, Result};
use nsl_core::{
    CanonicalJsonError, JsonDecodeError, JsonLimit, JsonLimits, ValueData, canonical_json,
    parse_json_with_limits,
};
pub(super) fn parse(bytes: &[u8], limits: NsoLimits) -> Result<ValueData> {
    parse_json_with_limits(
        bytes,
        JsonLimits {
            max_bytes: limits.max_bytes,
            max_depth: limits.max_depth,
            max_nodes: limits.max_nodes,
        },
    )
    .map_err(|e| match e {
        JsonDecodeError::Limit(k) => NsoCodecError::Limit(match k {
            JsonLimit::Bytes => NsoLimit::Bytes,
            JsonLimit::Depth => NsoLimit::Depth,
            JsonLimit::Nodes => NsoLimit::Nodes,
        }),
        JsonDecodeError::Json { offset, reason, .. } => NsoCodecError::Json {
            offset,
            reason: if reason == "JSON number tokens must be integers" {
                "NSO number tokens must be integers"
            } else {
                reason
            },
        },
    })
}
pub(super) fn canonical(value: &ValueData) -> Result<Vec<u8>> {
    canonical_json(value)
        .map_err(|CanonicalJsonError::IntegerTooWide| NsoCodecError::IntegerTooWide)
}

#[cfg(test)]
mod tests {
    use super::{canonical, parse};
    use crate::NsoLimits;

    #[test]
    fn canonical_json_normalizes_keys_and_integer_zero() {
        let input = br#"{"z":null,"\u0061":-0}"#;
        let limits = NsoLimits {
            max_bytes: input.len(),
            max_depth: 1,
            max_nodes: 3,
        };
        let data = parse(input, limits).unwrap();
        assert_eq!(canonical(&data).unwrap(), br#"{"a":0,"z":null}"#);
    }
}
