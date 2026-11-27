use crate::{
    CheckResult, EmitRecord, ExecutionIdentity, ExecutionRequest, ExecutionSecurityContext,
    ResourceGuard, ResourceUsage, RuntimeClock, SystemRuntimeClock, ValidatedWriteIdentities,
};
use nsl_core::{
    ApprovalStatus, Completeness, ExecutionStatus, IdentityError, Integer, ValueData, ValueEnvelope,
};
use nsl_ir::SkillObject;
use std::{collections::BTreeMap, fmt, sync::Arc};

pub const MAX_FOREACH_NESTING_DEPTH: usize = 16;
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ApprovalState {
    pub records: BTreeMap<String, ApprovalStatus>,
}
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct WriteJournalState {
    pub entries: Vec<ValueData>,
}
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct IdempotencyState {
    pub keys_by_write_node: BTreeMap<String, String>,
    pub payload_hashes_by_write_node: BTreeMap<String, String>,
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum WriteIdentityError {
    MissingKey,
    MissingPayload,
    Invalid(IdentityError),
}
impl fmt::Display for WriteIdentityError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::MissingKey => f.write_str("missing stored idempotency key"),
            Self::MissingPayload => f.write_str("missing stored payload hash"),
            Self::Invalid(e) => e.fmt(f),
        }
    }
}
impl std::error::Error for WriteIdentityError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Invalid(e) => Some(e),
            _ => None,
        }
    }
}
impl IdempotencyState {
    pub fn validate_for_write(
        &self,
        node: &str,
    ) -> Result<ValidatedWriteIdentities, WriteIdentityError> {
        let key = self
            .keys_by_write_node
            .get(node)
            .ok_or(WriteIdentityError::MissingKey)?;
        let payload = self
            .payload_hashes_by_write_node
            .get(node)
            .ok_or(WriteIdentityError::MissingPayload)?;
        ValidatedWriteIdentities::parse(key, payload).map_err(WriteIdentityError::Invalid)
    }
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CheckpointState {
    pub checkpoint_version: Integer,
    pub last_completed_node_id: Option<String>,
    pub suspended_at_node_id: Option<String>,
    pub trace_sequence: Integer,
    pub approval_request_published: bool,
    pub write_request_durable: bool,
    pub approval_request_ids_by_write_node: BTreeMap<String, String>,
    pub write_intent_hashes_by_write_node: BTreeMap<String, String>,
    pub payload_hashes_by_write_node: BTreeMap<String, String>,
}
impl Default for CheckpointState {
    fn default() -> Self {
        Self {
            checkpoint_version: 0.into(),
            last_completed_node_id: None,
            suspended_at_node_id: None,
            trace_sequence: 0.into(),
            approval_request_published: false,
            write_request_durable: false,
            approval_request_ids_by_write_node: BTreeMap::new(),
            write_intent_hashes_by_write_node: BTreeMap::new(),
            payload_hashes_by_write_node: BTreeMap::new(),
        }
    }
}
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum BindingError {
    Immutable(String),
    Unknown(String),
    RootFrame,
}
impl BindingError {
    pub fn detail_code(&self) -> Option<&'static str> {
        if matches!(self, Self::Immutable(_)) {
            Some("IMMUTABLE_BINDING_ERROR")
        } else {
            None
        }
    }
}
impl fmt::Display for BindingError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Immutable(s) => write!(f, "immutable symbol already bound: {s}"),
            Self::Unknown(s) => write!(f, "unknown symbol: {s}"),
            Self::RootFrame => f.write_str("cannot pop the root execution frame"),
        }
    }
}
impl std::error::Error for BindingError {}
#[derive(Default)]
struct Frame {
    values: BTreeMap<String, ValueEnvelope>,
    checks: BTreeMap<String, ValueEnvelope>,
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum BindingNamespace {
    Input,
    Context,
    Variable,
    Check,
}

pub struct ExecutionContext<C: RuntimeClock = SystemRuntimeClock> {
    skill: Arc<SkillObject>,
    request: ExecutionRequest,
    input_values: BTreeMap<String, ValueEnvelope>,
    context_values: BTreeMap<String, ValueEnvelope>,
    frames: Vec<Frame>,
    resource_guard: ResourceGuard<C>,
    data_completeness: Completeness,
    pub checks: Vec<CheckResult>,
    pub outputs: Vec<EmitRecord>,
    pub invocation_counter: Integer,
    pub foreach_depth: usize,
    pub current_node_id: Option<String>,
    pub approvals: ApprovalState,
    pub writes: WriteJournalState,
    pub idempotency: IdempotencyState,
    pub checkpoint: CheckpointState,
}
impl ExecutionContext<SystemRuntimeClock> {
    pub fn new(skill: Arc<SkillObject>, request: ExecutionRequest) -> Self {
        Self::with_clock(skill, request, SystemRuntimeClock::default())
    }
}
impl<C: RuntimeClock> ExecutionContext<C> {
    pub fn with_clock(skill: Arc<SkillObject>, request: ExecutionRequest, clock: C) -> Self {
        let resource_guard = ResourceGuard::new(skill.limits.clone(), clock);
        Self {
            skill,
            request,
            input_values: BTreeMap::new(),
            context_values: BTreeMap::new(),
            frames: vec![Frame::default()],
            resource_guard,
            data_completeness: Completeness::Complete,
            checks: Vec::new(),
            outputs: Vec::new(),
            invocation_counter: 0.into(),
            foreach_depth: 0,
            current_node_id: None,
            approvals: ApprovalState::default(),
            writes: WriteJournalState::default(),
            idempotency: IdempotencyState::default(),
            checkpoint: CheckpointState::default(),
        }
    }
    pub(crate) fn skill_arc(&self) -> Arc<SkillObject> {
        self.skill.clone()
    }
    pub fn skill(&self) -> &SkillObject {
        &self.skill
    }
    pub fn request(&self) -> &ExecutionRequest {
        &self.request
    }
    pub fn identity(&self) -> &ExecutionIdentity {
        self.request.identity()
    }
    pub fn security_context(&self) -> &ExecutionSecurityContext {
        self.request.security_context()
    }
    pub fn tenant_id(&self) -> &str {
        self.security_context().tenant_id()
    }
    pub fn input_values(&self) -> &BTreeMap<String, ValueEnvelope> {
        &self.input_values
    }
    pub fn context_values(&self) -> &BTreeMap<String, ValueEnvelope> {
        &self.context_values
    }
    pub(crate) fn checkpoint_frames(
        &self,
    ) -> impl Iterator<
        Item = (
            &BTreeMap<String, ValueEnvelope>,
            &BTreeMap<String, ValueEnvelope>,
        ),
    > {
        self.frames.iter().map(|f| (&f.values, &f.checks))
    }
    pub(crate) fn install_checkpoint_state(&mut self, s: crate::checkpoint::state::RestoredState) {
        self.input_values = s.inputs;
        self.context_values = s.contexts;
        self.frames = s
            .frames
            .into_iter()
            .map(|(values, checks)| Frame { values, checks })
            .collect();
        self.checks = s.checks;
        self.outputs = s.outputs;
        self.resource_guard.install_checkpoint_usage(s.resources);
        self.data_completeness = s.completeness;
        self.invocation_counter = s.invocation_counter;
        self.foreach_depth = s.foreach_depth;
        self.current_node_id = s.current_node;
        self.approvals = s.approvals;
        self.writes = s.writes;
        self.idempotency = s.idempotency;
        self.checkpoint = s.checkpoint;
    }
    pub(crate) fn envelopes(&self) -> impl Iterator<Item = &ValueEnvelope> {
        self.input_values
            .values()
            .chain(self.context_values.values())
            .chain(
                self.frames
                    .iter()
                    .flat_map(|f| f.values.values().chain(f.checks.values())),
            )
            .chain(
                self.outputs
                    .iter()
                    .flat_map(|r| r.fields().iter().map(|(_, value)| value)),
            )
    }
    pub fn frame_count(&self) -> usize {
        self.frames.len()
    }
    pub fn guard(&self) -> &ResourceGuard<C> {
        &self.resource_guard
    }
    pub fn guard_mut(&mut self) -> &mut ResourceGuard<C> {
        &mut self.resource_guard
    }
    pub fn usage(&self) -> ResourceUsage {
        self.resource_guard.usage()
    }
    pub fn bind(
        &mut self,
        namespace: BindingNamespace,
        symbol: String,
        value: ValueEnvelope,
    ) -> Result<(), BindingError> {
        if self.resolve(&symbol).is_ok() {
            return Err(BindingError::Immutable(symbol));
        }
        let frame = self.frames.last_mut().expect("root execution frame");
        let target = match namespace {
            BindingNamespace::Input => &mut self.input_values,
            BindingNamespace::Context => &mut self.context_values,
            BindingNamespace::Variable => &mut frame.values,
            BindingNamespace::Check => &mut frame.checks,
        };
        target.insert(symbol, value);
        Ok(())
    }
    pub fn resolve(&self, symbol: &str) -> Result<&ValueEnvelope, BindingError> {
        self.frames
            .iter()
            .rev()
            .find_map(|f| f.values.get(symbol))
            .or_else(|| self.frames.iter().rev().find_map(|f| f.checks.get(symbol)))
            .or_else(|| self.context_values.get(symbol))
            .or_else(|| self.input_values.get(symbol))
            .ok_or_else(|| BindingError::Unknown(symbol.into()))
    }
    pub fn push_frame(&mut self) {
        self.frames.push(Frame::default());
    }
    pub fn pop_frame(&mut self) -> Result<(), BindingError> {
        if self.frames.len() == 1 {
            return Err(BindingError::RootFrame);
        }
        self.frames.pop();
        Ok(())
    }
    pub fn observe_completeness(&mut self, value: Completeness) {
        if value == Completeness::Unknown
            || (value == Completeness::Partial && self.data_completeness == Completeness::Complete)
        {
            self.data_completeness = value;
        }
    }
    pub fn data_completeness(&self) -> Completeness {
        self.data_completeness
    }
    pub fn result_completeness(&self, status: ExecutionStatus) -> Completeness {
        if status == ExecutionStatus::Completed {
            self.data_completeness
        } else if !self.checks.is_empty() || !self.outputs.is_empty() {
            Completeness::Partial
        } else {
            Completeness::Unknown
        }
    }
}
