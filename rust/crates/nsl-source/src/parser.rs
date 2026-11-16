mod expression;
mod statements;

use std::{collections::BTreeMap, fmt};

use nsl_core::{DataClassification, Integer, TypeKind, TypeRef};

use crate::ast::*;
use crate::lexer::digits::{MAX_INTEGER_DIGITS, decimal_digit};
use crate::{
    LanguageHeaderScanner, Lexer, LexerError, SourceFile, SourceLanguageVersion, SourceSpan, Token,
    TokenKind, TokenValue,
};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ParseMode {
    RootSkill,
    IncludeFragment {
        language_version: SourceLanguageVersion,
    },
}

/// The number of simultaneously open List, foreach, call, or grouping constructs.
/// Zero permits flat syntax, including expressions with binary operators.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ParseLimits {
    pub max_nesting: usize,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ParseDiagnosticKind {
    ExpectedToken,
    ExpectedTokenKind,
    UnknownType,
    UnknownClassification,
    RequiredMetadata,
    InvalidLimitFields,
    NonPositiveLimit,
    NonPositiveForeachLimit,
    UnexpectedStatement,
    ExpectedExpression,
    UnexpectedFragmentDeclaration,
}

impl ParseDiagnosticKind {
    pub const fn code(self) -> &'static str {
        use ParseDiagnosticKind::*;
        match self {
            ExpectedToken => "NSL-E1101",
            ExpectedTokenKind => "NSL-E1102",
            UnknownType => "NSL-E1103",
            UnknownClassification => "NSL-E1104",
            RequiredMetadata => "NSL-E1105",
            InvalidLimitFields => "NSL-E1106",
            NonPositiveLimit => "NSL-E1107",
            NonPositiveForeachLimit => "NSL-E1108",
            UnexpectedStatement => "NSL-E1109",
            ExpectedExpression => "NSL-E1110",
            UnexpectedFragmentDeclaration => "NSL-E1111",
        }
    }

    const fn message(self) -> &'static str {
        use ParseDiagnosticKind::*;
        match self {
            ExpectedToken => "expected token",
            ExpectedTokenKind => "unexpected token kind",
            UnknownType => "unknown type or invalid currency",
            UnknownClassification => "unknown data classification",
            RequiredMetadata => "version, risk, and limits are required",
            InvalidLimitFields => "invalid limit fields",
            NonPositiveLimit => "all limits must be positive",
            NonPositiveForeachLimit => "foreach max must be positive",
            UnexpectedStatement => "unexpected statement",
            ExpectedExpression => "expected expression",
            UnexpectedFragmentDeclaration => "declaration is not allowed in an include fragment",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ParseDiagnostic {
    pub kind: ParseDiagnosticKind,
    pub span: SourceSpan,
}

impl fmt::Display for ParseDiagnostic {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let position = self.span.start();
        write!(
            f,
            "{} at {}:{}: {}",
            self.kind.code(),
            position.line(),
            position.column(),
            self.kind.message()
        )
    }
}

impl std::error::Error for ParseDiagnostic {}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum NumericErrorKind {
    NonDecimalDigit,
    TooManyIntegerDigits,
    InvalidDecimal,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct NumericError {
    pub kind: NumericErrorKind,
    pub span: SourceSpan,
}

impl fmt::Display for NumericError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self.kind {
            NumericErrorKind::NonDecimalDigit => "integer contains a non-decimal digit",
            NumericErrorKind::TooManyIntegerDigits => "integer exceeds 4300 decimal digits",
            NumericErrorKind::InvalidDecimal => "invalid decimal literal",
        })
    }
}

impl std::error::Error for NumericError {}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct NestingError {
    pub max_nesting: usize,
    pub span: SourceSpan,
}

impl fmt::Display for NestingError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "parser nesting exceeds the configured limit of {}",
            self.max_nesting
        )
    }
}

impl std::error::Error for NestingError {}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ParseError {
    Lexer(LexerError),
    Diagnostic(ParseDiagnostic),
    Numeric(NumericError),
    Nesting(NestingError),
}

impl fmt::Display for ParseError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Lexer(error) => error.fmt(f),
            Self::Diagnostic(error) => error.fmt(f),
            Self::Numeric(error) => error.fmt(f),
            Self::Nesting(error) => error.fmt(f),
        }
    }
}

impl std::error::Error for ParseError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Lexer(error) => Some(error),
            Self::Diagnostic(error) => Some(error),
            Self::Numeric(error) => Some(error),
            Self::Nesting(error) => Some(error),
        }
    }
}

pub struct Parser;

impl Parser {
    /// Parse a root with its declared version, or a fragment with the enclosing root's version.
    pub fn parse(
        source: &SourceFile,
        mode: ParseMode,
        limits: ParseLimits,
    ) -> Result<Ast, ParseError> {
        let version = match mode {
            ParseMode::RootSkill => {
                LanguageHeaderScanner::scan(source)
                    .map_err(|error| ParseError::Lexer(LexerError::Header(error)))?
                    .version
            }
            ParseMode::IncludeFragment { language_version } => language_version,
        };
        let tokens = Lexer::tokenize(source, version).map_err(ParseError::Lexer)?;
        let cursor = Cursor {
            tokens: &tokens,
            index: 0,
            version,
            limits,
            statements: Vec::new(),
            expressions: Vec::new(),
        };
        match mode {
            ParseMode::RootSkill => Ok(Ast::RootSkill(Box::new(cursor.skill()?))),
            ParseMode::IncludeFragment { .. } => Ok(Ast::IncludeFragment(cursor.fragment()?)),
        }
    }
}

struct Cursor<'tokens, 'src> {
    tokens: &'tokens [Token<'src>],
    index: usize,
    version: SourceLanguageVersion,
    limits: ParseLimits,
    statements: Vec<AstStatement>,
    expressions: Vec<AstExpression>,
}

use ParseDiagnosticKind::*;
use TokenKind::{Duration, Identifier, String as StringToken};

fn token_text<'a>(token: &'a Token<'_>) -> Option<&'a str> {
    match &token.value {
        TokenValue::Text(text) => Some(text),
        TokenValue::Boolean(_) | TokenValue::DurationMilliseconds(_) => None,
    }
}

fn span(start: &SourceSpan, end: &SourceSpan) -> SourceSpan {
    SourceSpan {
        source_id: start.source_id.clone(),
        start: start.start,
        end: end.end,
    }
}

fn diagnostic(kind: ParseDiagnosticKind, token: &Token<'_>) -> ParseError {
    ParseError::Diagnostic(ParseDiagnostic {
        kind,
        span: token.span.clone(),
    })
}

fn numeric_error(kind: NumericErrorKind, token: &Token<'_>) -> ParseError {
    ParseError::Numeric(NumericError {
        kind,
        span: token.span.clone(),
    })
}

fn integer(token: &Token<'_>) -> Result<Integer, ParseError> {
    let mut digits = String::new();
    for character in token.lexeme.chars() {
        let digit = decimal_digit(character)
            .ok_or_else(|| numeric_error(NumericErrorKind::NonDecimalDigit, token))?;
        if digits.len() <= MAX_INTEGER_DIGITS {
            digits.push(char::from(b'0' + digit));
        }
    }
    if digits.len() > MAX_INTEGER_DIGITS {
        return Err(numeric_error(NumericErrorKind::TooManyIntegerDigits, token));
    }
    let significant = digits.trim_start_matches('0');
    // The Lexer emits only nonempty digit runs as INTEGER.
    Ok(if significant.is_empty() {
        "0"
    } else {
        significant
    }
    .parse()
    .expect("canonicalized integer digits"))
}

impl<'tokens, 'src> Cursor<'tokens, 'src> {
    fn current(&self) -> &'tokens Token<'src> {
        &self.tokens[self.index]
    }

    fn previous(&self) -> &'tokens Token<'src> {
        &self.tokens[self.index - 1]
    }

    fn advance(&mut self) -> &'tokens Token<'src> {
        let token = self.current();
        self.index += 1;
        token
    }

    fn at(&self, value: &str) -> bool {
        token_text(self.current()) == Some(value)
    }

    fn accept(&mut self, value: &str) -> bool {
        if self.at(value) {
            self.advance();
            return true;
        }
        false
    }

    fn expect(&mut self, value: &str) -> Result<&'tokens Token<'src>, ParseError> {
        if self.at(value) {
            return Ok(self.advance());
        }
        Err(diagnostic(ExpectedToken, self.current()))
    }

    fn expect_kind(&mut self, kind: TokenKind) -> Result<&'tokens Token<'src>, ParseError> {
        if self.current().kind == kind {
            return Ok(self.advance());
        }
        Err(diagnostic(ExpectedTokenKind, self.current()))
    }

    fn text(&mut self, kind: TokenKind) -> Result<String, ParseError> {
        let token = self.expect_kind(kind)?;
        Ok(token_text(token).expect("textual token kind").to_owned())
    }

    fn controlled_keyword(&self, keyword: &str) -> bool {
        self.current().kind == TokenKind::Keyword
            && self.version == SourceLanguageVersion::V0_2
            && self.at(keyword)
    }

    fn check_nesting(&self, depth: usize, opening: &Token<'_>) -> Result<(), ParseError> {
        if depth >= self.limits.max_nesting {
            return Err(ParseError::Nesting(NestingError {
                max_nesting: self.limits.max_nesting,
                span: opening.span.clone(),
            }));
        }
        Ok(())
    }

    fn name_parts(&mut self, first: String) -> Result<Vec<String>, ParseError> {
        let mut parts = vec![first];
        while self.accept(".") {
            parts.push(self.text(Identifier)?);
        }
        Ok(parts)
    }

    fn qualified_name(&mut self) -> Result<String, ParseError> {
        let first = self.text(Identifier)?;
        Ok(self.name_parts(first)?.join("."))
    }

    fn skill(mut self) -> Result<AstSkill, ParseError> {
        let start = self.expect("language")?;
        self.expect("NSL")?;
        let language_version_span = self.expect_kind(StringToken)?.span.clone();
        self.expect(";")?;
        self.expect("skill")?;
        let skill_id = self.qualified_name()?;
        self.expect("{")?;

        let mut skill_version = String::new();
        let mut risk = None;
        let mut includes = Vec::new();
        let mut requires = Vec::new();
        let mut limits = None;
        let mut inputs = Vec::new();
        let mut contexts = Vec::new();
        let mut outputs = Vec::new();
        let mut body = Vec::new();
        while !self.at("}") {
            match token_text(self.current()) {
                Some("version") => {
                    self.advance();
                    skill_version = self.text(StringToken)?;
                    self.expect(";")?;
                }
                Some("risk") => {
                    self.advance();
                    let token = self.current();
                    risk = Some((self.text(Identifier)?, token.span.clone()));
                    self.expect(";")?;
                }
                Some("include") => includes.push(self.include()?),
                Some("requires") => requires = self.requires()?,
                Some("limits") => limits = Some(self.limit_block()?),
                Some("input") => inputs = self.fields(false)?,
                Some("context") => contexts = self.fields(true)?,
                Some("output") => outputs = self.fields(false)?,
                _ => body.push(self.statement()?),
            }
        }
        let end = self.advance();
        self.expect("<eof>")?;
        let eof = &self.tokens[self.tokens.len() - 1];
        let (risk, risk_span) = risk.ok_or_else(|| diagnostic(RequiredMetadata, eof))?;
        if skill_version.is_empty() || (limits.is_none() && includes.is_empty()) {
            return Err(diagnostic(RequiredMetadata, eof));
        }
        Ok(AstSkill {
            language_version: self.version,
            language_version_span,
            skill_id,
            skill_version,
            risk,
            risk_span,
            includes,
            requires,
            limits,
            inputs,
            contexts,
            outputs,
            body,
            span: span(&start.span, &end.span),
            statements: self.statements,
            expressions: self.expressions,
        })
    }

    fn fragment(mut self) -> Result<AstIncludeFragment, ParseError> {
        let start = self.current();
        let mut includes = Vec::new();
        let mut requires = Vec::new();
        let mut contexts = Vec::new();
        let mut limits = Vec::new();
        while self.current().kind != TokenKind::Eof {
            match token_text(self.current()) {
                Some("include") => includes.push(self.include()?),
                Some("requires") => requires.extend(self.requires()?),
                Some("context") => contexts.extend(self.fields(true)?),
                Some("limits") => limits.push(self.limit_block()?),
                _ => return Err(diagnostic(UnexpectedFragmentDeclaration, self.current())),
            }
        }
        let end = if self.index == 0 {
            start
        } else {
            self.previous()
        };
        Ok(AstIncludeFragment {
            includes,
            requires,
            contexts,
            limits,
            span: span(&start.span, &end.span),
        })
    }

    fn include(&mut self) -> Result<AstIncludeDeclaration, ParseError> {
        let start = self.advance();
        let path = self.text(StringToken)?;
        let end = self.expect(";")?;
        Ok(AstIncludeDeclaration {
            path,
            span: span(&start.span, &end.span),
        })
    }

    fn requires(&mut self) -> Result<Vec<AstRequiredTool>, ParseError> {
        self.advance();
        self.expect("{")?;
        let mut tools = Vec::new();
        while !self.accept("}") {
            let start = self.expect("tool")?;
            let tool_id = self.qualified_name()?;
            self.expect("version")?;
            let version = self.text(StringToken)?;
            let end = self.expect(";")?;
            tools.push(AstRequiredTool {
                tool_id,
                version,
                span: span(&start.span, &end.span),
            });
        }
        Ok(tools)
    }

    fn limit_block(&mut self) -> Result<AstLimits, ParseError> {
        let start = self.advance();
        self.expect("{")?;
        let mut values = BTreeMap::new();
        while !self.at("}") {
            let token = self.current();
            let name = self.text(Identifier)?;
            if values.contains_key(&name) {
                return Err(diagnostic(InvalidLimitFields, token));
            }
            let value = if name == "duration" {
                let token = self.expect_kind(Duration)?;
                let TokenValue::DurationMilliseconds(text) = &token.value else {
                    unreachable!("the lexer supplies milliseconds for duration tokens");
                };
                text.parse().expect("canonicalized lexer milliseconds")
            } else {
                integer(self.expect_kind(TokenKind::Integer)?)?
            };
            values.insert(name, value);
            self.expect(";")?;
        }
        let end = self.advance();
        let duration_ms = values
            .remove("duration")
            .unwrap_or_else(|| Integer::from(60_000));
        let (Some(tool_calls), Some(loop_iterations), Some(emitted_rows), Some(collection_size)) = (
            values.remove("tool_calls"),
            values.remove("loop_iterations"),
            values.remove("emitted_rows"),
            values.remove("collection_size"),
        ) else {
            return Err(diagnostic(InvalidLimitFields, self.current()));
        };
        if !values.is_empty() {
            return Err(diagnostic(InvalidLimitFields, self.current()));
        }
        if [
            &tool_calls,
            &loop_iterations,
            &emitted_rows,
            &collection_size,
            &duration_ms,
        ]
        .into_iter()
        .any(|value| value.as_str() == "0")
        {
            return Err(diagnostic(NonPositiveLimit, self.current()));
        }
        Ok(AstLimits {
            tool_calls,
            loop_iterations,
            emitted_rows,
            collection_size,
            duration_ms,
            span: span(&start.span, &end.span),
        })
    }

    fn fields(&mut self, with_path: bool) -> Result<Vec<AstFieldSpec>, ParseError> {
        self.advance();
        self.expect("{")?;
        let mut fields = Vec::new();
        while !self.accept("}") {
            let start = self.current();
            let name = self.text(Identifier)?;
            self.expect(":")?;
            let type_info = self.field_type()?;
            let path = if with_path {
                self.expect("from")?;
                self.text(StringToken)?
                    .split('.')
                    .map(str::to_owned)
                    .collect()
            } else {
                Vec::new()
            };
            let classification = if self.accept("classification") {
                let token = self.current();
                self.text(Identifier)?
                    .parse()
                    .map_err(|_| diagnostic(UnknownClassification, token))?
            } else {
                DataClassification::Internal
            };
            let end = self.expect(";")?;
            fields.push(AstFieldSpec {
                name,
                type_info,
                classification,
                path,
                span: span(&start.span, &end.span),
            });
        }
        Ok(fields)
    }

    fn field_type(&mut self) -> Result<AstType, ParseError> {
        let mut list_depth = 0;
        let kind = loop {
            let token = self.current();
            let name = self.text(Identifier)?;
            let kind = match name.as_str() {
                "List" => {
                    self.check_nesting(list_depth, token)?;
                    self.expect("<")?;
                    list_depth += 1;
                    continue;
                }
                "Money" => {
                    self.expect("<")?;
                    let currency_token = self.current();
                    let currency = self.text(Identifier)?;
                    self.expect(">")?;
                    TypeKind::Money(
                        currency
                            .parse()
                            .map_err(|_| diagnostic(UnknownType, currency_token))?,
                    )
                }
                "String" | "Int" | "Bool" | "Date" | "DateTime" | "Decimal" | "Year" => {
                    TypeKind::Primitive(name)
                }
                "TeamId" | "EmployeeId" | "AccountCode" | "ProjectCode" | "AccountingPeriod"
                | "OrganizationId" => TypeKind::Domain(name),
                "CheckStatus" => TypeKind::Enum {
                    name,
                    values: nsl_core::CheckStatus::ALL
                        .iter()
                        .map(|status| status.as_str().to_owned())
                        .collect(),
                },
                _ => return Err(diagnostic(UnknownType, token)),
            };
            break kind;
        };
        for _ in 0..list_depth {
            self.expect(">")?;
        }
        Ok(AstType {
            list_depth,
            item: TypeRef::new(kind).expect("built-in source type"),
        })
    }
}
