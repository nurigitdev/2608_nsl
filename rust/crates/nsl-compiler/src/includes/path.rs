use std::fmt;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct InvalidIncludePath;

impl fmt::Display for InvalidIncludePath {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("expected a relative POSIX path within the include root")
    }
}

impl std::error::Error for InvalidIncludePath {}

fn has_drive(path: &str) -> bool {
    matches!(path.as_bytes(), [first, b':', ..] if first.is_ascii_alphabetic())
}

fn relative_parts(path: &str) -> Result<impl Iterator<Item = &str>, InvalidIncludePath> {
    if path.is_empty() || path.starts_with('/') || path.contains(['\0', '\\']) || has_drive(path) {
        return Err(InvalidIncludePath);
    }
    Ok(path.split('/').filter(|part| !matches!(*part, "" | ".")))
}

fn append_parts<'a>(
    base: &mut Vec<&'a str>,
    path: &'a str,
    floor: usize,
) -> Result<(), InvalidIncludePath> {
    for part in relative_parts(path)? {
        if part == ".." {
            if base.len() == floor {
                return Err(InvalidIncludePath);
            }
            base.pop();
        } else {
            base.push(part);
        }
    }
    if base.first().is_some_and(|part| has_drive(part)) {
        return Err(InvalidIncludePath);
    }
    Ok(())
}

pub(super) fn normalized_parts(path: &str) -> Result<Vec<&str>, InvalidIncludePath> {
    let mut parts = Vec::new();
    append_parts(&mut parts, path, 0)?;
    Ok(parts)
}

pub(super) fn source_parts(path: &str) -> Result<Vec<&str>, InvalidIncludePath> {
    if !path.ends_with(".ns") {
        return Err(InvalidIncludePath);
    }
    normalized_parts(path)
}

pub(super) fn target_path<'a>(
    mut parent: Vec<&'a str>,
    include_path: &'a str,
    root: &[&str],
) -> Result<String, InvalidIncludePath> {
    if !include_path.ends_with(".ns") || !parent.starts_with(root) {
        return Err(InvalidIncludePath);
    }
    append_parts(&mut parent, include_path, root.len())?;
    Ok(parent.join("/"))
}

/// Resolve a relative include lexically, without consulting the filesystem.
pub fn canonical_include_path(
    including_logical_path: &str,
    include_path: &str,
    include_root: &str,
) -> Result<String, InvalidIncludePath> {
    let root = normalized_parts(include_root)?;
    let mut parent = source_parts(including_logical_path)?;
    parent.pop();
    target_path(parent, include_path, &root)
}
