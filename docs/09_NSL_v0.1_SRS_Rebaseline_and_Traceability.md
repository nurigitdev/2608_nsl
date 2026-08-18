# NSL v0.1 SRS Rebaseline and Traceability

- **Baseline ID:** `NSL-V0.1-SRS-RB1`
- **Slice:** `0002`
- **상태:** Verifiable Requirement Baseline
- **작성일:** 2026-08-18
- **SRS:** `01_NeX_Skill_Language_v0.1_Interpreter_Runtime_SRS_v1.1_Draft.md`
- **Machine-readable Baseline:** `requirements/nsl_v0_1_traceability.json`

## 1. 목적

이 문서는 SRS를 구현 순서에 참고하는 수준을 넘어 Requirement, Slice, Verification, Repository Evidence가 추적되는 검증 가능한 Baseline으로 고정한다.

```text
SRS Requirement
  -> Baseline Status
  -> Owning Slice
  -> Verification ID
  -> Repository Evidence
```

## 2. Rebaseline 결과

SRS Requirement Table에서 추출한 Baseline은 다음과 같다.

| 항목 | 값 |
|---|---:|
| Requirement | 325 |
| MUST | 294 |
| SHOULD | 30 |
| MAY | 1 |
| IMPLEMENTED | 43 |
| PARTIAL | 221 |
| PLANNED | 61 |

Canonical Requirement Fingerprint:

```text
sha256:1aa65b838f6f3a54b3e76f0b73e3e0efa1fac0558ea1d2c05c03542433bbac77
```

Fingerprint 입력은 SRS에 나타나는 순서대로 정규화한 `Requirement ID | Priority | Text`이다. Requirement 문구나 Priority가 변경되면 명시적인 Rebaseline 없이 Quality Gate를 통과할 수 없다.

## 3. Status 정의

| Status | 정의 |
|---|---|
| `IMPLEMENTED` | 구현과 전용 Repository Evidence가 존재하고 현재 Acceptance가 확인됨 |
| `PARTIAL` | 일부 구현 또는 공통 Vertical Slice 증거가 있으나 SRS 전체 조건이 닫히지 않음 |
| `PLANNED` | 구현 전이며 담당 Slice와 Verification Plan이 지정됨 |
| `OUT_OF_SCOPE` | 승인된 Scope Decision으로 현재 Release에서 제외됨 |
| `SUPERSEDED` | 승인된 새 Requirement 또는 Decision으로 대체됨 |

`IMPLEMENTED`는 코드 존재만으로 부여하지 않는다. 요구사항을 직접 검증하는 Test 또는 Quality Evidence가 필요하다. Slice 0001 상태는 이 원칙에 따라 보수적으로 분류했다.

## 4. Scope 결정

1. `NSL v0.1`은 Language Version이고 `Runtime v1.0`은 최초 구현 Release이다.
2. 품질 기준은 Statement Coverage 95% 이상, Branch Coverage 90% 이상이다.
3. Workflow Language, Multi-Skill Orchestration, WRITE, APPROVAL은 NSL v0.1 범위 밖이다.
4. Schedule은 NSL Syntax와 `.nso`에 포함하지 않는다.
5. Schedule은 하나의 등록된 Skill을 반복 호출하는 NeX Platform Extension이다.
6. SRS의 `NSL-SEC-013`만 Schedule Extension의 Service Principal Requirement로 Slice 0032에 배정한다.

## 5. Slice Ownership

| 범위 | Requirement 영역 | 완료점 |
|---|---|---|
| `0003~0006` | Architecture, Diagnostic, Source, Lexer, Parser, Include | Combined AST |
| `0007~0012` | Symbol, Type, Money, Built-in, Requires, Static Bound | Validated Semantic Model |
| `0013~0014` | IR Schema, Canonical Codec, Hash, Load Validation | Stable `.nso` |
| `0015~0021` | Runtime, LET, READ, FOREACH, LIMIT, CHECK, EMIT | Structured Execution Result |
| `0022~0025` | Principal, Authorization, Audit, Replay, MCP Adapter | Production Security Boundary |
| `0026~0029` | CLI, Package, Signature, NeX-AE Integration | Product Integration |
| `0030` | Performance, Reliability, Negative Acceptance, Traceability | NSL v0.1 SRS Certification |
| `0031~0032` | Schedule Contract/Store와 Runner | Platform Schedule Extension |
| `0033` | Chat, Immediate Run, Scheduled Run | Internal Pilot |

개별 Requirement의 정확한 담당 Slice는 Machine-readable Baseline을 단일 기준으로 사용한다.

## 6. Traceability Gate

```powershell
.\.venv\Scripts\python.exe tools\requirements_traceability.py
```

검증기는 다음 조건을 강제한다.

- SRS Requirement Row의 형식, ID 유일성, 전체 개수
- Canonical Requirement Fingerprint
- 모든 Requirement의 정확히 한 번 Base Mapping
- 유효한 Status, Slice, Verification ID
- `IMPLEMENTED` Evidence의 존재
- Extension Slice로 배정할 수 있는 Requirement의 명시적 Allowlist
- `OUT_OF_SCOPE`와 `SUPERSEDED`의 승인된 Scope Decision

`tools/run_quality.py`는 pytest 실행 전에 이 Gate를 수행한다. 따라서 Traceability 실패, Regression 실패, Coverage 실패 중 하나라도 발생하면 Commit과 동기화를 진행할 수 없다.

## 7. 변경 절차

Requirement에 영향을 주는 변경은 다음 순서를 따른다.

1. 영향받는 Requirement ID를 식별한다.
2. 구조 검토와 필요 Refactoring을 수행한다.
3. Baseline의 Status, Slice, Verification, Evidence를 갱신한다.
4. Requirement Text 또는 Priority 변경이면 Scope Decision과 Fingerprint를 Rebaseline한다.
5. Boundary, Robustness, Worst-case Test를 추가한다.
6. 전체 Traceability/Regression/Coverage Gate를 실행한다.

## 8. Slice 0002 Acceptance

Slice 0002는 다음 조건을 모두 만족할 때 완료된다.

- SRS의 325개 Requirement가 누락과 중복 없이 추출됨
- 모든 Requirement가 Status, Slice, Verification에 연결됨
- Slice 0001의 `IMPLEMENTED` 상태가 실제 Evidence로 검증됨
- SRS 문구와 Priority가 SHA-256 Fingerprint로 동결됨
- Coverage 기준과 Schedule 범위가 관련 문서에서 일치함
- Workflow Language가 NSL v0.1 범위 밖으로 명시됨
- Traceability Gate가 Mandatory Quality Gate에 통합됨
- 전체 Regression과 Coverage 기준이 통과함

## 9. 제한과 후속 감사

Evidence 경로의 존재는 Test가 Requirement 의미를 완전히 증명한다는 뜻이 아니다. 각 후속 Slice의 Structural Review에서 담당 Requirement를 다시 감사하고, 전체 조건이 확인된 경우에만 `PARTIAL`을 `IMPLEMENTED`로 변경한다.
