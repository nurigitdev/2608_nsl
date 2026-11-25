"""R7-S04 unmodified fixed Oracle; argv[1]=archive, [2]=normal|exceptions.
Synthetic in-memory atomic claim adapter only. Original observations are retained.
"""
import sys,json,asyncio
from pathlib import Path
from dataclasses import replace
from datetime import datetime,UTC,timedelta
sys.path.insert(0,str(Path(sys.argv[1]).resolve()))
sys.path.insert(1,str(Path(__file__).resolve().parent))
import generate_suspensions as s
from nsl.approval import *
from nsl.checkpoints import CheckpointCoordinator,_state_payload,ExecutionCheckpoint
from nsl.core import *
from nsl.runtime_models import ExecutionContext
from nsl.security import PrincipalVerification
from nsl.ir import canonical_json
NOW=datetime(2026,10,20,1,2,3,tzinfo=UTC)
class Ports:
 def __init__(self,cp,case):self.cp=cp;self.events=[];self.state='AVAILABLE';self.owner=None;self.latest=cp.checkpoint_version;self.case=case;self.returned=cp
 async def claim(self,execution_id,version,claim_id):
  self.events.append('claim')
  if self.case=='claim-before':raise RuntimeError('private claim transport')
  if (execution_id,version)!=(self.cp.execution_id,self.cp.checkpoint_version) or version!=self.latest:raise ResumeClaimError('stale checkpoint version')
  if self.state=='LIVE':raise ResumeClaimError('checkpoint already has a live claim')
  if self.state=='CONSUMED':raise ResumeClaimError('checkpoint already consumed')
  self.state='LIVE';self.owner=claim_id
  if self.case=='claim-after':raise RuntimeError('private claim transport')
  return self.returned
 async def release(self,*args):await self.finish('release','AVAILABLE',*args)
 async def consume(self,*args):await self.finish('consume','CONSUMED',*args)
 async def finish(self,op,target,execution_id,version,claim_id):
  self.events.append(op)
  if self.case==op+'-before' or self.case=='error-'+op+'-before':raise RuntimeError('private cleanup transport')
  if (execution_id,version)!=(self.cp.execution_id,self.cp.checkpoint_version) or self.state!='LIVE' or self.owner!=claim_id:raise ResumeClaimError('claim ownership mismatch')
  self.state=target;self.owner=None
  if self.case==op+'-after' or self.case=='error-'+op+'-after':raise RuntimeError('private cleanup transport')
class Verifier:
 def __init__(self,p,mode):self.p=p;self.mode=mode;self.calls=0
 def verify(self,evidence):
  self.calls+=1;self.p.events.append('verify')
  if self.mode=='error':self.mode='true';raise OSError('private verifier transport')
  return self.mode!='false'
class Clock:
 def __init__(self,p,value,failed):self.p=p;self.value=value;self.failed=failed;self.calls=0
 def now(self):
  self.calls+=1;self.p.events.append('clock')
  if self.failed:self.failed=False;raise OSError('private clock transport')
  return self.value
class RuntimeClock:
 def __init__(self):self.calls=0
 def monotonic_ns(self):self.calls+=1;return 1_000_000_000
async def scenario(ver,case):
 ctx,req,ev=s.setup(ver,'populated' if case=='populated' else 'pass')
 if case in ('principal-unverified','principal-actor'):
  principal=replace(ctx.request.principal,**({'verification':PrincipalVerification.UNVERIFIED} if case=='principal-unverified' else {'actor_type':'INVALID-ACTOR'}))
  ctx=ExecutionContext(ctx.skill,replace(ctx.request,principal=principal))
  # Legacy component accepts no checks; v0.2 needs the approved pre-WRITE facts.
  fresh,_,_=s.setup(ver,'pass');ctx.checks.extend(fresh.checks);ev=replace(ev,validation=s.ValidationSummary.from_checks(tuple(ctx.checks)))
  req=replace(req,tenant_id=ctx.tenant_id)
 ports=s.Ports('ok');sus=await ApprovalSuspensionCoordinator().suspend(ctx,req,ports,ports,ev)
 cp=sus.checkpoint;state=json.loads(cp.state_payload)
 if case.startswith('pending-'):state['approvals'][req.approval_id]=case[8:]
 if case=='missing-approval':state['approvals'].clear()
 if case.startswith('missing-') and case!='missing-approval':
  fields={'missing-request':'approval_request_ids_by_write_node','missing-intent':'write_intent_hashes_by_write_node','missing-payload':'payload_hashes_by_write_node'}
  state['checkpoint'][fields[case]].clear()
 if case=='bad-payload':state['checkpoint']['payload_hashes_by_write_node'][req.write_node_id]='invalid'
 if case=='blank-intent':state['checkpoint']['write_intent_hashes_by_write_node'][req.write_node_id]=' '
 if case.startswith(('pending-','missing-')) or case in ('bad-payload','blank-intent'):cp=s.h.resign(cp,state)
 p=Ports(cp,case);request=ctx.request
 if case.startswith('principal-change-'):
  field=case[17:];values={'tenant_id':'other','subject_id':'other','actor_type':'SERVICE','roles':frozenset({'other'}),'scopes':frozenset({'other'}),'auth_context_ref':'other','on_behalf_of':'other'}
  request=replace(request,principal=replace(request.principal,**{field:values[field]}))
 if case=='policy-change':request=replace(request,data_policy=s.h.DataHandlingPolicy(snapshot_retention_days=31))
 evidence=ApprovalEvidence(req.tenant_id,req.execution_id,req.business_operation_id,req.approval_id,req.approval_request_id,req.skill_semantic_hash,ApprovalDecision.APPROVED,'approver-a',NOW,NOW+timedelta(hours=1),req.payload_hash,req.write_intent_hash,'opaque-signed-proof')
 if case.startswith('evidence-'):
  field=case[9:];evidence=replace(evidence,**{field:PayloadHash('sha256:'+'c'*64) if field in ('skill_semantic_hash','payload_hash') else 'other'})
 if case=='priority':evidence=replace(evidence,business_operation_id='other',approval_request_id='other',write_intent_hash='other',payload_hash=PayloadHash('sha256:'+'c'*64))
 if case in ('rejected','expired-rejected'):evidence=replace(evidence,decision=ApprovalDecision.REJECTED)
 if case=='no-expiry':evidence=replace(evidence,expires_at=None)
 now=NOW
 if case in ('before-expiry','at-expiry','after-expiry','expired-rejected'):now=evidence.expires_at+timedelta(microseconds={'before-expiry':-1,'at-expiry':0,'after-expiry':1,'expired-rejected':0}[case])
 if case=='far-future':now=datetime.max.replace(tzinfo=UTC);evidence=replace(evidence,expires_at=None)
 token=ResumeToken('other' if case=='token-execution' else req.execution_id,'other' if case=='token-business' else req.business_operation_id,cp.checkpoint_version,evidence)
 if case=='stale':p.latest=cp.checkpoint_version+1
 if case=='live':p.state='LIVE';p.owner='other-claim'
 if case=='consumed':p.state='CONSUMED'
 if case=='returned-version':
  state=json.loads(cp.state_payload);state['checkpoint_version']=2;state['checkpoint']['checkpoint_version']=2;p.returned=s.h.resign(cp,state,checkpoint_version=2)
 if case in ('returned-execution','returned-business'):p.returned=s.h.resign(cp,**{'execution_id' if case=='returned-execution' else 'business_operation_id':'other'})
 mode='error' if case in ('verifier-error','error-release-before','error-release-after') else ('false' if case in ('invalid-proof','proof-retry','release-before','release-after') else 'true')
 verifier=Verifier(p,mode);clock=Clock(p,now,case=='clock-error');runtime_clock=RuntimeClock()
 current_intent='changed-intent' if case=='current-intent' else req.write_intent_hash
 current_payload=PayloadHash('sha256:'+'d'*64) if case=='current-payload' else req.payload_hash
 approval_id='' if case=='blank-approval' else req.approval_id
 write_node='' if case=='blank-node' else req.write_node_id
 claim_id='' if case=='blank-claim' else 'claim-a'
 if case=='blank-current-intent':current_intent=' '
 input=dict(checkpoint=cp.to_bytes().hex(),token_execution=token.execution_id,token_business=token.business_operation_id,token_version=token.checkpoint_version,evidence=evidence.to_data(),approval_id=approval_id,write_node_id=write_node,current_intent=current_intent,current_payload=current_payload,claim_id=claim_id,now=now.isoformat())
 rows=[]
 for attempt in range(2 if case in ('duplicate','proof-retry','verifier-error','clock-error','consume-before','consume-after') else 1):
  if attempt and case=='proof-retry':verifier.mode='true'
  try:
   o=await ApprovalResumeCoordinator().resume(token=token,skill=ctx.skill,request=request,approval_id=approval_id,write_node_id=write_node,current_write_intent_hash=current_intent,current_payload_hash=current_payload,claim_id=claim_id,checkpoint_port=p,verifier=verifier,clock=clock,runtime_clock=runtime_clock)
   v=o.validation
   result=['ok',dict(validation=v.validation.value,approval_status=v.approval_status.value,reason_code=v.reason_code),v.approval_gate_passed,v.requires_reapproval,o.claim_consumed,_state_payload(o.context,o.context.checkpoint.checkpoint_version).hex(),o.checkpoint.to_bytes().hex(),o.claim_id]
  except Exception as e:result=['error',type(e).__name__,str(e)]
  rows.append(dict(outcome=result,events=list(p.events),store_state=p.state,owner=p.owner,verifier_calls=verifier.calls,clock_calls=clock.calls,runtime_calls=runtime_clock.calls))
 return dict(input=input,observations=rows)
async def main():
 print('# Python Oracle 28a949ec96581cbdc52c8133527b23bb5b72ee0a')
 normal=['pass','populated','duplicate','invalid-proof','proof-retry','verifier-error','clock-error','priority','rejected','no-expiry','before-expiry','at-expiry','after-expiry','expired-rejected','far-future','current-intent','current-payload','token-execution','token-business','stale','live','consumed','missing-approval','missing-request','missing-intent','missing-payload','bad-payload','blank-intent','blank-approval','blank-node','blank-claim','blank-current-intent','returned-execution','returned-business','policy-change','claim-before','claim-after','release-before','release-after','error-release-before','error-release-after','consume-before','consume-after']+['evidence-'+f for f in ('tenant_id','execution_id','business_operation_id','approval_id','approval_request_id','skill_semantic_hash','payload_hash','write_intent_hash')]+['principal-change-'+f for f in ('tenant_id','subject_id','actor_type','roles','scopes','auth_context_ref','on_behalf_of')]+['pending-'+status.value for status in ApprovalStatus if status is not ApprovalStatus.PENDING]
 cases=normal if sys.argv[2]=='normal' else ['principal-unverified','principal-actor','returned-version']
 for ver in ('1-0','1-1'):
  for case in cases:
   result=await scenario(ver,case)
   print(ver,case,canonical_json(result).hex(),sep='\t')
if __name__=='__main__':asyncio.run(main())
