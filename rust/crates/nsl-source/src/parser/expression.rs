use super::*;

enum Frame {
    Binary {
        left: ExpressionId,
        operator: BinaryOperator,
        parent_precedence: u8,
    },
    Group {
        start: SourceSpan,
        parent_precedence: u8,
    },
    Call {
        call: Call,
        parent_precedence: u8,
    },
}

#[derive(Clone, Copy)]
enum CallKind {
    Function,
    Read,
    Write,
}

struct Call {
    kind: CallKind,
    start: SourceSpan,
    name: String,
    arguments: Vec<ExpressionId>,
    names: Vec<String>,
}

impl Call {
    fn prepare_argument(&mut self, cursor: &mut Cursor<'_, '_>) -> Result<(), ParseError> {
        let separator = match self.kind {
            CallKind::Function => return Ok(()),
            CallKind::Read => ":",
            CallKind::Write => "=",
        };
        self.names.push(cursor.text(Identifier)?);
        cursor.expect(separator)?;
        Ok(())
    }

    fn finish(self, cursor: &mut Cursor<'_, '_>) -> Result<ExpressionId, ParseError> {
        let kind = match self.kind {
            CallKind::Function => ExpressionKind::Call {
                name: self.name,
                arguments: self.arguments,
            },
            CallKind::Read => ExpressionKind::Read {
                tool_id: self.name,
                arguments: self.names.into_iter().zip(self.arguments).collect(),
            },
            CallKind::Write => {
                cursor.expect("approved_by")?;
                let approval = cursor.current();
                let approval_id = cursor.text(Identifier)?;
                ExpressionKind::Write {
                    tool_id: self.name,
                    arguments: self.names.into_iter().zip(self.arguments).collect(),
                    approval_id,
                    approval_span: approval.span.clone(),
                }
            }
        };
        let range = span(&self.start, &cursor.previous().span);
        Ok(cursor.push_expression(kind, range))
    }
}

enum Primary {
    Value(ExpressionId),
    Group(SourceSpan),
    Call(Call),
}

impl Cursor<'_, '_> {
    fn push_expression(&mut self, kind: ExpressionKind, span: SourceSpan) -> ExpressionId {
        let id = ExpressionId(self.expressions.len());
        self.expressions.push(AstExpression { kind, span });
        id
    }

    /// Precedence climbing with heap-backed continuations for nested operands.
    pub(super) fn expression(&mut self, mut depth: usize) -> Result<ExpressionId, ParseError> {
        let mut frames = Vec::new();
        let mut minimum = 0;
        'operand: loop {
            let mut value = match self.primary(depth)? {
                Primary::Value(value) => value,
                Primary::Group(start) => {
                    frames.push(Frame::Group {
                        start,
                        parent_precedence: minimum,
                    });
                    depth += 1;
                    minimum = 0;
                    continue;
                }
                Primary::Call(mut call) => {
                    if self.accept(")") {
                        call.finish(self)?
                    } else {
                        call.prepare_argument(self)?;
                        frames.push(Frame::Call {
                            call,
                            parent_precedence: minimum,
                        });
                        depth += 1;
                        minimum = 0;
                        continue;
                    }
                }
            };

            loop {
                if let Some((operator, precedence)) =
                    token_text(self.current()).and_then(binary_operator)
                    && precedence >= minimum
                {
                    self.advance();
                    frames.push(Frame::Binary {
                        left: value,
                        operator,
                        parent_precedence: minimum,
                    });
                    minimum = precedence + 1;
                    continue 'operand;
                }
                match frames.pop() {
                    Some(Frame::Binary {
                        left,
                        operator,
                        parent_precedence,
                    }) => {
                        let range = span(
                            &self.expressions[left.0].span,
                            &self.expressions[value.0].span,
                        );
                        value = self.push_expression(
                            ExpressionKind::Binary {
                                operator,
                                left,
                                right: value,
                            },
                            range,
                        );
                        minimum = parent_precedence;
                    }
                    Some(Frame::Group {
                        start,
                        parent_precedence,
                    }) => {
                        let end = self.expect(")")?;
                        self.expressions[value.0].span = span(&start, &end.span);
                        depth -= 1;
                        minimum = parent_precedence;
                    }
                    Some(Frame::Call {
                        mut call,
                        parent_precedence,
                    }) => {
                        call.arguments.push(value);
                        if self.accept(")") {
                            value = call.finish(self)?;
                            depth -= 1;
                            minimum = parent_precedence;
                        } else {
                            self.expect(",")?;
                            call.prepare_argument(self)?;
                            frames.push(Frame::Call {
                                call,
                                parent_precedence,
                            });
                            minimum = 0;
                            continue 'operand;
                        }
                    }
                    None => return Ok(value),
                }
            }
        }
    }

    fn primary(&mut self, depth: usize) -> Result<Primary, ParseError> {
        let token = self.current();
        let literal = match token.kind {
            TokenKind::Integer => Some(AstLiteral::Int(integer(token)?)),
            TokenKind::Decimal => {
                Some(AstLiteral::Decimal(token.lexeme.parse().map_err(|_| {
                    numeric_error(NumericErrorKind::InvalidDecimal, token)
                })?))
            }
            StringToken => Some(AstLiteral::String(
                token_text(token).expect("textual string token").to_owned(),
            )),
            TokenKind::Boolean => Some(AstLiteral::Bool(token.value == TokenValue::Boolean(true))),
            _ => None,
        };
        if let Some(literal) = literal {
            self.advance();
            return Ok(Primary::Value(self.push_expression(
                ExpressionKind::Literal(literal),
                token.span.clone(),
            )));
        }

        if self.at("read") {
            self.advance();
            let name = self.qualified_name()?;
            return self.start_call(depth, token, CallKind::Read, name);
        }
        if self.controlled_keyword("write") {
            self.advance();
            let name = self.qualified_name()?;
            return self.start_call(depth, token, CallKind::Write, name);
        }
        if token.kind == Identifier {
            let name = self.text(Identifier)?;
            if self.at("(") {
                return self.start_call(depth, token, CallKind::Function, name);
            }
            let parts = self.name_parts(name)?;
            let range = span(&token.span, &self.previous().span);
            return Ok(Primary::Value(
                self.push_expression(ExpressionKind::Path(parts), range),
            ));
        }
        if self.accept("(") {
            self.check_nesting(depth, token)?;
            return Ok(Primary::Group(token.span.clone()));
        }
        Err(diagnostic(ExpectedExpression, token))
    }

    fn start_call(
        &mut self,
        depth: usize,
        start: &Token<'_>,
        kind: CallKind,
        name: String,
    ) -> Result<Primary, ParseError> {
        let opening = self.expect("(")?;
        self.check_nesting(depth, opening)?;
        Ok(Primary::Call(Call {
            kind,
            name,
            start: start.span.clone(),
            arguments: Vec::new(),
            names: Vec::new(),
        }))
    }
}

fn binary_operator(text: &str) -> Option<(BinaryOperator, u8)> {
    use BinaryOperator::*;
    Some(match text {
        "==" => (Eq, 10),
        "!=" => (Ne, 10),
        "<" => (Lt, 20),
        "<=" => (Le, 20),
        ">" => (Gt, 20),
        ">=" => (Ge, 20),
        "+" => (Add, 30),
        "-" => (Sub, 30),
        "*" => (Mul, 40),
        "/" => (Div, 40),
        _ => return None,
    })
}
