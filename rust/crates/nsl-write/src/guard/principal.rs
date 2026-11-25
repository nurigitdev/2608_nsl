use super::{WriteGuardError, WriteGuardErrorKind, error, required};
use std::collections::BTreeSet;
/// Borrowed fields of the host's *current* ExecutionPrincipal, including its
/// verified-authentication metadata. No copied token/credential or owned context.
/// As with Runtime's principal model, the host must establish verification;
/// this view validates the admitted model, not an authentication protocol.
#[derive(Clone, Copy)]
pub struct WritePrincipal<'a> {
    pub tenant_id: &'a str,
    pub subject_id: &'a str,
    pub actor_type: &'a str,
    pub roles: &'a BTreeSet<String>,
    pub scopes: &'a BTreeSet<String>,
    pub auth_context_ref: &'a str,
    pub verified: bool,
    pub on_behalf_of: Option<&'a str>,
}
impl WritePrincipal<'_> {
    pub fn validate(&self) -> Result<(), WriteGuardError> {
        let field = |value: &str, path: &str| {
            required(value, path).map_err(|_| {
                error(
                    WriteGuardErrorKind::Principal,
                    path,
                    "invalid current execution principal",
                )
            })
        };
        field(self.tenant_id, "$.context.principal.tenant_id")?;
        field(self.subject_id, "$.context.principal.subject_id")?;
        if !matches!(self.actor_type, "USER" | "SERVICE") {
            return Err(error(
                WriteGuardErrorKind::Principal,
                "$.context.principal.actor_type",
                "current principal actor type must be USER or SERVICE",
            ));
        }
        field(
            self.auth_context_ref,
            "$.context.principal.auth_context_ref",
        )?;
        for value in self.roles {
            field(value, "$.context.principal.roles")?;
        }
        for value in self.scopes {
            field(value, "$.context.principal.scopes")?;
        }
        if let Some(value) = self.on_behalf_of {
            field(value, "$.context.principal.on_behalf_of")?;
        }
        if !self.verified {
            return Err(error(
                WriteGuardErrorKind::Principal,
                "$.context.principal.verification",
                "verified current principal is required",
            ));
        }
        Ok(())
    }
}
