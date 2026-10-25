# Canonical Voucher Draft READ Operations

This P8.10 source set adds `FINANCE.VOUCHER_DRAFT.GET` and
`FINANCE.VOUCHER_DRAFT.LIST` without changing the frozen Golden A Voucher WRITE
Skill. Both Skills are deterministic READ_ONLY programs and rely on the EOS
adapter for fresh OA authorization, tenant isolation, active Project
membership, and the explicit finance-auditor override.

The LIST input requires one authoritative Project ID and bounded pagination.
The artifact manifest binds exact source, semantic, and Tool-catalog digests.
