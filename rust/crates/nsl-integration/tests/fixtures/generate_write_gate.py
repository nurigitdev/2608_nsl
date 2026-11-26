"""R8-S05 component-composition gate from frozen Python, no business I/O.
Usage Python3.12 -I -B SCRIPT ARCHIVE base|skill|normal|exceptions.
Native CHECK consumes recorded predicate envelopes; R6 certifies expression/prefix execution.
"""
import sys,runpy,dataclasses,asyncio,json,copy
from pathlib import Path
from datetime import datetime,timedelta
from collections.abc import Mapping
sys.path.insert(0,str(Path(sys.argv[1]).resolve()))
from nsl.core import *
from nsl.ir import NsoCodec,canonical_json,encode_value,CheckStatement
from nsl.validation import *
from nsl.approval import ApprovalEvidence,ApprovalResumeBindings,ApprovalEvidenceValidator,RecordedApprovalClock
from nsl.idempotency import *
from nsl.write_execution import *
from nsl.write_intent import compute_write_intent_hash
h=runpy.run_path(str(Path(sys.argv[1])/'tests/test_v0_2_controlled_runtime.py'))
_,_,original,ctx,intent=h['_prepared_context']();NOW=h['NOW'];US=timedelta(microseconds=1)
PIN='28a949ec96581cbdc52c8133527b23bb5b72ee0a'
def plain(v):
 if isinstance(v,datetime):return v.isoformat()
 if dataclasses.is_dataclass(v):return {f.name:plain(getattr(v,f.name)) for f in dataclasses.fields(v)}
 if isinstance(v,Mapping):return {k:plain(x) for k,x in v.items()}
 if isinstance(v,(tuple,list)):return [plain(x) for x in v]
 if isinstance(v,(set,frozenset)):return sorted(v)
 return v

def thaw(v):
 if isinstance(v,Mapping):return {k:thaw(x) for k,x in v.items()}
 if isinstance(v,(tuple,list)):return [thaw(x) for x in v]
 return v

def envelope(e):return dict(value=encode_value(thaw(e.value)),type=e.type_info.to_data(),presence=e.presence.value,completeness=e.completeness.value,classification=e.classification.value,provenance_refs=list(e.provenance_refs))
def base():
 i={k:getattr(intent,k) for k in ('tenant_id','business_operation_id','node_id','tool_ref','tool_id','approval_id','skill_semantic_hash','payload_hash','write_intent_hash')};i['canonical_arguments']={k:envelope(v) for k,v in intent.canonical_arguments.items()}
 bindings=dict(tenant_id=intent.tenant_id,execution_id=ctx.identity.execution_id,business_operation_id=intent.business_operation_id,approval_id=intent.approval_id,approval_request_id='gate-approval',skill_semantic_hash=ctx.skill.semantic_hash,payload_hash=intent.payload_hash,write_intent_hash=intent.write_intent_hash)
 approval=ApprovalEvidence(**bindings,decision=ApprovalDecision.APPROVED,approver_id='gate-approver',decided_at=NOW-timedelta(seconds=1),expires_at=NOW+timedelta(seconds=1),evidence_integrity='gate-authenticated-proof')
 return dict(oracle=PIN,intent=i,principal=plain(ctx.request.principal),execution_id=ctx.identity.execution_id,business_operation_id=ctx.identity.business_operation_id,now=NOW.isoformat(),prior=(NOW-US).isoformat(),provider_until=(NOW+timedelta(days=30)).isoformat(),required_until=(NOW+timedelta(days=7)).isoformat(),contract=original.to_canonical_data(),contract_evidence=plain(h['_contract_evidence'](original)),policy=plain(h['_policy_decision'](intent,ctx,h['_contract_evidence'](original))),precondition=plain(h['_business_precondition'](intent,original)),approval=plain(approval),approval_bindings=bindings,predicates=[dict(check_id=c.check_id,truth=c.status is not CheckStatus.FAIL,presence=c.presence.value,completeness=c.completeness.value,provenance_refs=list(c.provenance_refs)) for c in ctx.checks])

def ctor(cls,d):
 d=dict(d)
 for k in ('decided_at','expires_at','evaluated_at','observed_at','verified_at','guaranteed_until'):
  if k in d and d[k] is not None:d[k]=datetime.fromisoformat(d[k])
 if 'decision' in d:d['decision']=ApprovalDecision(d['decision'])
 if 'effect' in d:d['effect']=PolicyEffect(d['effect'])
 if 'mechanism' in d:d['mechanism']=BusinessPreconditionMechanism(d['mechanism'])
 if 'risk_level' in d:d['risk_level']=RiskLevel(d['risk_level'])
 if 'capability' in d:d['capability']=ToolCapability(d['capability'])
 return cls(**d)

async def run(config):
 b=base();i=intent;skill=ctx.skill;case=config['case'];execution=h['_execution_evidence'](ctx)
 predicates=copy.deepcopy(b['predicates'])
 if case=='check-fail':predicates[0]['truth']=False
 if case=='check-unknown':predicates[0]['completeness']='UNKNOWN'
 if case=='check-both':predicates[0]['truth']=False;predicates[1]['completeness']='PARTIAL'
 if case=='no-check':
  skill=dataclasses.replace(skill,body=tuple(n for n in skill.body if not isinstance(n,CheckStatement))).with_computed_hash();i=dataclasses.replace(i,skill_semantic_hash=skill.semantic_hash,write_intent_hash=compute_write_intent_hash(skill_semantic_hash=skill.semantic_hash,write_node_id=i.node_id,tool_id=i.tool_id,payload_hash=i.payload_hash));predicates=[]
 local_ctx=type(ctx)(skill,ctx.request);local_ctx.checks=[]
 checks={n.check_id:n for n in skill.body if isinstance(n,CheckStatement)}
 for p in predicates:local_ctx.checks.append(StrictRuleEvaluator().evaluate(checks[p['check_id']],ValueEnvelope(p['truth'],BOOL,Presence(p['presence']),Completeness(p['completeness']),DataClassification.INTERNAL,tuple(p['provenance_refs']))))
 execution=dataclasses.replace(execution,validation=ValidationSummary.from_checks(tuple(local_ctx.checks)))
 current=original
 if case in ('current-contract','recompiled-old-approval','recompiled-approved'):current=dataclasses.replace(original,timeout_ms=original.timeout_ms+1)
 if case in ('recompiled-old-approval','recompiled-approved'):
  from nsl.compiler import NslCompiler
  from nsl.tools import ToolContractCatalog
  catalog,_=h['_catalog']()
  skill=NslCompiler(ToolContractCatalog(tuple(current if t.tool_id==current.tool_id else t for t in catalog._contracts.values()))).compile(h['SOURCE']).skill
  i=dataclasses.replace(i,skill_semantic_hash=skill.semantic_hash,write_intent_hash=compute_write_intent_hash(skill_semantic_hash=skill.semantic_hash,write_node_id=i.node_id,tool_id=i.tool_id,payload_hash=i.payload_hash))
  local_ctx.skill=skill
 ce=h['_contract_evidence'](current);policy=h['_policy_decision'](i,local_ctx,ce);pre=h['_business_precondition'](i,current)
 bindings={**b['approval_bindings'],'skill_semantic_hash':skill.semantic_hash,'write_intent_hash':i.write_intent_hash};approval=ctor(ApprovalEvidence,{**b['approval'],'skill_semantic_hash':skill.semantic_hash,'write_intent_hash':i.write_intent_hash})
 if case=='recompiled-old-approval':approval=ctor(ApprovalEvidence,b['approval'])
 delta=config.get('delta',0)
 if case=='approval-rejected':approval=dataclasses.replace(approval,decision=ApprovalDecision.REJECTED)
 if case=='approval-expiry':approval=dataclasses.replace(approval,expires_at=NOW+delta*US)
 if case.startswith('approval-binding-'):
  f=case.removeprefix('approval-binding-');approval=dataclasses.replace(approval,**{f:'sha256:'+'f'*64 if f in ('skill_semantic_hash','payload_hash','write_intent_hash') else 'other'})
 if case=='policy-deny':policy=dataclasses.replace(policy,effect=PolicyEffect.DENY)
 if case=='policy-expiry':policy=dataclasses.replace(policy,expires_at=NOW+delta*US)
 if case=='policy-future':policy=dataclasses.replace(policy,evaluated_at=NOW+US)
 if case=='policy-binding':policy=dataclasses.replace(policy,subject_id='other')
 if case=='precondition-missing':pre=None
 if case=='precondition-expiry':pre=dataclasses.replace(pre,expires_at=NOW+delta*US)
 if case=='precondition-future':pre=dataclasses.replace(pre,observed_at=NOW+US)
 if case=='precondition-atomic':pre=dataclasses.replace(pre,provider_atomic_enforcement=False)
 if case.startswith('precondition-binding-'):
  f=case.removeprefix('precondition-binding-');pre=dataclasses.replace(pre,**{f:'sha256:'+'f'*64 if f.endswith('hash') else 'other'})
 for name in ('runtime_preflight_passed','trust_valid','intent_binding_valid','durability_ready','replay_live_write_allowed'):
  if case=='axis-'+name:execution=dataclasses.replace(execution,**{name:False})
 if case=='scope':local_ctx.request=dataclasses.replace(local_ctx.request,principal=dataclasses.replace(local_ctx.request.principal,scopes=frozenset({'nsl:skill:execute'})))
 if case=='principal':
  from nsl.security import PrincipalVerification
  local_ctx.request=dataclasses.replace(local_ctx.request,principal=dataclasses.replace(local_ctx.request.principal,verification=PrincipalVerification.UNVERIFIED))
 events=[];store=[];policy_calls=0;reserve_calls=0;provider_calls=0;av=None;decision=None;phase='approval';code=None
 class Proof:
  def verify(self,e):events.append('verify');return case!='approval-proof'
 class Policy:
  def authorize_write(self,r):
   nonlocal policy_calls
   events.append('policy');policy_calls+=1;return policy
 class Idem:
  async def reserve(self,r):
   nonlocal reserve_calls
   events.append('reserve');reserve_calls+=1
   disp=IdempotencyDisposition.CONFLICT if case=='idempotency-conflict' else IdempotencyDisposition.REUSED if case in ('reused','pending') else IdempotencyDisposition.CREATED
   return IdempotencyReservation(disp,r,'result:original' if case=='reused' else None)
 class Storage:
  async def record_requested(self,r):
   events.append('WRITE_REQUESTED');store.append(r)
   if case=='storage-error':raise OSError('protected storage failure')
   return WriteRequestedReceipt('sha256:'+'f'*64 if case=='receipt-hash' else r.record_hash,'gate-durable',NOW-US if case=='receipt-time' else NOW,case!='receipt-durable')
 class Provider:
  async def execute(self,r):
   nonlocal provider_calls
   events.append('PROVIDER_SPY');provider_calls+=1;return 'provider-result'
 record_hash=None;local_ctx.checkpoint.write_request_durable=config.get('old_flag',False)
 try:
  av=ApprovalEvidenceValidator().validate(approval,ApprovalResumeBindings(**bindings),Proof(),RecordedApprovalClock(NOW));execution=dataclasses.replace(execution,approval_status=av.approval_status,approval_evidence_valid=av.approval_gate_passed)
  phase='guard';p=Policy().authorize_write(write_policy_request(i,local_ctx,ce));decision=WriteGuard().evaluate(WriteGuardRequest(i,local_ctx,ce,execution,p,pre),now=NOW)
  if decision.eligibility is not PreWriteEligibility.ALLOWED:raise WriteEligibilityError('blocked')
  phase='idempotency';ev=ProviderIdempotencyEvidence(current.tool_id,current.contract_hash,'PROVIDER_DEFINED','gate-provider-guarantee',NOW,NOW+timedelta(days=30),case!='retention')
  reservation=await IdempotencyCoordinator(Idem()).reserve(intent=i,context=local_ctx,contract=current,provider_evidence=ev,now=NOW,required_until=NOW+timedelta(days=7))
  phase='builder';request=WriteCallRequestBuilder().build(intent=i,context=local_ctx,contract_evidence=ce,reservation=reservation,approval_request_id=bindings['approval_request_id'],invocation_id='gate-invocation',business_preconditions=pre,trace_refs=('gate-trace',))
  phase='evidence';await WriteExecutionCoordinator(Provider(),Storage()).execute(request=request,decision=decision,context=local_ctx,requested_at=NOW);phase='provider';record_hash=store[-1].record_hash
 except Exception as e:code=getattr(e,'detail_code',None)
 return dict(config=config,skill=NsoCodec.encode(skill).hex(),intent={**b['intent'],'skill_semantic_hash':i.skill_semantic_hash,'write_intent_hash':i.write_intent_hash},principal=plain(local_ctx.request.principal),contract=current.to_canonical_data(),contract_evidence=plain(ce),policy=plain(policy),expected_policy_request=plain(write_policy_request(i,local_ctx,ce)),precondition=plain(pre),approval=plain(approval),approval_bindings=bindings,predicates=predicates,execution={**plain(execution),'validation':execution.validation.status.value},observed=dict(phase=phase,checks=plain(local_ctx.checks),validation=plain(execution.validation),approval=plain(av),decision=plain(decision),code=code,events=events,policy_calls=policy_calls,reserve_calls=reserve_calls,stored=len(store),provider_calls=provider_calls,flag=local_ctx.checkpoint.write_request_durable,record_hash=record_hash))

async def main():
 mode=sys.argv[2]
 if mode=='manifest':
  import hashlib
  root=Path(__file__).resolve().parents[5];p=Path(__file__).resolve().parent
  files=[root/n for n in ('rust/crates/nsl-write/tests/fixtures/idempotency-keys-oracle.tsv', 'rust/crates/nsl-write/tests/fixtures/idempotency-models-oracle.tsv', 'rust/crates/nsl-write/tests/fixtures/idempotency-reservations-oracle.tsv', 'rust/crates/nsl-write/tests/fixtures/idempotency-retention-oracle.tsv', 'rust/crates/nsl-write/tests/fixtures/write-admission-oracle.tsv', 'rust/crates/nsl-write/tests/fixtures/write-goldens-oracle.tsv', 'rust/crates/nsl-write/tests/fixtures/write-guard-decisions-oracle.tsv', 'rust/crates/nsl-write/tests/fixtures/write-guard-exceptions-oracle.tsv', 'rust/crates/nsl-write/tests/fixtures/write-guard-models-oracle.tsv', 'rust/crates/nsl-write/tests/fixtures/write-guard-prewrite-oracle.tsv', 'rust/crates/nsl-write/tests/fixtures/write-metadata-oracle.tsv', 'rust/crates/nsl-write/tests/fixtures/write-models-oracle.tsv', 'rust/crates/nsl-write/tests/fixtures/write-negative-oracle.tsv', 'rust/crates/nsl-write/tests/fixtures/write-requested-build-oracle.tsv', 'rust/crates/nsl-write/tests/fixtures/write-requested-dispositions-oracle.tsv', 'rust/crates/nsl-write/tests/fixtures/write-requested-durability-oracle.tsv', 'rust/crates/nsl-write/tests/fixtures/write-requested-models-oracle.tsv', 'rust/crates/nsl-write/tests/fixtures/write-requested-records-oracle.tsv', 'rust/crates/nsl-write/tests/fixtures/write-values-oracle.tsv')]+[p/n for n in ('write-gate-base.json','write-gate.nso','write-gate-normal-oracle.tsv','write-gate-exceptions-oracle.tsv')]+[root/'rust/crates/nsl-runtime/tests/fixtures/approval-gate-manifest.json']
  rows=[]
  for f in files:
   b=f.read_bytes();r=dict(path=str(f.relative_to(root)),sha256=hashlib.sha256(b).hexdigest())
   if f.suffix=='.tsv':r['rows']=len(b.splitlines())-1
   rows.append(r)
  m=dict(schema_version=1,gate_id='R8-EXIT',oracle_commit=PIN,oracle_lock_sha256=hashlib.sha256((root/'rust-transition/baseline/python-oracle-lock.json').read_bytes()).hexdigest(),approved_difference_ids=['R8-S01-SNAPSHOT-METADATA','R8-S03-CURRENT-PRINCIPAL-SCOPE','R8-S04-RESERVATION-DISPOSITION','R8-S05-DECLARED-CONTRACT-BINDING'],comparisons=['checks','validation','approval','policy_request','eligibility','phase','record_hash','checkpoint_flag','port_counts','event_order'],fixtures=rows)
  print(json.dumps(m,ensure_ascii=False,indent=2));return
 if mode=='base':print(canonical_json(base()).decode());return
 if mode=='skill':sys.stdout.buffer.write(NsoCodec.encode(ctx.skill));return
 print('# Python Oracle '+PIN)
 cases=[dict(case=c) for c in ['pass','no-check','recompiled-old-approval','recompiled-approved','check-fail','check-unknown','check-both','approval-proof','approval-rejected','policy-deny','policy-binding','policy-future','precondition-missing','precondition-future','precondition-atomic','idempotency-conflict','retention','storage-error','receipt-hash','receipt-time','receipt-durable']]
 cases += [dict(case=axis,delta=d) for axis in ('approval-expiry','policy-expiry','precondition-expiry') for d in (-1,0,1)]
 cases += [dict(case='approval-binding-'+f) for f in ('tenant_id','execution_id','business_operation_id','approval_id','approval_request_id','skill_semantic_hash','payload_hash','write_intent_hash')]
 cases += [dict(case='precondition-binding-'+f) for f in ('tenant_id','business_operation_id','write_node_id','tool_id','contract_hash','payload_hash','write_intent_hash')]
 cases += [dict(case='axis-'+f) for f in ('runtime_preflight_passed','trust_valid','intent_binding_valid','durability_ready','replay_live_write_allowed')]
 cases += [dict(case=c,old_flag=True) for c in ('storage-error','receipt-hash','receipt-durable','receipt-time')]
 if mode=='exceptions':cases=[dict(case=c) for c in ('current-contract','scope','principal','reused','pending')]
 for n,c in enumerate(cases):print(str(n)+'-'+c['case'],canonical_json(await run(c)).hex(),sep='\t')
asyncio.run(main())
