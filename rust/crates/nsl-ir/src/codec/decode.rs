use std::{collections::BTreeMap, str::FromStr};

use nsl_core::{Integer, Money, PayloadHash, TypeRef, ValueData};

use crate::{
    ApprovalPolicy, ApprovalStatement, BuildMetadata, BuildSource, ContextSpec, Expression,
    ExpressionKind, InputSpec, IrVersion, LiteralValue, NamedExpression, OutputField, RequiredTool,
    RequiredToolKind, ResourceLimits, ResultPolicy, SkillObject, Statement, StatementKind,
    StaticAnalysis, SymbolSpec, WriteExpression,
};

use super::{NsoCodecError, Result};

#[derive(Clone, Copy)]
struct Field<'a> {
    value: &'a ValueData,
    name: &'static str,
}

impl<'a> Field<'a> {
    fn error(self) -> NsoCodecError {
        NsoCodecError::InvalidModelField(self.name)
    }

    fn object(self) -> Result<&'a BTreeMap<String, ValueData>> {
        match self.value {
            ValueData::Object(fields) => Ok(fields),
            _ => Err(self.error()),
        }
    }

    fn optional(self, name: &'static str) -> Result<Option<Self>> {
        Ok(self.object()?.get(name).map(|value| Self { name, value }))
    }

    fn get(self, name: &'static str) -> Result<Self> {
        self.optional(name)?
            .ok_or(NsoCodecError::InvalidModelField(name))
    }

    fn text(self) -> Result<&'a str> {
        match self.value {
            ValueData::String(value) => Ok(value),
            _ => Err(self.error()),
        }
    }

    fn string(self) -> Result<String> {
        self.text().map(str::to_owned)
    }

    fn boolean(self) -> Result<bool> {
        match self.value {
            ValueData::Bool(value) => Ok(*value),
            _ => Err(self.error()),
        }
    }

    fn integer(self) -> Result<Integer> {
        match self.value {
            ValueData::Number(value) => Ok(value.clone()),
            _ => Err(self.error()),
        }
    }

    fn parse<T: FromStr>(self) -> Result<T> {
        self.text()?.parse().map_err(|_| self.error())
    }

    fn items(self) -> Result<impl DoubleEndedIterator<Item = Self>> {
        match self.value {
            ValueData::Array(items) => Ok(items.iter().map(move |value| Self { value, ..self })),
            _ => Err(self.error()),
        }
    }

    fn list<T>(self, decode: impl FnMut(Self) -> Result<T>) -> Result<Vec<T>> {
        self.items()?.map(decode).collect()
    }

    fn ty(self) -> Result<TypeRef> {
        TypeRef::from_data(self.value).map_err(NsoCodecError::Type)
    }
}

pub(super) fn skill(document: &ValueData) -> Result<SkillObject> {
    let root = Field {
        value: document,
        name: "document",
    };
    let version = root.get("ir_version")?.parse()?;
    let language = root.get("language")?;
    let meta = root.get("skill")?;
    let hashes = root.get("hashes")?;
    let build = root.get("build")?;
    let source_bundle_sha256 = build.get("source_bundle_sha256")?.parse()?;
    if source_bundle_sha256 != hashes.get("source_bundle_sha256")?.parse::<PayloadHash>()? {
        return Err(NsoCodecError::InconsistentSourceBundle);
    }
    let limits = root.get("limits")?;
    let analysis = root.get("analysis")?;
    Ok(SkillObject {
        ir_version: version,
        language_version: language.get("version")?.string()?,
        skill_id: meta.get("id")?.string()?,
        skill_version: meta.get("version")?.string()?,
        risk: meta.get("risk")?.string()?,
        semantics_profile: root.get("semantics_profile")?.string()?,
        semantic_hash: hashes.get("semantic_sha256")?.parse()?,
        features: root
            .get("features")?
            .items()?
            .map(Field::string)
            .collect::<Result<_>>()?,
        symbols: root.get("symbols")?.list(|item| {
            Ok(SymbolSpec {
                symbol_id: item.get("symbol_id")?.string()?,
                name: item.get("name")?.string()?,
                category: item.get("category")?.string()?,
                type_info: item.get("type")?.ty()?,
                classification: item.get("classification")?.parse()?,
            })
        })?,
        required_tools: root.get("requires")?.list(|item| tool(item, version))?,
        limits: ResourceLimits {
            tool_calls: limits.get("tool_calls")?.integer()?,
            loop_iterations: limits.get("loop_iterations")?.integer()?,
            emitted_rows: limits.get("emitted_rows")?.integer()?,
            collection_size: limits.get("collection_size")?.integer()?,
            duration_ms: limits.get("duration_ms")?.integer()?,
        },
        inputs: root.get("inputs")?.list(|item| {
            Ok(InputSpec {
                symbol_id: item.get("symbol_id")?.string()?,
                name: item.get("name")?.string()?,
                type_info: item.get("type")?.ty()?,
                required: item.get("required")?.boolean()?,
                classification: item.get("classification")?.parse()?,
            })
        })?,
        contexts: root.get("contexts")?.list(|item| {
            Ok(ContextSpec {
                symbol_id: item.get("symbol_id")?.string()?,
                name: item.get("name")?.string()?,
                type_info: item.get("type")?.ty()?,
                path: item.get("path")?.list(Field::string)?,
                classification: item.get("classification")?.parse()?,
            })
        })?,
        outputs: root.get("output")?.list(|item| {
            Ok(OutputField {
                name: item.get("name")?.string()?,
                type_info: item.get("type")?.ty()?,
                classification: item.get("classification")?.parse()?,
            })
        })?,
        body: root.get("body")?.list(statement)?,
        analysis: StaticAnalysis {
            max_tool_calls: analysis.get("max_tool_calls")?.integer()?,
            max_loop_iterations: analysis.get("max_loop_iterations")?.integer()?,
            max_emit_records: analysis.get("max_emit_records")?.integer()?,
            bounded: analysis.get("bounded")?.boolean()?,
        },
        build: BuildMetadata {
            source_bundle_sha256,
            root_source: build.get("root_source")?.string()?,
            sources: build.get("sources")?.list(|item| {
                Ok(BuildSource {
                    logical_path: item.get("logical_path")?.string()?,
                    content_hash: item.get("sha256")?.parse()?,
                    size_bytes: item.get("size_bytes")?.integer()?,
                    is_root: item.get("is_root")?.boolean()?,
                })
            })?,
        },
    })
}

fn tool(node: Field<'_>, version: IrVersion) -> Result<RequiredTool> {
    let kind = match version {
        IrVersion::V1_0 => RequiredToolKind::ReadV1_0,
        IrVersion::V1_1 => RequiredToolKind::V1_1 {
            capability: node.get("capability")?.parse()?,
            risk_level: node.get("risk_level")?.parse()?,
        },
    };
    Ok(RequiredTool {
        tool_ref: node.get("tool_ref")?.string()?,
        tool_id: node.get("tool_id")?.string()?,
        version: node.get("version")?.string()?,
        kind,
        contract_hash: node.get("contract_hash")?.parse()?,
        required_scope: node.get("required_scope")?.string()?,
        output_classification: node.get("output_classification")?.parse()?,
    })
}

fn literal(node: Field<'_>) -> Result<LiteralValue> {
    match node.value {
        ValueData::Bool(value) => Ok(LiteralValue::Bool(*value)),
        ValueData::Number(value) => Ok(LiteralValue::Int(value.clone())),
        ValueData::String(value) => Ok(LiteralValue::String(value.clone())),
        _ => match node.get("$type")?.text()? {
            "Decimal" => Ok(LiteralValue::Decimal(node.get("value")?.parse()?)),
            "Money" => Ok(LiteralValue::Money(Money::new(
                node.get("amount")?.parse()?,
                node.get("currency")?.parse()?,
            ))),
            _ => Err(node.error()),
        },
    }
}

fn named(node: Field<'_>) -> Result<NamedExpression> {
    Ok(NamedExpression {
        name: node.get("name")?.string()?,
        value: expression(node.get("value")?)?,
    })
}

fn expression(node: Field<'_>) -> Result<Expression> {
    let kind_field = node.get("kind")?;
    let kind = match kind_field.text()? {
        "literal" => ExpressionKind::Literal(literal(node.get("value")?)?),
        "symbol_ref" => ExpressionKind::SymbolRef {
            symbol_id: node.get("symbol_id")?.string()?,
        },
        "field" => ExpressionKind::Field {
            source: Box::new(expression(node.get("source")?)?),
            field: node.get("field")?.string()?,
        },
        "project" => ExpressionKind::Projection {
            source: Box::new(expression(node.get("source")?)?),
            field: node.get("field")?.string()?,
        },
        "call" => ExpressionKind::Call {
            function: node.get("function")?.string()?,
            arguments: node.get("arguments")?.list(expression)?,
        },
        "binary" => ExpressionKind::Binary {
            operator: node.get("operator")?.string()?,
            left: Box::new(expression(node.get("left")?)?),
            right: Box::new(expression(node.get("right")?)?),
        },
        "read" => {
            let policy = node.get("result_policy")?;
            ExpressionKind::Read {
                tool_ref: node.get("tool_ref")?.string()?,
                arguments: node.get("arguments")?.list(named)?,
                result_policy: ResultPolicy {
                    required: policy.get("required")?.boolean()?,
                    accept_partial: policy.get("accept_partial")?.boolean()?,
                    empty_is_valid: policy.get("empty_is_valid")?.boolean()?,
                },
            }
        }
        "write" => ExpressionKind::Write(WriteExpression {
            tool_ref: node.get("tool_ref")?.string()?,
            arguments: node.get("arguments")?.list(named)?,
            approval_ref: node.get("approval_ref")?.string()?,
        }),
        _ => return Err(kind_field.error()),
    };
    Ok(Expression {
        node_id: node.get("node_id")?.string()?,
        type_info: node.get("type")?.ty()?,
        kind,
    })
}

fn statement(node: Field<'_>) -> Result<Statement> {
    let kind_field = node.get("kind")?;
    let kind = match kind_field.text()? {
        "let" => StatementKind::Let {
            target_symbol_id: node.get("target_symbol_id")?.string()?,
            value: expression(node.get("value")?)?,
        },
        "foreach" => StatementKind::Foreach {
            iterator_symbol_id: node.get("iterator_symbol_id")?.string()?,
            collection: expression(node.get("collection")?)?,
            max_iterations: node.get("max_iterations")?.integer()?,
            body: node.get("body")?.list(statement)?,
        },
        "check" => StatementKind::Check {
            check_id: node.get("check_id")?.string()?,
            condition: expression(node.get("condition")?)?,
            severity: node.get("severity")?.string()?,
            on_fail: node.get("on_fail")?.string()?,
            message: node.get("message")?.string()?,
            result_symbol_id: node.get("result_symbol_id")?.string()?,
        },
        "emit" => StatementKind::Emit {
            fields: node.get("fields")?.list(named)?,
        },
        "approval" => StatementKind::Approval(ApprovalStatement {
            approval_id: node.get("approval_id")?.string()?,
            message: node.get("message")?.string()?,
            policy: ApprovalPolicy {
                ttl_seconds: node
                    .get("policy")?
                    .optional("ttl_seconds")?
                    .map(Field::integer)
                    .transpose()?,
            },
        }),
        _ => return Err(kind_field.error()),
    };
    Ok(Statement {
        node_id: node.get("node_id")?.string()?,
        kind,
    })
}
