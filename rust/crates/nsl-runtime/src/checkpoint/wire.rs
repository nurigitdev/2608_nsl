use super::*;
use nsl_core::{
    Integer, JsonDecodeError, canonical_json, ensure_no_credential_material, parse_json_with_limits,
};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
#[derive(Clone, Eq, PartialEq)]
pub struct CheckpointSpec {
    pub execution_id: String,
    pub business_operation_id: String,
    pub checkpoint_version: Integer,
    pub skill_id: String,
    pub skill_version: String,
    pub semantic_hash: String,
    pub ir_version: String,
    pub runtime_version: String,
    pub state_payload: Vec<u8>,
}
impl fmt::Debug for CheckpointSpec {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("CheckpointSpec { .. }")
    }
}
#[derive(Clone, Eq, PartialEq)]
pub struct ExecutionCheckpoint {
    spec: CheckpointSpec,
    business_operation_id: nsl_core::BusinessOperationId,
    integrity_hash: String,
}
impl fmt::Debug for ExecutionCheckpoint {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ExecutionCheckpoint")
            .field("checkpoint_version", &self.spec.checkpoint_version)
            .field("state_bytes", &self.spec.state_payload.len())
            .finish_non_exhaustive()
    }
}
impl ExecutionCheckpoint {
    pub fn create(spec: CheckpointSpec, limits: CheckpointLimits) -> Result<Self> {
        for (name, v) in [
            ("execution_id", &spec.execution_id),
            ("business_operation_id", &spec.business_operation_id),
            ("skill_id", &spec.skill_id),
            ("skill_version", &spec.skill_version),
            ("semantic_hash", &spec.semantic_hash),
            ("ir_version", &spec.ir_version),
            ("runtime_version", &spec.runtime_version),
        ] {
            if v.len() > limits.outer.max_bytes {
                return Err(limit("$", JsonLimit::Bytes));
            }
            required(&ValueData::String(v.clone()), &format!("$.{name}"))?;
        }
        if crate::data::bigint(&spec.checkpoint_version) <= 0.into() {
            return Err(error("$.checkpoint_version", "expected a positive integer"));
        }
        if spec.state_payload.len() > limits.state.max_bytes {
            return Err(limit("$.state_payload", JsonLimit::Bytes));
        }
        let business_operation_id = spec
            .business_operation_id
            .parse()
            .expect("validated nonblank business identity");
        let mut cp = Self {
            spec,
            business_operation_id,
            integrity_hash: String::new(),
        };
        cp.integrity_hash = cp.digest(limits)?;
        // The final envelope also includes the digest field.
        canonical(&cp.to_data(limits)?, limits.outer, "$")?;
        Ok(cp)
    }
    pub fn business_operation_id(&self) -> &nsl_core::BusinessOperationId {
        &self.business_operation_id
    }
    pub fn metadata(&self) -> &CheckpointSpec {
        &self.spec
    }
    pub fn state_payload(&self) -> &[u8] {
        &self.spec.state_payload
    }
    pub fn integrity_hash(&self) -> &str {
        &self.integrity_hash
    }
    fn material(&self, limits: CheckpointLimits) -> Result<BTreeMap<String, ValueData>> {
        if self.spec.state_payload.len() > limits.state.max_bytes {
            return Err(limit("$.state_payload", JsonLimit::Bytes));
        }
        let n = self
            .spec
            .state_payload
            .len()
            .checked_add(2)
            .and_then(|n| (n / 3).checked_mul(4))
            .ok_or_else(|| limit("$", JsonLimit::Bytes))?;
        if n > limits.outer.max_bytes {
            return Err(limit("$", JsonLimit::Bytes));
        }
        Ok([
            ("schema_version", ValueData::Number(1.into())),
            ("execution_id", crate::data::text(&self.spec.execution_id)),
            (
                "business_operation_id",
                crate::data::text(self.business_operation_id.as_str()),
            ),
            (
                "checkpoint_version",
                ValueData::Number(self.spec.checkpoint_version.clone()),
            ),
            ("skill_id", crate::data::text(&self.spec.skill_id)),
            ("skill_version", crate::data::text(&self.spec.skill_version)),
            ("semantic_hash", crate::data::text(&self.spec.semantic_hash)),
            ("ir_version", crate::data::text(&self.spec.ir_version)),
            (
                "runtime_version",
                crate::data::text(&self.spec.runtime_version),
            ),
            (
                "state_payload",
                ValueData::String(base64_encode(&self.spec.state_payload)),
            ),
        ]
        .into_iter()
        .map(|(k, v)| (k.into(), v))
        .collect())
    }
    fn digest(&self, limits: CheckpointLimits) -> Result<String> {
        Ok(nsl_core::PayloadHash::from_digest(
            Sha256::digest(canonical(
                &ValueData::Object(self.material(limits)?),
                limits.outer,
                "$",
            )?)
            .into(),
        )
        .to_string())
    }
    pub fn verify(&self, limits: CheckpointLimits) -> Result<()> {
        if self.digest(limits)? != self.integrity_hash {
            return Err(CheckpointError {
                kind: CheckpointErrorKind::Integrity,
                ..error("$.integrity_hash", "checkpoint integrity mismatch")
            });
        }
        Ok(())
    }
    pub fn to_data(&self, limits: CheckpointLimits) -> Result<ValueData> {
        let mut data = self.material(limits)?;
        data.insert(
            "integrity_hash".into(),
            crate::data::text(&self.integrity_hash),
        );
        Ok(ValueData::Object(data))
    }
    pub fn to_bytes(&self, limits: CheckpointLimits) -> Result<Vec<u8>> {
        self.verify(limits)?;
        canonical(&self.to_data(limits)?, limits.outer, "$")
    }
    pub fn from_bytes(bytes: &[u8], limits: CheckpointLimits) -> Result<Self> {
        let data = parse(bytes, limits.outer, "$")?;
        let fields = exact(
            &data,
            &[
                "schema_version",
                "execution_id",
                "business_operation_id",
                "checkpoint_version",
                "skill_id",
                "skill_version",
                "semantic_hash",
                "ir_version",
                "runtime_version",
                "state_payload",
                "integrity_hash",
            ],
            "$",
        )?;
        if integer(&fields["schema_version"], "$.schema_version", false)? != Integer::from(1) {
            return Err(error("$.schema_version", "unsupported checkpoint schema"));
        }
        let state = match &fields["state_payload"] {
            ValueData::String(s) => base64_decode(s, limits.state.max_bytes)?,
            _ => return Err(error("$.state_payload", "invalid base64 payload")),
        };
        let spec = CheckpointSpec {
            execution_id: required(&fields["execution_id"], "$.execution_id")?.into(),
            business_operation_id: required(
                &fields["business_operation_id"],
                "$.business_operation_id",
            )?
            .into(),
            checkpoint_version: integer(
                &fields["checkpoint_version"],
                "$.checkpoint_version",
                true,
            )?,
            skill_id: required(&fields["skill_id"], "$.skill_id")?.into(),
            skill_version: required(&fields["skill_version"], "$.skill_version")?.into(),
            semantic_hash: required(&fields["semantic_hash"], "$.semantic_hash")?.into(),
            ir_version: required(&fields["ir_version"], "$.ir_version")?.into(),
            runtime_version: required(&fields["runtime_version"], "$.runtime_version")?.into(),
            state_payload: state,
        };
        let hash = match &fields["integrity_hash"] {
            ValueData::String(s) if s.starts_with("sha256:") && s.chars().count() == 71 => s,
            _ => return Err(error("$.integrity_hash", "expected a SHA-256 value")),
        };
        let business_operation_id = spec
            .business_operation_id
            .parse()
            .expect("validated nonblank business identity");
        let cp = Self {
            spec,
            business_operation_id,
            integrity_hash: hash.clone(),
        };
        cp.verify(limits)?;
        Ok(cp)
    }
}
pub(super) fn parse(bytes: &[u8], limits: JsonLimits, path: &str) -> Result<ValueData> {
    parse_json_with_limits(bytes, limits).map_err(|e| match e {
        JsonDecodeError::Limit(k) => limit(path, k),
        JsonDecodeError::Json {
            offset,
            reason,
            path,
        } => CheckpointError {
            kind: if reason == "JSON number tokens must be integers" {
                CheckpointErrorKind::Type
            } else {
                CheckpointErrorKind::Json
            },
            offset: Some(offset),
            ..error(&path, reason)
        },
    })
}
pub(super) fn canonical(data: &ValueData, limits: JsonLimits, path: &str) -> Result<Vec<u8>> {
    let mut pending = vec![(data, 0usize)];
    let mut nodes = 0usize;
    let mut bytes = 0usize;
    let quoted = |s: &str| -> usize {
        2usize.saturating_add(
            s.chars()
                .map(|c| match c {
                    '"' | '\\' | '\n' | '\r' | '\t' | '\u{8}' | '\u{c}' => 2,
                    '\0'..='\u{1f}' => 6,
                    _ => c.len_utf8(),
                })
                .sum::<usize>(),
        )
    };
    while let Some((v, depth)) = pending.pop() {
        if depth > limits.max_depth {
            return Err(limit(path, JsonLimit::Depth));
        }
        nodes = nodes.saturating_add(1);
        if nodes > limits.max_nodes {
            return Err(limit(path, JsonLimit::Nodes));
        }
        let n = match v {
            ValueData::Null => 4,
            ValueData::Bool(true) => 4,
            ValueData::Bool(false) => 5,
            ValueData::Number(n) => n.as_str().len(),
            ValueData::String(s) => quoted(s),
            ValueData::Array(a) => {
                pending.extend(a.iter().map(|v| (v, depth + 1)));
                2 + a.len().saturating_sub(1)
            }
            ValueData::Object(o) => {
                pending.extend(o.values().map(|v| (v, depth + 1)));
                2 + o.len().saturating_sub(1) + o.keys().map(|k| quoted(k) + 1).sum::<usize>()
            }
        };
        bytes = bytes.saturating_add(n);
        if bytes > limits.max_bytes {
            return Err(limit(path, JsonLimit::Bytes));
        }
    }
    canonical_json(data).map_err(|_| CheckpointError {
        kind: CheckpointErrorKind::Encoding,
        ..error(path, "state is not serializable")
    })
}
pub(super) fn object<'a>(v: &'a ValueData, path: &str) -> Result<&'a BTreeMap<String, ValueData>> {
    if let ValueData::Object(o) = v {
        Ok(o)
    } else {
        Err(error(path, "expected an object"))
    }
}
pub(super) fn exact<'a>(
    v: &'a ValueData,
    fields: &[&str],
    path: &str,
) -> Result<&'a BTreeMap<String, ValueData>> {
    let ValueData::Object(o) = v else {
        return Err(error(path, "invalid object schema"));
    };
    if o.len() != fields.len() || fields.iter().any(|k| !o.contains_key(*k)) {
        return Err(error(path, "invalid object schema"));
    }
    Ok(o)
}
pub(super) fn required<'a>(v: &'a ValueData, path: &str) -> Result<&'a str> {
    if let ValueData::String(s) = v
        && !crate::data::blank(s)
    {
        credential(v, path)?;
        return Ok(s);
    }
    Err(error(path, "expected a non-empty string"))
}
pub(super) fn credential(v: &ValueData, path: &str) -> Result<()> {
    ensure_no_credential_material(v).map_err(|_| CheckpointError {
        kind: CheckpointErrorKind::Credential,
        ..error(path, "credential material is forbidden")
    })
}
pub(super) fn integer(v: &ValueData, path: &str, positive: bool) -> Result<Integer> {
    let ValueData::Number(n) = v else {
        return Err(typed(path, "expected an integer"));
    };
    if crate::data::bigint(n) < 0.into() {
        return Err(error(path, "expected a non-negative integer"));
    }
    if positive && n == &Integer::from(0) {
        return Err(error(path, "expected a positive integer"));
    }
    Ok(n.clone())
}
pub(super) fn optional(v: &ValueData, path: &str) -> Result<Option<String>> {
    if v == &ValueData::Null {
        Ok(None)
    } else {
        required(v, path).map(|s| Some(s.into()))
    }
}
const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
fn base64_encode(bytes: &[u8]) -> String {
    let mut out = String::new();
    for chunk in bytes.chunks(3) {
        let a = chunk[0];
        let b = chunk.get(1).copied().unwrap_or(0);
        let c = chunk.get(2).copied().unwrap_or(0);
        for n in [a >> 2, ((a & 3) << 4) | (b >> 4)] {
            out.push(char::from(ALPHABET[usize::from(n)]));
        }
        out.push(if chunk.len() > 1 {
            char::from(ALPHABET[usize::from(((b & 15) << 2) | (c >> 6))])
        } else {
            '='
        });
        out.push(if chunk.len() > 2 {
            char::from(ALPHABET[usize::from(c & 63)])
        } else {
            '='
        });
    }
    out
}
fn base64_decode(text: &str, max_bytes: usize) -> Result<Vec<u8>> {
    let bad = || error("$.state_payload", "invalid base64 payload");
    let letters = text.find('=').unwrap_or(text.len());
    let padding = text.len() - letters;
    let rem = letters % 4;
    if rem == 1
        || (letters == 0 && padding > 0)
        || !text.as_bytes()[letters..].iter().all(|b| *b == b'=')
        || (rem == 2 && padding != 2)
        || (rem == 3 && padding != 1)
    {
        return Err(bad());
    }
    let size = letters / 4 * 3
        + match rem {
            2 => 1,
            3 => 2,
            _ => 0,
        };
    if size > max_bytes {
        return Err(limit("$.state_payload", JsonLimit::Bytes));
    }
    let mut out = Vec::with_capacity(size);
    let mut acc = 0u32;
    let mut bits = 0;
    for byte in text.as_bytes()[..letters].iter().copied() {
        let digit = ALPHABET.iter().position(|b| *b == byte).ok_or_else(bad)? as u32;
        acc = (acc << 6) | digit;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((acc >> bits) as u8);
            acc &= (1 << bits) - 1;
        }
    }
    Ok(out)
}
