# NSL Business Controls Showcase Pack

- **Pack ID:** `NEX.NSL.BUSINESS_CONTROLS`
- **Pack Version:** `0.1.0`
- **상태:** Corporate Card Module Accepted; 4 Modules Planned
- **작성일:** 2026-08-20
- **Inventory:** `examples/nsl_showcase_pack/pack.json`

## 1. 목적

NSL이 예산 점검 한 가지에 한정되지 않고 조회, 집계, 정책 통제, 권한,
데이터 분류, Audit, Replay와 Scenario 검증에 재사용될 수 있음을 고객에게
보여주는 업무 통제 예제 Pack이다. 각 Skill은 독립 실행되며 Workflow Language나
Skill 간 orchestration을 도입하지 않는다.

## 2. Pack Roadmap

| Module | Status |
|---|---|
| Corporate Card Control | `IMPLEMENTED` |
| Employee Contract Expiry | `PLANNED` |
| Access Segregation | `PLANNED` |
| Vendor Concentration | `PLANNED` |
| Duplicate Invoice | `PLANNED` |

현재 Pack은 source/test showcase다. 다섯 모듈이 완료되기 전에는 일부 구현을
production-ready signed NSP로 표현하지 않는다.

## 3. Corporate Card Module

### Summary Skill

`FINANCE.CORPORATE_CARD_MONTHLY_SUMMARY`는 활성 팀과 월별 카드 거래를 읽고
`sum(List<Money<KRW>>)`와 `count(List<Transaction>)`로 팀별 합계와 건수를
EMIT한다. CHECK statement가 없으므로 보고 목적 결과와 정책 위반을 혼동하지
않는다. 거래가 없는 팀의 Money 합은 typed KRW zero이고 건수는 0이다.

### Policy Skill

`FINANCE.CORPORATE_CARD_MONTHLY_POLICY_CHECK`는 같은 사실에 다음 여섯 통제를
적용한다.

1. 월 합계 100만원 이하
2. 활성 팀의 사용 건수 1건 이상
3. 월 사용 건수 10건 미만
4. 증빙 누락 0건
5. 건별 최대 50만원 이하
6. 제한 업종 사용 0건

금액과 건수 임계값은 context에서 주입된다. Mock Tool은 정책 결과가 아니라
거래, 누락 후보, 제한 업종 후보와 최대 건별 금액을 반환하고 NSL이 최종
PASS/FAIL을 소유한다.

## 4. Boundary and Negative Acceptance

금액 경계는 900,000, 1,000,000, 1,000,001 KRW를, 건수 경계는 0, 9, 10을
동시에 검증한다. 정확히 100만원과 50만원은 PASS이고 100만원 초과와 10건
이상은 FAIL이다.

Summary와 Policy suite는 각각 다음 5개 case를 실행한다.

- boundary matrix
- missing Tool fixture
- card Scope authorization denied
- active team collection max+1
- deterministic repeat

조직 Scope가 있는 Principal은 활성 팀 Tool을 호출할 수 있지만 카드 Scope가
없으면 카드 provider 호출 전에 거부된다. Resource evidence에는 허용된 조직
Tool 1회만 기록된다.

## 5. Security and Product Boundary

- 월 기준은 caller가 `year`, `month`로 명시하며 hidden wall clock을 사용하지 않음
- 카드에서 파생된 금액과 건수는 `CONFIDENTIAL`, 값이 없는 CHECK 상태는 `INTERNAL`
- endpoint와 credential은 Pack content에서 제외
- Tool Contract는 객관적 사실을, NSL은 고객 정책 판정을 소유
- 최대 10개 팀과 11개 Tool call로 bounded execution 유지
- 실제 고객 Adapter는 승인/취소/환불, 기준 timezone, 거래 당시 소속을 명시해야 함

## 6. Verification

Repository test는 두 Skill의 compile/run 결과, 출력 전용 Summary의 empty CHECK,
6개 정책의 경계값, 10개 scenario 결과와 Pack inventory를 검증한다. 최종 품질
수치는 전체 `tools/run_quality.py` 결과를 기준으로 기록한다.

```text
Regression: 1225 passed
Statement Coverage: 99.56%
Branch Coverage: 98.76%
Performance Gate: PASSED
Summary Scenarios: 5/5
Policy Scenarios: 5/5
```

Corporate Card Module 내부 미완료 항목은 없다. 나머지 네 Pack Module은
`pack.json`에서 `PLANNED`로 명시한다.
