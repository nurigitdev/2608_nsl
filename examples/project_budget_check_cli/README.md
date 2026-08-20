# PROJECT_BUDGET_CHECK CLI Example

Run the complete local profile from the repository root:

```powershell
python -m nsl check --profile examples\project_budget_check.profile.json
python -m nsl compile --profile examples\project_budget_check.profile.json -o test\project_budget_check.nso
python -m nsl run --profile examples\project_budget_check.profile.json
python -m nsl test --suite examples\project_budget_check.scenarios.json
```

The fixture returns one parent project with a KRW 100,000,000 budget and
three child expenses totaling KRW 87,500,000. The expected check status is
`PASS` and the remaining budget is KRW 12,500,000.

The Skill contains `CONFIDENTIAL` values. Standalone portable replay export is
therefore rejected unless a protected snapshot backend is provided.
