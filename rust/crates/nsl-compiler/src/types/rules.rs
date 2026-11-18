use nsl_core::{TypeKind, TypeRef};
use nsl_source::SourceSpan;
use nsl_source::ast::BinaryOperator;

use super::{FieldResolution, StaticTypeChecker, TypeCheckError, TypeDiagnosticKind::*};

pub(super) fn primitive(name: &str) -> TypeRef {
    TypeRef::new(TypeKind::Primitive(name.into())).expect("built-in primitive name")
}

pub(super) fn list(item: TypeRef) -> TypeRef {
    TypeRef::new(TypeKind::List(Box::new(item))).expect("list type")
}

impl StaticTypeChecker<'_> {
    pub fn require_bool(&self, actual: &TypeRef, span: &SourceSpan) -> Result<(), TypeCheckError> {
        if actual == &primitive("Bool") {
            return Ok(());
        }
        Err(self.error(CheckCondition, "CHECK assert must have Bool type", span))
    }

    pub fn require_list(
        &self,
        actual: &TypeRef,
        span: &SourceSpan,
    ) -> Result<TypeRef, TypeCheckError> {
        if let TypeKind::List(item) = actual.kind() {
            return Ok(item.as_ref().clone());
        }
        Err(self.error(
            ForeachCollection,
            "foreach collection must be List<T>",
            span,
        ))
    }

    pub fn field_result(
        &self,
        source: &TypeRef,
        field: &str,
        span: &SourceSpan,
    ) -> Result<FieldResolution, TypeCheckError> {
        let result = match source.kind() {
            TypeKind::List(item) => item.field(field).map(|ty| FieldResolution {
                type_info: list(ty.clone()),
                projected: true,
            }),
            TypeKind::Record { .. } => source.field(field).map(|ty| FieldResolution {
                type_info: ty.clone(),
                projected: false,
            }),
            _ => None,
        };
        result.ok_or_else(|| {
            self.error(
                UnknownField,
                format!("type {} has no field {field}", kind_name(source)),
                span,
            )
        })
    }

    pub fn builtin_result(
        &self,
        name: &str,
        arguments: &[TypeRef],
        span: &SourceSpan,
    ) -> Result<TypeRef, TypeCheckError> {
        if !matches!(name, "count" | "min" | "max" | "sum") {
            return Err(self.error(
                UnsupportedBuiltin,
                format!("unsupported built-in call: {name}"),
                span,
            ));
        }
        let code = if name == "sum" {
            SumArgument
        } else {
            UnsupportedBuiltin
        };
        let [argument] = arguments else {
            return Err(self.error(code, format!("{name} requires exactly one argument"), span));
        };
        let TypeKind::List(item) = argument.kind() else {
            return Err(self.error(code, format!("{name} requires a List argument"), span));
        };
        if name == "sum" && !numeric(item) && !matches!(item.kind(), TypeKind::Money(_)) {
            return Err(self.error(code, "sum requires List<Int|Decimal|Money>", span));
        }
        Ok(if name == "count" {
            primitive("Int")
        } else {
            item.as_ref().clone()
        })
    }

    pub fn binary_result(
        &self,
        operator: BinaryOperator,
        left: &TypeRef,
        right: &TypeRef,
        span: &SourceSpan,
    ) -> Result<TypeRef, TypeCheckError> {
        use BinaryOperator::*;
        if left != right {
            return Err(self.error(
                BinaryType,
                format!("binary type mismatch for {}", operator.as_str()),
                span,
            ));
        }
        match operator {
            Eq | Ne => return Ok(primitive("Bool")),
            Lt | Le | Gt | Ge if numeric(left) || matches!(left.kind(), TypeKind::Money(_)) => {
                return Ok(primitive("Bool"));
            }
            Add | Sub | Mul | Div => {
                if left == &primitive("Int") {
                    return Ok(primitive(if operator == Div { "Decimal" } else { "Int" }));
                }
                if left == &primitive("Decimal") {
                    return Ok(left.clone());
                }
                if matches!(left.kind(), TypeKind::Money(_)) && matches!(operator, Add | Sub) {
                    return Ok(left.clone());
                }
            }
            _ => {}
        }
        let name = match left.kind() {
            TypeKind::Primitive(name)
            | TypeKind::Domain(name)
            | TypeKind::Record { name, .. }
            | TypeKind::Enum { name, .. } => name.as_str(),
            _ => kind_name(left),
        };
        Err(self.error(
            BinaryType,
            format!("operator {} is not defined for {name}", operator.as_str()),
            span,
        ))
    }
}

fn numeric(ty: &TypeRef) -> bool {
    matches!(ty.kind(), TypeKind::Primitive(name) if matches!(name.as_str(), "Int" | "Decimal"))
}

fn kind_name(ty: &TypeRef) -> &'static str {
    match ty.kind() {
        TypeKind::Primitive(_) => "primitive",
        TypeKind::Domain(_) => "domain",
        TypeKind::List(_) => "list",
        TypeKind::Record { .. } => "record",
        TypeKind::Enum { .. } => "enum",
        TypeKind::Optional(_) => "optional",
        TypeKind::Money(_) => "money",
    }
}
