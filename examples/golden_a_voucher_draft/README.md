# Golden A Canonical Voucher Draft

This NSL v0.2 controlled-WRITE example versions
`FINANCE.VOUCHER_DRAFT_CREATE` to `1.1.0` without changing the legacy v1.0
voucher example.

The deterministic resolver produces the complete business payload before the
approval boundary. The WRITE tool therefore hashes stable Project and Account
references, balanced voucher lines, and attachment references. Runtime-created
approval request and evidence identifiers remain control lineage and are not
WRITE arguments.

Run the protected prefix with:

```bash
python -m nsl run --profile examples/golden_a_voucher_draft/controlled.profile.json \
  --business-operation-id 86000000-0000-4000-8000-000000000001 \
  --checkpoint-out /tmp/golden-a-checkpoint.json \
  --approval-request-out /tmp/golden-a-approval.json
```
