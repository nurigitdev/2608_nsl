"""R9-S05 actual frozen prefix/suspend/resume/WRITE/verification/result.
Python3.12 -I -B SCRIPT ARCHIVE base|cases. Synthetic host adapters only.
"""
import asyncio,sys,runpy,dataclasses,json
from pathlib import Path
from datetime import datetime,timedelta
from collections.abc import Mapping
sys.path.insert(0,str(Path(sys.argv[1]).resolve()))
from nsl.core import *
from nsl.ir import NsoCodec,canonical_json,encode_value
from nsl.runtime import RuntimeEngine
from nsl.checkpoints import CheckpointCoordinator
from nsl.approval import *
from nsl.validation import *
from nsl.idempotency import *
from nsl.write_execution import *
from nsl.verification import *
from nsl.audit import InMemoryAuditSink
h=runpy.run_path(str(Path(sys.argv[1])/'tests/test_v0_2_controlled_runtime.py'));NOW=h['NOW']
PIN='28a949ec96581cbdc52c8133527b23bb5b72ee0a'
def plain(v):
 if isinstance(v,ValueEnvelope):return dict(value=encode_value(thaw(v.value)),type=v.type_info.to_data(),presence=v.presence,completeness=v.completeness,classification=v.classification,provenance_refs=list(v.provenance_refs))
 if isinstance(v,datetime):return v.isoformat()
 if dataclasses.is_dataclass(v):return {f.name:plain(getattr(v,f.name)) for f in dataclasses.fields(v)}
 if isinstance(v,Mapping):return {k:plain(x) for k,x in v.items()}
 if isinstance(v,(list,tuple)):return [plain(x) for x in v]
 if isinstance(v,(set,frozenset)):return sorted(v)
 return v

def thaw(v):
 if isinstance(v,Mapping):return {k:thaw(x) for k,x in v.items()}
 if isinstance(v,(list,tuple)):return [thaw(x) for x in v]
 return v

async def run(mode):
 catalog,contract,skill=h['_compiled']();request=h['_request']();tools=h['_read_tools'](catalog);engine=RuntimeEngine(catalog)
 prefix=await engine.execute_controlled_prefix(skill,request,tools,InMemoryAuditSink())
 ctx=CheckpointCoordinator().restore(prefix.checkpoint,skill,request)
 intent=await engine.prepare_controlled_write_intent(ctx,tools)
 approval=ApprovalRequest(tenant_id=ctx.tenant_id,execution_id=ctx.identity.execution_id,business_operation_id=intent.business_operation_id,approval_id=intent.approval_id,approval_request_id=ApprovalRequestId('approval-voucher-0063'),skill_id=skill.skill_id,skill_version=skill.skill_version,skill_semantic_hash=skill.semantic_hash,write_node_id=intent.node_id,tool_id=intent.tool_id,payload_hash=intent.payload_hash,write_intent_hash=intent.write_intent_hash,risk_level=RiskLevel.R3,message='Approve voucher draft',expires_at=NOW+timedelta(minutes=5),display_safe_summary={'document_type':'voucher-draft'},protected_payload_ref='approval-payload:0063',data_classification=DataClassification.CONFIDENTIAL)
 lifecycle=h['_LifecyclePort']()
 suspension=await ApprovalSuspensionCoordinator().suspend(ctx,approval,lifecycle,lifecycle,dataclasses.replace(h['_execution_evidence'](ctx),approval_status=ApprovalStatus.NOT_REQUESTED,approval_evidence_valid=False,current_policy_allowed=True,tool_preconditions_passed=True,business_preconditions_passed=True))
 proof=ApprovalEvidence(tenant_id=ctx.tenant_id,execution_id=ctx.identity.execution_id,business_operation_id=intent.business_operation_id,approval_id=intent.approval_id,approval_request_id=approval.approval_request_id,skill_semantic_hash=skill.semantic_hash,decision=ApprovalDecision.APPROVED,approver_id='finance-manager-0063',decided_at=NOW,expires_at=approval.expires_at,payload_hash=intent.payload_hash,write_intent_hash=intent.write_intent_hash,evidence_integrity='approval-signature:0063')
 resumed=await ApprovalResumeCoordinator().resume(token=ResumeToken(request.execution_id,intent.business_operation_id,suspension.checkpoint.checkpoint_version,proof),skill=skill,request=request,approval_id=intent.approval_id,write_node_id=intent.node_id,current_write_intent_hash=intent.write_intent_hash,current_payload_hash=intent.payload_hash,claim_id='claim-voucher-0063',checkpoint_port=lifecycle,verifier=h['_ApprovalVerifier'](),clock=RecordedApprovalClock(NOW+timedelta(seconds=1)))
 ctx=resumed.context;ce=h['_contract_evidence'](contract);pre=h['_business_precondition'](intent,contract);policy=h['_policy_decision'](intent,ctx,ce)
 provider_evidence=ProviderIdempotencyEvidence(contract.tool_id,contract.contract_hash,'PROVIDER_DEFINED','provider-idempotency:0063',NOW,NOW+timedelta(days=30),True)
 reservation=await IdempotencyCoordinator(h['_IdempotencyPort']()).reserve(intent=intent,context=ctx,contract=contract,provider_evidence=provider_evidence,now=NOW,required_until=NOW+timedelta(days=7))
 decision=WriteGuard().evaluate(WriteGuardRequest(intent,ctx,ce,h['_execution_evidence'](ctx),policy,pre),now=NOW)
 call=WriteCallRequestBuilder().build(intent=intent,context=ctx,contract_evidence=ce,reservation=reservation,approval_request_id='approval-voucher-0063',invocation_id='write-invocation-0063',business_preconditions=pre,trace_refs=('trace-voucher:0063',))
 events=[];requested=h['_WriteEvidencePort'](events);outcomes=h['_OutcomePort']()
 class Provider:
  calls=0
  async def execute(self,r):
   self.calls+=1
   if mode=='not-committed':return WriteNotCommitted(WriteOutcomeIdentity.from_request(r),'BUSINESS_REJECTED','provider confirmed no mutation')
   if mode=='unknown-commit':raise TimeoutError('synthetic uncertainty')
   return await h['_VoucherWritePort'](events).execute(r)
 provider=Provider();artifact,profile=h['_trust'](skill)
 commit=await TrustedCommitAwareWriteExecutor(TrustedWriteExecutionCoordinator(WriteExecutionCoordinator(provider,requested)),WriteOutcomeCoordinator(outcomes)).execute(artifact=artifact,profile=profile,trust_checked_at=NOW,request=call,decision=decision,context=ctx,requested_at=NOW,recorded_at=NOW+timedelta(seconds=2))
 class Read:
  calls=0
  async def read(self,r):
   self.calls+=1
   if mode=='verification-unknown':raise TimeoutError('synthetic READ timeout')
   return VerificationObservation(r.request_hash,r.business_reference,mode!='verification-failed','verification:0063',NOW+timedelta(seconds=3),None if mode!='verification-failed' else 'PROVIDER_STATE_MISMATCH')
 read=Read();verification=None
 if isinstance(commit.outcome,WriteCommitted):verification=await PostWriteVerifier().verify(commit.outcome.result,contract,ctx,read)
 result=await engine.complete_controlled_execution(context=ctx,approval_request_id='approval-voucher-0063',commit=commit,verification=verification,tools=tools,audit_sink=InMemoryAuditSink())
 if mode=='base':
  fixture=json.loads((h['EXAMPLE']/'fixtures/success.json').read_text())
  return dict(oracle=PIN,skill=NsoCodec.encode(skill).hex(),contracts=[c.to_canonical_data() for c in catalog._contracts.values()],request=dict(execution_id=request.execution_id,inputs=encode_value(request.inputs),context=encode_value(request.runtime_context),principal=plain(request.principal),business_operation_id=request.business_operation_id),reads=fixture,approval=plain(approval),proof=plain(proof),intent=plain(intent),precondition=plain(pre),policy=plain(policy),provider_evidence=plain(provider_evidence),prefix=prefix.result.to_data(),suspension=suspension.result.to_data(),call=plain(call),committed=plain(commit.outcome.result),now=NOW.isoformat(),required_until=(NOW+timedelta(days=7)).isoformat(),recorded_at=(NOW+timedelta(seconds=2)).isoformat(),observed_at=(NOW+timedelta(seconds=3)).isoformat())
 return dict(case=mode,result=result.to_data(),semantic=result.semantic_view(),write_calls=provider.calls,verification_calls=read.calls,read_calls=tools.call_count,journal_entries=len(ctx.writes.entries),automatic_retry_allowed=commit.automatic_retry_allowed,requested=[plain(r) for r in requested.records],outcomes=[plain(r) for r in outcomes.records])

if sys.argv[2]=='base':print(canonical_json(plain(asyncio.run(run('base')))).decode())
else:
 print('case\tjson_utf8_hex')
 for mode in ('verified','verification-failed','verification-unknown','not-committed','unknown-commit'):
  print(mode,canonical_json(plain(asyncio.run(run(mode)))).hex(),sep='\t')
