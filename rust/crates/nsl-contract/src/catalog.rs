use std::{
    collections::{BTreeMap, btree_map::Entry},
    fmt,
};

use crate::ToolContract;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CatalogErrorKind {
    DuplicateToolContract,
    MissingExactContract,
    UnknownToolContract,
    IncompatibleToolVersion,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CatalogError {
    pub kind: CatalogErrorKind,
    pub tool_id: String,
    pub version: String,
}

impl CatalogError {
    fn new(kind: CatalogErrorKind, tool_id: &str, version: &str) -> Self {
        Self {
            kind,
            tool_id: tool_id.into(),
            version: version.into(),
        }
    }
}

impl fmt::Display for CatalogError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        use CatalogErrorKind::*;
        f.write_str(match self.kind {
            DuplicateToolContract => "duplicate tool contract",
            MissingExactContract | UnknownToolContract => "unknown tool contract",
            IncompatibleToolVersion => "incompatible tool version",
        })
    }
}

impl std::error::Error for CatalogError {}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ToolContractCatalog {
    contracts: BTreeMap<String, BTreeMap<String, ToolContract>>,
}

impl ToolContractCatalog {
    pub fn new(contracts: impl IntoIterator<Item = ToolContract>) -> Result<Self, CatalogError> {
        let mut indexed = BTreeMap::<String, BTreeMap<String, ToolContract>>::new();
        for contract in contracts {
            let versions = indexed.entry(contract.tool_id().into()).or_default();
            match versions.entry(contract.version().into()) {
                Entry::Vacant(slot) => {
                    slot.insert(contract);
                }
                Entry::Occupied(_) => {
                    return Err(CatalogError::new(
                        CatalogErrorKind::DuplicateToolContract,
                        contract.tool_id(),
                        contract.version(),
                    ));
                }
            }
        }
        Ok(Self { contracts: indexed })
    }

    /// Look up an exact identity. Both missing IDs and missing versions are errors.
    pub fn get(&self, tool_id: &str, version: &str) -> Result<&ToolContract, CatalogError> {
        self.contracts
            .get(tool_id)
            .and_then(|versions| versions.get(version))
            .ok_or_else(|| {
                CatalogError::new(CatalogErrorKind::MissingExactContract, tool_id, version)
            })
    }

    /// Resolve using the Oracle's EXACT compatibility policy.
    pub fn resolve(&self, tool_id: &str, version: &str) -> Result<&ToolContract, CatalogError> {
        self.contracts
            .get(tool_id)
            .ok_or_else(|| {
                CatalogError::new(CatalogErrorKind::UnknownToolContract, tool_id, version)
            })?
            .get(version)
            .ok_or_else(|| {
                CatalogError::new(CatalogErrorKind::IncompatibleToolVersion, tool_id, version)
            })
    }
}
