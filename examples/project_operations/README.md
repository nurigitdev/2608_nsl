# Canonical Project READ Operations

This flat P8.10 source set introduces the canonical three-segment Project READ
contracts without changing historical two-segment Skills or Tools.

The admitted identities are PROJECT.PROJECT.GET, PROJECT.PROJECT.LIST, and
PROJECT.MEMBERSHIP.LIST.

All three Skills are deterministic READ_ONLY programs. They use only the
corresponding Tool contract, require current project:project:read authority,
and rely on the Platform/EOS adapter for tenant and membership filtering. The
collection Skills enforce a maximum of 100 returned rows.

`artifact_manifest.json` binds every source digest, compiled semantic hash,
and the shared Tool catalog digest without changing the admitted NSL wheel.
