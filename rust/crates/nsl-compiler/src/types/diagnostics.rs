use std::fmt::Write;

include!("printable.rs");

// Tool Contract field names are model strings, not necessarily NSL identifiers.
// Oracle diagnostics use Python repr(), including its Unicode 15.0 printability.
pub(super) fn quoted_name(name: &str) -> String {
    let quote = if name.contains('\'') && !name.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut result = String::new();
    result.push(quote);
    for ch in name.chars() {
        match ch {
            '\n' => result.push_str("\\n"),
            '\r' => result.push_str("\\r"),
            '\t' => result.push_str("\\t"),
            '\\' => result.push_str("\\\\"),
            ch if ch == quote => {
                result.push('\\');
                result.push(ch);
            }
            ch => {
                let code = u32::from(ch);
                if NONPRINTABLE_BOUNDARIES
                    .partition_point(|boundary| *boundary <= code)
                    .is_multiple_of(2)
                {
                    result.push(ch);
                } else if code < 0x100 {
                    write!(result, "\\x{code:02x}").expect("String formatting");
                } else if code < 0x10000 {
                    write!(result, "\\u{code:04x}").expect("String formatting");
                } else {
                    write!(result, "\\U{code:08x}").expect("String formatting");
                }
            }
        }
    }
    result.push(quote);
    result
}
