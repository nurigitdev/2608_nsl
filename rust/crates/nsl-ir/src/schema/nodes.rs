use std::collections::{BTreeMap, BTreeSet};

use nsl_core::ToolCapability;

use super::{Checked, Cursor, IrVersion, Tool};

enum Task<'a> {
    Statement(Cursor<'a>, bool),
    Expression(Cursor<'a>, Position),
}

#[derive(Clone, Copy)]
struct Position {
    order: usize,
    inside_foreach: bool,
}

struct WriteLink<'a> {
    node: Cursor<'a>,
    approval_ref: &'a str,
    tool_ref: &'a str,
    position: Position,
}

struct ApprovalLink<'a> {
    id: Cursor<'a>,
    order: usize,
    uses: usize,
}

struct Body<'a> {
    version: IrVersion,
    pending: Vec<Task<'a>>,
    order: usize,
    writes: Vec<WriteLink<'a>>,
    approvals: Vec<ApprovalLink<'a>>,
}

pub(super) fn validate_body<'a>(
    body: Cursor<'a>,
    version: IrVersion,
    profile: &str,
    tools: Vec<Tool<'a>>,
) -> Checked {
    let mut body = Body {
        version,
        pending: body
            .items()?
            .rev()
            .map(|node| Task::Statement(node, false))
            .collect(),
        order: 0,
        writes: Vec::new(),
        approvals: Vec::new(),
    };
    while let Some(task) = body.pending.pop() {
        match task {
            Task::Statement(node, inside_foreach) => body.statement(node, inside_foreach)?,
            Task::Expression(node, position) => body.expression(node, position)?,
        }
    }
    if version == IrVersion::V1_1 {
        body.validate_links(profile, tools)?;
    }
    Ok(())
}

impl<'a> Body<'a> {
    fn statement(&mut self, node: Cursor<'a>, inside_foreach: bool) -> Checked {
        let kind = node.name("kind")?;
        let fields: &[&str] = match kind {
            "let" => &["target_symbol_id", "value"],
            "foreach" => &["iterator_symbol_id", "collection", "max_iterations", "body"],
            "check" => &[
                "check_id",
                "condition",
                "severity",
                "on_fail",
                "message",
                "result_symbol_id",
                "data_policy",
            ],
            "emit" => &["fields"],
            "approval" if self.version == IrVersion::V1_1 => &["approval_id", "message", "policy"],
            _ => return Err(node.error_at("kind", "unknown statement kind for IR version")),
        };
        node.exact_with(&["node_id", "kind"], fields, &[])?;
        node.name("node_id")?;
        self.order += 1;
        let position = Position {
            order: self.order,
            inside_foreach,
        };
        match kind {
            "let" => {
                node.name("target_symbol_id")?;
                self.pending
                    .push(Task::Expression(node.field("value")?, position));
            }
            "foreach" => {
                node.name("iterator_symbol_id")?;
                node.field("max_iterations")?.integer(true)?;
                self.pending.extend(
                    node.field("body")?
                        .items()?
                        .rev()
                        .map(|node| Task::Statement(node, true)),
                );
                // The collection is evaluated before entering the loop. This is also the
                // Oracle's linkage order for a nested collection expression.
                self.pending
                    .push(Task::Expression(node.field("collection")?, position));
            }
            "check" => {
                node.names(&["check_id", "severity", "on_fail", "result_symbol_id"])?;
                node.field("message")?.text(true)?;
                let policy = node.field("data_policy")?;
                policy.exact(&["require_complete", "on_partial", "on_unknown"], &[])?;
                policy.field("require_complete")?.boolean()?;
                policy
                    .field("on_partial")?
                    .constant("UNKNOWN", "expected UNKNOWN")?;
                policy
                    .field("on_unknown")?
                    .constant("UNKNOWN", "expected UNKNOWN")?;
                self.pending
                    .push(Task::Expression(node.field("condition")?, position));
            }
            "emit" => self.named_expressions(node.field("fields")?, position, false)?,
            "approval" => {
                nonblank(&node.field("node_id")?)?;
                let id = node.field("approval_id")?;
                nonblank(&id)?;
                node.field("message")?.text(true)?;
                let policy = node.field("policy")?;
                policy.exact(&["required"], &["ttl_seconds"])?;
                let required = policy.field("required")?;
                if !required.boolean()? {
                    return Err(required.error("approval policy must be required"));
                }
                if let Some(ttl) = policy.optional("ttl_seconds")? {
                    ttl.integer(true)?;
                }
                self.approvals.push(ApprovalLink {
                    id,
                    order: position.order,
                    uses: 0,
                });
            }
            _ => unreachable!("statement kind matched its field list"),
        }
        Ok(())
    }

    fn expression(&mut self, node: Cursor<'a>, position: Position) -> Checked {
        let kind = node.name("kind")?;
        let fields: &[&str] = match kind {
            "literal" => &["value"],
            "symbol_ref" => &["symbol_id"],
            "field" | "project" => &["source", "field"],
            "call" => &["function", "arguments"],
            "binary" => &["operator", "left", "right"],
            "read" => &["tool_ref", "arguments", "result_policy"],
            "write" if self.version == IrVersion::V1_1 => {
                &["tool_ref", "approval_ref", "arguments"]
            }
            _ => return Err(node.error_at("kind", "unknown expression kind for IR version")),
        };
        node.exact_with(&["node_id", "kind", "type"], fields, &[])?;
        node.name("node_id")?;
        node.field("type")?.type_document()?;
        match kind {
            "literal" => node.field("value")?.literal()?,
            "symbol_ref" => {
                node.name("symbol_id")?;
            }
            "field" | "project" => {
                node.name("field")?;
                self.pending
                    .push(Task::Expression(node.field("source")?, position));
            }
            "call" => {
                node.name("function")?;
                self.pending.extend(
                    node.field("arguments")?
                        .items()?
                        .rev()
                        .map(|node| Task::Expression(node, position)),
                );
            }
            "binary" => {
                node.name("operator")?;
                self.pending
                    .push(Task::Expression(node.field("right")?, position));
                self.pending
                    .push(Task::Expression(node.field("left")?, position));
            }
            "read" => {
                node.name("tool_ref")?;
                self.named_expressions(node.field("arguments")?, position, false)?;
                let policy = node.field("result_policy")?;
                let fields = ["required", "accept_partial", "empty_is_valid"];
                policy.exact(&fields, &[])?;
                for field in fields {
                    policy.field(field)?.boolean()?;
                }
            }
            "write" => {
                nonblank(&node.field("node_id")?)?;
                let tool_ref = nonblank(&node.field("tool_ref")?)?;
                let approval_ref = nonblank(&node.field("approval_ref")?)?;
                self.named_expressions(node.field("arguments")?, position, true)?;
                self.writes.push(WriteLink {
                    node,
                    approval_ref,
                    tool_ref,
                    position,
                });
            }
            _ => unreachable!("expression kind matched its field list"),
        }
        Ok(())
    }

    fn named_expressions(&mut self, data: Cursor<'a>, position: Position, write: bool) -> Checked {
        let start = self.pending.len();
        let mut names = BTreeSet::new();
        for item in data.items()? {
            item.exact(&["name", "value"], &[])?;
            let name = item.field("name")?;
            if write && !names.insert(nonblank(&name)?) {
                return Err(name.error("duplicate WRITE argument name"));
            }
            name.text(false)?;
            self.pending
                .push(Task::Expression(item.field("value")?, position));
        }
        self.pending[start..].reverse();
        Ok(())
    }

    fn validate_links(mut self, profile: &str, tools: Vec<Tool<'a>>) -> Checked {
        let mut required_tools = BTreeMap::new();
        for tool in tools {
            if required_tools
                .insert(tool.reference.text(false)?, tool.capability)
                .is_some()
            {
                return Err(tool.reference.error("duplicate tool_ref"));
            }
        }
        let mut approval_indices = BTreeMap::new();
        for (index, approval) in self.approvals.iter().enumerate() {
            if approval_indices
                .insert(approval.id.text(false)?, index)
                .is_some()
            {
                return Err(approval.id.error("duplicate approval_id"));
            }
        }
        if profile == "NSL-0.2-STRICT"
            && let Some(second) = self.writes.get(1)
        {
            return Err(second.node.error("strict profile allows at most one WRITE"));
        }
        for write in self.writes {
            if write.position.inside_foreach {
                return Err(write.node.error("WRITE must not be nested inside foreach"));
            }
            let Some(&index) = approval_indices.get(write.approval_ref) else {
                return Err(write
                    .node
                    .error_at("approval_ref", "must reference a declared approval_id"));
            };
            let approval = &mut self.approvals[index];
            if approval.order >= write.position.order {
                return Err(write
                    .node
                    .error_at("approval_ref", "approval must occur before WRITE"));
            }
            match required_tools.get(write.tool_ref) {
                None => {
                    return Err(write
                        .node
                        .error_at("tool_ref", "must reference a required tool"));
                }
                Some(ToolCapability::Write) => approval.uses += 1,
                Some(_) => {
                    return Err(write
                        .node
                        .error_at("tool_ref", "must reference a WRITE capability tool"));
                }
            }
        }
        for approval in self.approvals {
            if approval.uses != 1 {
                return Err(approval
                    .id
                    .error("approval must be linked to exactly one WRITE"));
            }
        }
        Ok(())
    }
}

fn nonblank<'a>(field: &Cursor<'a>) -> Checked<&'a str> {
    let text = field.text(false)?;
    if text
        .trim_matches(|c: char| c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c))
        .is_empty()
    {
        return Err(field.error("must not be blank"));
    }
    Ok(text)
}
