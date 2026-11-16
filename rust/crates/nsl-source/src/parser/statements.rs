use super::*;

struct Foreach {
    start: SourceSpan,
    iterator: String,
    collection: ExpressionId,
    max_iterations: Integer,
    body: Vec<StatementId>,
}

impl Cursor<'_, '_> {
    pub(super) fn statement(&mut self) -> Result<StatementId, ParseError> {
        let mut loops = Vec::new();
        loop {
            while self.at("foreach") {
                loops.push(self.start_foreach(loops.len())?);
            }
            let start = self.current();
            let statement = if !loops.is_empty() && self.accept("}") {
                let frame = loops.pop().expect("open foreach");
                AstStatement {
                    kind: StatementKind::Foreach {
                        iterator: frame.iterator,
                        collection: frame.collection,
                        max_iterations: frame.max_iterations,
                        body: frame.body,
                    },
                    span: span(&frame.start, &start.span),
                }
            } else {
                let kind = self.simple_statement(loops.len())?;
                AstStatement {
                    kind,
                    span: span(&start.span, &self.previous().span),
                }
            };
            let id = StatementId(self.statements.len());
            self.statements.push(statement);
            if let Some(parent) = loops.last_mut() {
                parent.body.push(id);
            } else {
                return Ok(id);
            }
        }
    }

    fn start_foreach(&mut self, depth: usize) -> Result<Foreach, ParseError> {
        let start = self.advance();
        self.check_nesting(depth, start)?;
        let iterator = self.text(Identifier)?;
        self.expect("in")?;
        let collection = self.expression(depth + 1)?;
        self.expect("max")?;
        let limit = self.expect_kind(TokenKind::Integer)?;
        let max_iterations = integer(limit)?;
        if max_iterations.as_str() == "0" {
            return Err(diagnostic(NonPositiveForeachLimit, limit));
        }
        self.expect("{")?;
        Ok(Foreach {
            start: start.span.clone(),
            iterator,
            collection,
            max_iterations,
            body: Vec::new(),
        })
    }

    fn simple_statement(&mut self, depth: usize) -> Result<StatementKind, ParseError> {
        let token = self.current();
        Ok(match token_text(token) {
            Some("approve") if self.controlled_keyword("approve") => {
                self.advance();
                let id_token = self.current();
                let approval_id = self.text(Identifier)?;
                self.expect("{")?;
                self.expect("message")?;
                let message = self.text(StringToken)?;
                self.expect(";")?;
                self.expect("}")?;
                StatementKind::Approve {
                    approval_id,
                    approval_id_span: id_token.span.clone(),
                    message,
                }
            }
            Some("let") => {
                self.advance();
                let name = self.text(Identifier)?;
                self.expect("=")?;
                let value = self.expression(depth)?;
                self.expect(";")?;
                StatementKind::Let { name, value }
            }
            Some("check") => {
                self.advance();
                let check_id = self.text(Identifier)?;
                self.expect("{")?;
                self.expect("assert")?;
                let condition = self.expression(depth)?;
                self.expect(";")?;
                self.expect("severity")?;
                let severity = self.text(Identifier)?;
                self.expect(";")?;
                self.expect("on_fail")?;
                let on_fail = self.text(Identifier)?;
                self.expect(";")?;
                self.expect("message")?;
                let message = self.text(StringToken)?;
                self.expect(";")?;
                self.expect("}")?;
                StatementKind::Check {
                    check_id,
                    condition,
                    severity,
                    on_fail,
                    message,
                }
            }
            Some("emit") => {
                self.advance();
                self.expect("{")?;
                let mut fields = Vec::new();
                while !self.accept("}") {
                    let name = self.text(Identifier)?;
                    self.expect(":")?;
                    fields.push((name, self.expression(depth)?));
                    self.expect(";")?;
                }
                StatementKind::Emit { fields }
            }
            _ => return Err(diagnostic(UnexpectedStatement, token)),
        })
    }
}
