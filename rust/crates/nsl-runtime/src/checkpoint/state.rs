use super::{wire::*, *};
use crate::{
    ApprovalState, CheckResult, CheckpointState, EmitRecord, ExecutionContext,
    ExecutionSecurityContext, IdempotencyState, MAX_FOREACH_NESTING_DEPTH, ResourceUsage,
    RuntimeClock, WriteJournalState,
};
use nsl_core::{
    ApprovalStatus, CheckStatus, Completeness, DataClassification, Integer, Money, Presence,
    TypeRef, Value, ValueEnvelope,
};
use std::collections::BTreeMap;
type Namespace = BTreeMap<String, ValueEnvelope>;
pub(crate) struct RestoredState {
    pub inputs: Namespace,
    pub contexts: Namespace,
    pub frames: Vec<(Namespace, Namespace)>,
    pub checks: Vec<CheckResult>,
    pub outputs: Vec<EmitRecord>,
    pub resources: ResourceUsage,
    pub completeness: Completeness,
    pub invocation_counter: Integer,
    pub foreach_depth: usize,
    pub current_node: Option<String>,
    pub approvals: ApprovalState,
    pub writes: WriteJournalState,
    pub idempotency: IdempotencyState,
    pub checkpoint: CheckpointState,
}
fn obj(items: impl IntoIterator<Item = (&'static str, ValueData)>) -> ValueData {
    ValueData::Object(items.into_iter().map(|(k, v)| (k.into(), v)).collect())
}
fn text(s: &str) -> ValueData {
    ValueData::String(s.into())
}
fn number(n: &Integer) -> ValueData {
    ValueData::Number(n.clone())
}
fn opt(s: Option<&str>) -> ValueData {
    s.map_or(ValueData::Null, text)
}
fn strings(s: impl IntoIterator<Item = impl AsRef<str>>) -> ValueData {
    ValueData::Array(s.into_iter().map(|v| text(v.as_ref())).collect())
}
fn string_map(s: &BTreeMap<String, String>) -> ValueData {
    ValueData::Object(s.iter().map(|(k, v)| (k.clone(), text(v))).collect())
}
pub(super) fn security(s: &ExecutionSecurityContext) -> ValueData {
    let p = &s.principal;
    let policy = &s.data_policy;
    obj([
        (
            "principal",
            obj([
                ("tenant_id", text(&p.tenant_id)),
                ("subject_id", text(&p.subject_id)),
                ("actor_type", text(&p.actor_type)),
                ("roles", strings(&p.roles)),
                ("scopes", strings(&p.scopes)),
                ("auth_context_ref", text(&p.auth_context_ref)),
                ("verification", text(p.verification.as_str())),
                ("on_behalf_of", opt(p.on_behalf_of.as_deref())),
            ]),
        ),
        (
            "data_policy",
            obj([
                (
                    "max_trace_classification",
                    text(policy.max_trace_classification().as_str()),
                ),
                (
                    "snapshot_retention_days",
                    number(policy.snapshot_retention_days()),
                ),
                (
                    "audit_retention_days",
                    number(policy.audit_retention_days()),
                ),
            ]),
        ),
    ])
}
fn envelope_data(e: &ValueEnvelope, limits: CheckpointLimits) -> Result<ValueData> {
    Ok(obj([
        (
            "value",
            e.value()
                .to_data(limits.max_decimal_bytes)
                .map_err(|_| CheckpointError {
                    kind: CheckpointErrorKind::Encoding,
                    ..error("$.state_payload", "state is not serializable")
                })?,
        ),
        (
            "type",
            e.type_ref()
                .to_data()
                .map_err(|_| error("$.state_payload", "state is not serializable"))?,
        ),
        ("presence", text(e.presence().as_str())),
        ("completeness", text(e.completeness().as_str())),
        ("classification", text(e.classification().as_str())),
        ("provenance_refs", strings(e.provenance_refs())),
    ]))
}
fn namespace_data(ns: &Namespace, limits: CheckpointLimits) -> Result<ValueData> {
    Ok(ValueData::Object(
        ns.iter()
            .map(|(k, v)| Ok((k.clone(), envelope_data(v, limits)?)))
            .collect::<Result<_>>()?,
    ))
}
// Bound borrowed native values before recursive Core projection or journal clones.
// These are lower bounds on final JSON cost; canonical() enforces exact totals.
fn preflight<C: RuntimeClock>(ctx: &ExecutionContext<C>, limits: CheckpointLimits) -> Result<()> {
    enum Item<'a> {
        Value(&'a Value, usize),
        Data(&'a ValueData, usize),
        Type(&'a TypeRef, usize),
    }
    let mut work = Vec::new();
    let p = &ctx.security_context().principal;
    let mut nodes = ctx
        .frame_count()
        .saturating_add(ctx.checks.len())
        .saturating_add(ctx.outputs.len())
        .saturating_add(p.roles.len())
        .saturating_add(p.scopes.len());
    for e in ctx.envelopes() {
        nodes = nodes.saturating_add(e.provenance_refs().len());
    }
    for c in &ctx.checks {
        nodes = nodes.saturating_add(c.provenance_refs.len());
    }
    for m in [
        &ctx.idempotency.keys_by_write_node,
        &ctx.idempotency.payload_hashes_by_write_node,
        &ctx.checkpoint.approval_request_ids_by_write_node,
        &ctx.checkpoint.write_intent_hashes_by_write_node,
        &ctx.checkpoint.payload_hashes_by_write_node,
    ] {
        nodes = nodes.saturating_add(m.len());
    }
    nodes = nodes.saturating_add(ctx.approvals.records.len());
    if nodes > limits.state.max_nodes {
        return Err(limit("$.state_payload", JsonLimit::Nodes));
    }
    let mut bytes = 0usize;
    let mut text_cost = |s: &str| -> Result<()> {
        bytes = bytes.saturating_add(s.len());
        if bytes > limits.state.max_bytes {
            Err(limit("$.state_payload", JsonLimit::Bytes))
        } else {
            Ok(())
        }
    };
    let usage = ctx.guard().checkpoint_usage();
    for n in [
        &usage.tool_calls,
        &usage.loop_iterations,
        &usage.emitted_rows,
        &usage.max_collection_size_seen,
        &ctx.invocation_counter,
        &ctx.checkpoint.checkpoint_version,
        &ctx.checkpoint.trace_sequence,
        ctx.security_context().data_policy.snapshot_retention_days(),
        ctx.security_context().data_policy.audit_retention_days(),
    ] {
        text_cost(n.as_str())?;
    }
    for v in [
        &p.tenant_id,
        &p.subject_id,
        &p.actor_type,
        &p.auth_context_ref,
    ]
    .into_iter()
    .chain(p.roles.iter())
    .chain(p.scopes.iter())
    .chain(p.on_behalf_of.iter())
    .chain(ctx.current_node_id.iter())
    .chain(ctx.checkpoint.last_completed_node_id.iter())
    .chain(ctx.checkpoint.suspended_at_node_id.iter())
    {
        text_cost(v)?;
    }
    for k in ctx
        .input_values()
        .keys()
        .chain(ctx.context_values().keys())
        .chain(ctx.approvals.records.keys())
    {
        text_cost(k)?;
    }
    for (values, checks) in ctx.checkpoint_frames() {
        for k in values.keys().chain(checks.keys()) {
            text_cost(k)?;
        }
    }
    for c in &ctx.checks {
        for v in [&c.check_id, &c.severity, &c.message, &c.condition_node_id]
            .into_iter()
            .chain(c.provenance_refs.iter())
            .chain(c.reason_code.iter())
        {
            text_cost(v)?;
        }
    }
    for output in &ctx.outputs {
        for (k, _) in output.fields() {
            text_cost(k)?;
        }
    }
    for map in [
        &ctx.idempotency.keys_by_write_node,
        &ctx.idempotency.payload_hashes_by_write_node,
        &ctx.checkpoint.approval_request_ids_by_write_node,
        &ctx.checkpoint.write_intent_hashes_by_write_node,
        &ctx.checkpoint.payload_hashes_by_write_node,
    ] {
        for (k, v) in map {
            text_cost(k)?;
            text_cost(v)?;
        }
    }
    for e in ctx.envelopes() {
        for r in e.provenance_refs() {
            text_cost(r)?;
        }
        work.push(Item::Value(e.value(), 4));
        work.push(Item::Type(e.type_ref(), 4));
        if work.len() > limits.state.max_nodes {
            return Err(limit("$.state_payload", JsonLimit::Nodes));
        }
    }
    for v in &ctx.writes.entries {
        work.push(Item::Data(v, 2));
        if work.len() > limits.state.max_nodes {
            return Err(limit("$.state_payload", JsonLimit::Nodes));
        }
    }
    while let Some(item) = work.pop() {
        let depth = match &item {
            Item::Value(_, d) | Item::Data(_, d) | Item::Type(_, d) => *d,
        };
        if depth > limits.state.max_depth {
            return Err(limit("$.state_payload", JsonLimit::Depth));
        }
        nodes = nodes.saturating_add(1);
        if nodes > limits.state.max_nodes {
            return Err(limit("$.state_payload", JsonLimit::Nodes));
        }
        let remaining = limits.state.max_nodes.saturating_sub(nodes + work.len());
        match item {
            Item::Value(v, d) => match v {
                Value::List(a) => {
                    if a.len() > remaining {
                        return Err(limit("$.state_payload", JsonLimit::Nodes));
                    }
                    work.extend(a.iter().map(|v| Item::Value(v, d + 1)));
                }
                Value::Record(o) => {
                    if o.len() > remaining {
                        return Err(limit("$.state_payload", JsonLimit::Nodes));
                    }
                    for (k, v) in o {
                        text_cost(k)?;
                        work.push(Item::Value(v, d + 1));
                    }
                }
                Value::String(s) | Value::Date(s) | Value::DateTime(s) => text_cost(s)?,
                Value::Int(n) => text_cost(n.as_str())?,
                Value::Decimal(n) => text_cost(
                    &n.to_fixed_point(limits.max_decimal_bytes.min(limits.state.max_bytes))
                        .map_err(|_| CheckpointError {
                            kind: CheckpointErrorKind::Encoding,
                            ..error("$.state_payload", "Decimal encoding limit exceeded")
                        })?,
                )?,
                Value::Money(m) => text_cost(
                    &m.amount()
                        .to_fixed_point(limits.max_decimal_bytes.min(limits.state.max_bytes))
                        .map_err(|_| CheckpointError {
                            kind: CheckpointErrorKind::Encoding,
                            ..error("$.state_payload", "Decimal encoding limit exceeded")
                        })?,
                )?,
                _ => {}
            },
            Item::Data(v, d) => match v {
                ValueData::Array(a) => {
                    if a.len() > remaining {
                        return Err(limit("$.state_payload", JsonLimit::Nodes));
                    }
                    work.extend(a.iter().map(|v| Item::Data(v, d + 1)));
                }
                ValueData::Object(o) => {
                    if o.len() > remaining {
                        return Err(limit("$.state_payload", JsonLimit::Nodes));
                    }
                    for (k, v) in o {
                        text_cost(k)?;
                        work.push(Item::Data(v, d + 1));
                    }
                }
                ValueData::String(s) => text_cost(s)?,
                ValueData::Number(n) => text_cost(n.as_str())?,
                _ => {}
            },
            Item::Type(t, d) => match t.kind() {
                nsl_core::TypeKind::List(t) | nsl_core::TypeKind::Optional(t) => {
                    work.push(Item::Type(t, d + 1))
                }
                nsl_core::TypeKind::Record { name, fields } => {
                    text_cost(name)?;
                    if fields.len() > remaining {
                        return Err(limit("$.state_payload", JsonLimit::Nodes));
                    }
                    for (k, t) in fields {
                        text_cost(k)?;
                        work.push(Item::Type(t, d + 1));
                    }
                }
                nsl_core::TypeKind::Primitive(s) | nsl_core::TypeKind::Domain(s) => text_cost(s)?,
                nsl_core::TypeKind::Enum { name, values } => {
                    text_cost(name)?;
                    for s in values {
                        text_cost(s)?;
                    }
                }
                _ => {}
            },
        }
    }
    Ok(())
}
pub(super) fn encode<C: RuntimeClock>(
    ctx: &ExecutionContext<C>,
    version: &Integer,
    limits: CheckpointLimits,
) -> Result<ValueData> {
    preflight(ctx, limits)?;
    let mut frames = vec![];
    let mut checks = vec![];
    for (values, check) in ctx.checkpoint_frames() {
        frames.push(namespace_data(values, limits)?);
        checks.push(namespace_data(check, limits)?);
    }
    let checkpoint = &ctx.checkpoint;
    Ok(obj([
        ("schema_version", ValueData::Number(1.into())),
        ("checkpoint_version", number(version)),
        ("security", security(ctx.security_context())),
        (
            "symbols",
            obj([
                ("inputs", namespace_data(ctx.input_values(), limits)?),
                ("contexts", namespace_data(ctx.context_values(), limits)?),
                ("frames", ValueData::Array(frames)),
                ("check_frames", ValueData::Array(checks)),
            ]),
        ),
        (
            "checks",
            ValueData::Array(ctx.checks.iter().map(CheckResult::to_data).collect()),
        ),
        (
            "outputs",
            ValueData::Array(
                ctx.outputs
                    .iter()
                    .map(|r| namespace_data(&r.fields().iter().cloned().collect(), limits))
                    .collect::<Result<_>>()?,
            ),
        ),
        ("resources", ctx.usage().to_data()),
        (
            "execution",
            obj([
                ("data_completeness", text(ctx.data_completeness().as_str())),
                ("invocation_counter", number(&ctx.invocation_counter)),
                (
                    "foreach_depth",
                    ValueData::Number(crate::data::integer(ctx.foreach_depth)),
                ),
                ("current_node_id", opt(ctx.current_node_id.as_deref())),
            ]),
        ),
        (
            "approvals",
            ValueData::Object(
                ctx.approvals
                    .records
                    .iter()
                    .map(|(k, v)| (k.clone(), text(v.as_str())))
                    .collect(),
            ),
        ),
        ("writes", ValueData::Array(ctx.writes.entries.clone())),
        (
            "idempotency",
            obj([
                (
                    "keys_by_write_node",
                    string_map(&ctx.idempotency.keys_by_write_node),
                ),
                (
                    "payload_hashes_by_write_node",
                    string_map(&ctx.idempotency.payload_hashes_by_write_node),
                ),
            ]),
        ),
        (
            "checkpoint",
            obj([
                ("checkpoint_version", number(version)),
                (
                    "last_completed_node_id",
                    opt(checkpoint.last_completed_node_id.as_deref()),
                ),
                (
                    "suspended_at_node_id",
                    opt(checkpoint.suspended_at_node_id.as_deref()),
                ),
                ("trace_sequence", number(&checkpoint.trace_sequence)),
                (
                    "approval_request_published",
                    ValueData::Bool(checkpoint.approval_request_published),
                ),
                (
                    "write_request_durable",
                    ValueData::Bool(checkpoint.write_request_durable),
                ),
                (
                    "approval_request_ids_by_write_node",
                    string_map(&checkpoint.approval_request_ids_by_write_node),
                ),
                (
                    "write_intent_hashes_by_write_node",
                    string_map(&checkpoint.write_intent_hashes_by_write_node),
                ),
                (
                    "payload_hashes_by_write_node",
                    string_map(&checkpoint.payload_hashes_by_write_node),
                ),
            ]),
        ),
    ]))
}
fn array<'a>(v: &'a ValueData, path: &str) -> Result<&'a [ValueData]> {
    if let ValueData::Array(v) = v {
        Ok(v)
    } else {
        Err(error(path, "expected an array"))
    }
}
fn boolean(v: &ValueData, path: &str) -> Result<bool> {
    if let ValueData::Bool(v) = v {
        Ok(*v)
    } else {
        Err(typed(path, "expected a boolean"))
    }
}
fn enum_value<T: std::str::FromStr>(v: &ValueData, path: &str) -> Result<T> {
    required(v, path)?
        .parse()
        .map_err(|_| error(path, "invalid value"))
}
fn refs(v: &ValueData, path: &str) -> Result<Vec<String>> {
    let ValueData::Array(a) = v else {
        return Err(typed(path, "expected an array"));
    };
    a.iter()
        .map(|v| match v {
            ValueData::String(s) if !s.is_empty() => Ok(s.clone()),
            _ => Err(error(path, "expected non-empty strings")),
        })
        .collect()
}
fn envelope(v: &ValueData, path: &str, limits: CheckpointLimits) -> Result<ValueEnvelope> {
    let e = exact(
        v,
        &[
            "value",
            "type",
            "presence",
            "completeness",
            "classification",
            "provenance_refs",
        ],
        path,
    )?;
    let provenance = refs(&e["provenance_refs"], &format!("{path}.provenance_refs"))?;
    let invalid = || error(path, "invalid ValueEnvelope");
    let value = decode_value(&e["value"], &format!("{path}.value"), limits).map_err(|e| {
        if matches!(e.kind, CheckpointErrorKind::Invalid) {
            invalid()
        } else {
            e
        }
    })?;
    let ty = TypeRef::from_data(&e["type"]).map_err(|_| invalid())?;
    if !ty.accepts(&value) {
        return Err(typed(path, "value does not conform to declared type"));
    }
    let presence: Presence = enum_value(&e["presence"], path).map_err(|_| invalid())?;
    let complete: Completeness = enum_value(&e["completeness"], path).map_err(|_| invalid())?;
    let classification: DataClassification =
        enum_value(&e["classification"], path).map_err(|_| invalid())?;
    Ok(ValueEnvelope::new(
        value,
        ty,
        presence,
        complete,
        classification,
        provenance,
    ))
}
fn namespace(v: &ValueData, path: &str, limits: CheckpointLimits) -> Result<Namespace> {
    object(v, path)?
        .iter()
        .map(|(k, v)| {
            required(&text(k), &format!("{path}.key"))?;
            Ok((k.clone(), envelope(v, &format!("{path}.{k}"), limits)?))
        })
        .collect()
}
fn decode_string_map(v: &ValueData, path: &str) -> Result<BTreeMap<String, String>> {
    object(v, path)?
        .iter()
        .map(|(k, v)| {
            required(&text(k), &format!("{path}.key"))?;
            Ok((k.clone(), required(v, &format!("{path}.{k}"))?.into()))
        })
        .collect()
}
fn check(v: &ValueData, path: &str) -> Result<CheckResult> {
    let c = exact(
        v,
        &[
            "check_id",
            "status",
            "severity",
            "message",
            "condition_node_id",
            "presence",
            "completeness",
            "provenance_refs",
            "reason_code",
        ],
        path,
    )?;
    let invalid = || error(path, "invalid CheckResult");
    let ValueData::String(message) = &c["message"] else {
        return Err(typed(&format!("{path}.message"), "expected a string"));
    };
    Ok(CheckResult {
        check_id: required(&c["check_id"], path)?.into(),
        status: enum_value::<CheckStatus>(&c["status"], path).map_err(|_| invalid())?,
        severity: required(&c["severity"], path)?.into(),
        message: message.clone(),
        condition_node_id: required(&c["condition_node_id"], path)?.into(),
        presence: enum_value(&c["presence"], path).map_err(|_| invalid())?,
        completeness: enum_value(&c["completeness"], path).map_err(|_| invalid())?,
        provenance_refs: refs(&c["provenance_refs"], path).map_err(|e| {
            if e.kind == CheckpointErrorKind::Type {
                e
            } else {
                invalid()
            }
        })?,
        reason_code: optional(&c["reason_code"], path)?,
    })
}
pub(super) fn decode(
    v: &ValueData,
    cp: &ExecutionCheckpoint,
    request: &ExecutionRequest,
    limits: CheckpointLimits,
) -> Result<RestoredState> {
    let s = exact(
        v,
        &[
            "schema_version",
            "checkpoint_version",
            "security",
            "symbols",
            "checks",
            "outputs",
            "resources",
            "execution",
            "approvals",
            "writes",
            "idempotency",
            "checkpoint",
        ],
        "$",
    )?;
    if integer(&s["schema_version"], "$.schema_version", false)? != Integer::from(1) {
        return Err(error("$.schema_version", "unsupported state schema"));
    }
    let version = integer(&s["checkpoint_version"], "$.checkpoint_version", true)?;
    if version != cp.metadata().checkpoint_version {
        return Err(error("$.checkpoint_version", "checkpoint binding mismatch"));
    }
    if s["security"] != security(request.security_context()) {
        return Err(CheckpointError {
            kind: CheckpointErrorKind::Binding,
            ..error("$.security", "security context binding mismatch")
        });
    }
    credential(v, "$")?;
    let sym = exact(
        &s["symbols"],
        &["inputs", "contexts", "frames", "check_frames"],
        "$.symbols",
    )?;
    let frames = array(&sym["frames"], "$.symbols.frames")?;
    if frames.is_empty() {
        return Err(error("$.symbols.frames", "expected a non-empty array"));
    }
    let checks_frames = array(&sym["check_frames"], "$.symbols.check_frames")?;
    if frames.len() != checks_frames.len() {
        return Err(error("$.symbols.check_frames", "frame depth mismatch"));
    }
    let inputs = namespace(&sym["inputs"], "$.symbols.inputs", limits)?;
    let contexts = namespace(&sym["contexts"], "$.symbols.contexts", limits)?;
    let frames = frames
        .iter()
        .zip(checks_frames)
        .enumerate()
        .map(|(i, (v, c))| {
            Ok((
                namespace(v, &format!("$.symbols.frames[{i}]"), limits)?,
                namespace(c, &format!("$.symbols.check_frames[{i}]"), limits)?,
            ))
        })
        .collect::<Result<_>>()?;
    let checks = array(&s["checks"], "$.checks")?
        .iter()
        .enumerate()
        .map(|(i, v)| check(v, &format!("$.checks[{i}]")))
        .collect::<Result<_>>()?;
    let outputs = array(&s["outputs"], "$.outputs")?
        .iter()
        .enumerate()
        .map(|(i, v)| {
            EmitRecord::new(
                namespace(v, &format!("$.outputs[{i}]"), limits)?
                    .into_iter()
                    .collect(),
            )
            .map_err(|_| error("$.outputs", "invalid emit record"))
        })
        .collect::<Result<_>>()?;
    let r = exact(
        &s["resources"],
        &[
            "tool_calls",
            "loop_iterations",
            "emitted_rows",
            "max_collection_size_seen",
        ],
        "$.resources",
    )?;
    let resources = ResourceUsage {
        tool_calls: integer(&r["tool_calls"], "$.resources.tool_calls", false)?,
        loop_iterations: integer(&r["loop_iterations"], "$.resources.loop_iterations", false)?,
        emitted_rows: integer(&r["emitted_rows"], "$.resources.emitted_rows", false)?,
        max_collection_size_seen: integer(
            &r["max_collection_size_seen"],
            "$.resources.max_collection_size_seen",
            false,
        )?,
    };
    let e = exact(
        &s["execution"],
        &[
            "data_completeness",
            "invocation_counter",
            "foreach_depth",
            "current_node_id",
        ],
        "$.execution",
    )?;
    let completeness = enum_value(&e["data_completeness"], "$.execution.data_completeness")?;
    let invocation_counter = integer(
        &e["invocation_counter"],
        "$.execution.invocation_counter",
        false,
    )?;
    let depth = integer(&e["foreach_depth"], "$.execution.foreach_depth", false)?;
    let foreach_depth = depth
        .as_str()
        .parse::<usize>()
        .map_err(|_| typed("$.execution.foreach_depth", "FOREACH depth exceeds 16"))?;
    if foreach_depth > MAX_FOREACH_NESTING_DEPTH {
        return Err(typed(
            "$.execution.foreach_depth",
            "FOREACH depth exceeds 16",
        ));
    }
    let current_node = optional(&e["current_node_id"], "$.execution.current_node_id")?;
    let approvals = object(&s["approvals"], "$.approvals")?
        .iter()
        .map(|(k, v)| {
            required(&text(k), "$.approvals.key")?;
            enum_value::<ApprovalStatus>(v, "$.approvals")
                .map(|v| (k.clone(), v))
                .map_err(|_| error("$.approvals", "invalid approval status"))
        })
        .collect::<Result<_>>()?;
    let decoded = decode_value(&s["writes"], "$.writes", limits)?;
    let Value::List(writes) = decoded else {
        return Err(error("$.writes", "expected an array"));
    };
    let writes = writes
        .iter()
        .map(|v| {
            v.to_data(limits.max_decimal_bytes)
                .map_err(|_| error("$.writes", "invalid encoded value"))
        })
        .collect::<Result<_>>()?;
    let id = exact(
        &s["idempotency"],
        &["keys_by_write_node", "payload_hashes_by_write_node"],
        "$.idempotency",
    )?;
    let idempotency = IdempotencyState {
        keys_by_write_node: decode_string_map(
            &id["keys_by_write_node"],
            "$.idempotency.keys_by_write_node",
        )?,
        payload_hashes_by_write_node: decode_string_map(
            &id["payload_hashes_by_write_node"],
            "$.idempotency.payload_hashes_by_write_node",
        )?,
    };
    let c = exact(
        &s["checkpoint"],
        &[
            "checkpoint_version",
            "last_completed_node_id",
            "suspended_at_node_id",
            "trace_sequence",
            "approval_request_published",
            "write_request_durable",
            "approval_request_ids_by_write_node",
            "write_intent_hashes_by_write_node",
            "payload_hashes_by_write_node",
        ],
        "$.checkpoint",
    )?;
    let restored = integer(
        &c["checkpoint_version"],
        "$.checkpoint.checkpoint_version",
        true,
    )?;
    if restored != version {
        return Err(error(
            "$.checkpoint.checkpoint_version",
            "checkpoint binding mismatch",
        ));
    }
    let checkpoint = CheckpointState {
        checkpoint_version: restored,
        last_completed_node_id: optional(
            &c["last_completed_node_id"],
            "$.checkpoint.last_completed_node_id",
        )?,
        suspended_at_node_id: optional(
            &c["suspended_at_node_id"],
            "$.checkpoint.suspended_at_node_id",
        )?,
        trace_sequence: integer(&c["trace_sequence"], "$.checkpoint.trace_sequence", false)?,
        approval_request_published: boolean(
            &c["approval_request_published"],
            "$.checkpoint.approval_request_published",
        )?,
        write_request_durable: boolean(
            &c["write_request_durable"],
            "$.checkpoint.write_request_durable",
        )?,
        approval_request_ids_by_write_node: decode_string_map(
            &c["approval_request_ids_by_write_node"],
            "$.checkpoint.approval_request_ids_by_write_node",
        )?,
        write_intent_hashes_by_write_node: decode_string_map(
            &c["write_intent_hashes_by_write_node"],
            "$.checkpoint.write_intent_hashes_by_write_node",
        )?,
        payload_hashes_by_write_node: decode_string_map(
            &c["payload_hashes_by_write_node"],
            "$.checkpoint.payload_hashes_by_write_node",
        )?,
    };
    Ok(RestoredState {
        inputs,
        contexts,
        frames,
        checks,
        outputs,
        resources,
        completeness,
        invocation_counter,
        foreach_depth,
        current_node,
        approvals: ApprovalState { records: approvals },
        writes: WriteJournalState { entries: writes },
        idempotency,
        checkpoint,
    })
}
fn decode_value(v: &ValueData, path: &str, limits: CheckpointLimits) -> Result<Value> {
    Ok(match v {
        ValueData::Null => Value::Null,
        ValueData::Bool(b) => Value::Bool(*b),
        ValueData::Number(n) => Value::Int(n.clone()),
        ValueData::String(s) => Value::String(s.clone()),
        ValueData::Array(v) => Value::List(
            v.iter()
                .enumerate()
                .map(|(i, v)| decode_value(v, &format!("{path}[{i}]"), limits))
                .collect::<Result<_>>()?,
        ),
        ValueData::Object(o) => {
            let tag = o.get("$type");
            let encoded = |field: &str| -> Result<&str> {
                if let Some(ValueData::String(s)) = o.get(field) {
                    Ok(s)
                } else {
                    Err(error(path, "invalid encoded value"))
                }
            };
            let decimal = |field: &str| -> Result<nsl_core::NslDecimal> {
                let s = encoded(field)?;
                let n = s
                    .parse::<nsl_core::NslDecimal>()
                    .map_err(|_| typed(path, "expected a finite Decimal"))?;
                n.to_fixed_point(limits.max_decimal_bytes)
                    .map_err(|_| CheckpointError {
                        kind: CheckpointErrorKind::Encoding,
                        ..error(path, "Decimal encoding limit exceeded")
                    })?;
                Ok(n)
            };
            match tag {
                Some(ValueData::String(t)) if t == "Decimal" => Value::Decimal(decimal("value")?),
                Some(ValueData::String(t)) if t == "Money" => Value::Money(Money::new(
                    decimal("amount")?,
                    encoded("currency")?
                        .parse()
                        .map_err(|_| error(path, "invalid encoded value"))?,
                )),
                Some(ValueData::String(t)) if t == "Date" => Value::Date(
                    temporal(encoded("value")?, false)
                        .ok_or_else(|| error(path, "invalid encoded value"))?,
                ),
                Some(ValueData::String(t)) if t == "DateTime" => Value::DateTime(
                    temporal(encoded("value")?, true)
                        .ok_or_else(|| error(path, "invalid encoded value"))?,
                ),
                _ => Value::Record(
                    o.iter()
                        .map(|(k, v)| {
                            Ok((k.clone(), decode_value(v, &format!("{path}.{k}"), limits)?))
                        })
                        .collect::<Result<_>>()?,
                ),
            }
        }
    })
}
fn temporal(s: &str, datetime: bool) -> Option<String> {
    if datetime {
        nsl_core::normalize_datetime(s)
    } else {
        nsl_core::normalize_date(s)
    }
}
