#![allow(dead_code)]

use nsl_source::ast::{Ast, AstSkill};
use nsl_source::{ParseLimits, ParseMode, Parser, SourceFile, SourcePosition, SourceSpan};

pub fn encode(value: &str) -> String {
    if value.is_empty() {
        "-".into()
    } else {
        value.bytes().map(|byte| format!("{byte:02x}")).collect()
    }
}

pub fn decode(value: &str) -> String {
    if value == "-" {
        return String::new();
    }
    String::from_utf8(
        (0..value.len())
            .step_by(2)
            .map(|index| u8::from_str_radix(&value[index..index + 2], 16).unwrap())
            .collect(),
    )
    .unwrap()
}

pub fn position(point: SourcePosition) -> String {
    format!("{},{},{}", point.offset(), point.line(), point.column())
}

pub fn span(value: &SourceSpan) -> String {
    format!(
        "{}\t{}\t{}",
        value.source_id().as_str(),
        position(value.start()),
        position(value.end())
    )
}

pub fn parse(source: &SourceFile, max_nesting: usize) -> AstSkill {
    let Ast::RootSkill(skill) =
        Parser::parse(source, ParseMode::RootSkill, ParseLimits { max_nesting }).unwrap()
    else {
        panic!("root skill")
    };
    *skill
}

pub fn source(version: &str, body: &str) -> SourceFile {
    SourceFile::from_text("approvals/skill.ns", &format!("language NSL \"{version}\"; skill TEST {{ version \"1\"; risk READ_ONLY; limits {{ tool_calls 1; loop_iterations 1; emitted_rows 1; collection_size 1; }} input {{ root: Int; items: List<Int>; }}\n{body}\n}}"), None).unwrap()
}

pub fn approval(name: &str) -> String {
    format!("approve {name} {{ message \"approve\"; }}\n")
}

pub fn write(name: &str) -> String {
    format!("write TEST.CREATE(amount = root) approved_by {name}")
}
