"""R7-S05 end-to-end fixed Oracle. argv[1]=archive, [2]=normal|exceptions.
Input actions + unmodified Oracle observations, in-memory wire persistence only.
"""
import sys,json,asyncio
from pathlib import Path
from dataclasses import replace
from datetime import timedelta
sys.path.insert(0,str(Path(sys.argv[1]).resolve()));sys.path.insert(1,str(Path(__file__).resolve().parent))
import generate_resumes as g
from nsl.approval import *
from nsl.checkpoints import ExecutionCheckpoint,CheckpointCoordinator,_state_payload
from nsl.core import *
from nsl.ir import canonical_json
from nsl.runtime_models import ExecutionContext,EmitRecord
from nsl.security import PrincipalVerification
class Ports:
 def __init__(self,case):self.case=case;self.events=[];self.slots={};self.latest={};self.published={};self.verify=True;self.now=g.NOW;self.verifier_calls=0;self.clock_calls=0;self.publish_failed=False
 async def save(self,cp):
  self.events.append('save');wire=cp.to_bytes();ExecutionCheckpoint.from_bytes(wire).verify();key=(cp.execution_id,cp.checkpoint_version)
  self.slots[key]=dict(wire=wire,state='AVAILABLE',owner=None);self.latest[cp.execution_id]=cp.checkpoint_version
 async def publish(self,req):
  self.events.append('publish')
  if self.case=='publish-retry' and not self.publish_failed:self.publish_failed=True;raise RuntimeError('private publication failure')
  prior=self.published.setdefault(req.approval_request_id,req)
  if prior!=req:raise RuntimeError('ApprovalRequestId collision')
 async def claim(self,execution,version,claim):
  self.events.append('claim');key=(execution,version)
  if key not in self.slots or self.latest.get(execution)!=version:raise ResumeClaimError('stale checkpoint version')
  slot=self.slots[key]
  if slot['state']=='LIVE':raise ResumeClaimError('checkpoint already has a live claim')
  if slot['state']=='CONSUMED':raise ResumeClaimError('checkpoint already consumed')
  cp=ExecutionCheckpoint.from_bytes(slot['wire']);slot['state']='LIVE';slot['owner']=claim
  if self.case=='returned-version':
   state=json.loads(cp.state_payload);state['checkpoint_version']=version+1;state['checkpoint']['checkpoint_version']=version+1
   cp=g.s.h.resign(cp,state,checkpoint_version=version+1)
  return cp
 async def release(self,*a):self.finish('release','AVAILABLE',*a)
 async def consume(self,*a):
  if self.case=='consume-before':self.events.append('consume');raise RuntimeError('private consumption failure')
  self.finish('consume','CONSUMED',*a)
  if self.case=='consume-after':raise RuntimeError('private consumption failure')
 def finish(self,op,state,execution,version,claim):
  self.events.append(op);slot=self.slots[(execution,version)]
  if slot['state']!='LIVE' or slot['owner']!=claim:raise ResumeClaimError('claim ownership mismatch')
  slot['state']=state;slot['owner']=None
 def view(self):return dict(events=list(self.events),slots=[dict(execution=k[0],version=k[1],wire=v['wire'].hex(),state=v['state'],owner=v['owner']) for k,v in self.slots.items()],latest=dict(self.latest),published=[v.to_data() for _,v in sorted(self.published.items())],verifier_calls=self.verifier_calls,clock_calls=self.clock_calls)
# Separate proof/time views prevent colliding method/attribute names.
class Proof:
 def __init__(self,p):self.p=p
 def verify(self,e):self.p.events.append('verify');self.p.verifier_calls+=1;return self.p.verify
class Clock:
 def __init__(self,p):self.p=p
 def now(self):self.p.events.append('clock');self.p.clock_calls+=1;return self.p.now
async def scenario(ver,case):
 ctx,req,ev=g.s.setup(ver,'pass')
 ctx.checkpoint.checkpoint_version=10**40 if case=='wide' else 0
 if case=='empty':ctx.checks.clear();ev=replace(ev,validation=g.s.ValidationSummary.from_checks(()))
 if case=='populated':
  v=ValueEnvelope(2026,INT,Presence.PRESENT,Completeness.COMPLETE,DataClassification.CONFIDENTIAL,('input:year',));t=ValueEnvelope('TEAM-FINANCE',STRING,Presence.PRESENT,Completeness.COMPLETE,DataClassification.INTERNAL,('context:team',))
  ctx.bind_input('sym-input',v);ctx.bind_context('sym-context',t);ctx.bind('sym-root',v);ctx.push_frame();ctx.bind('sym-inner',t);ctx.bind_check('check-inner',v);ctx.outputs.append(EmitRecord({'team':t,'year':v}));ctx.resources.tool_calls=1;ctx.resources.loop_iterations=1;ctx.resources.emitted_rows=1;ctx.resources.max_collection_size_seen=1;ctx.invocation_counter=3;ctx.foreach_depth=1;ctx.current_node_id='approval0001';ctx.checkpoint.last_completed_node_id='approval0001';ctx.checkpoint.trace_sequence=4
 if case in ('principal-unverified','principal-actor'):
  request=replace(ctx.request,principal=replace(ctx.request.principal,**({'verification':PrincipalVerification.UNVERIFIED} if case=='principal-unverified' else {'actor_type':'INVALID-ACTOR'})));old=ctx;ctx=ExecutionContext(ctx.skill,request);ctx.checks.extend(old.checks)
 p=Ports(case);clock=g.RuntimeClock();actions=[];observations=[];last=None
 def record(action,outcome):
  actions.append(action);observations.append(dict(outcome=outcome,store=p.view(),suspend_context=_state_payload(ctx,ctx.checkpoint.checkpoint_version).hex(),runtime_calls=clock.calls))
 async def suspend(request,remove=None):
  nonlocal last
  action=dict(op='suspend',request=request.to_data(),remove=remove)
  if remove:getattr(ctx.checkpoint,remove).pop(req.write_node_id,None)
  try:
   last=await ApprovalSuspensionCoordinator().suspend(ctx,request,p,p,ev);o=['ok',last.result.to_data(),last.result.semantic_view(),last.checkpoint.to_bytes().hex(),last.checkpoint.integrity_hash]
  except Exception as e:o=['error',type(e).__name__,str(e)]
  record(action,o);return o[0]=='ok'
 first_ok=await suspend(req)
 if not first_ok and case!='publish-retry':return dict(recipe=case,request=req.to_data(),actions=actions,observations=observations)
 if case in ('changed-payload','changed-intent','missing-payload','missing-intent'):
  changed=replace(req,**({'payload_hash':PayloadHash('sha256:'+'c'*64)} if case=='changed-payload' else {'write_intent_hash':'intent-b'} if case=='changed-intent' else {}))
  await suspend(changed,{'missing-payload':'payload_hashes_by_write_node','missing-intent':'write_intent_hashes_by_write_node'}.get(case));return dict(recipe=case,request=req.to_data(),actions=actions,observations=observations)
 old_version=ctx.checkpoint.checkpoint_version
 if case in ('suspend-retry','publish-retry'):await suspend(req)
 if case=='tamper-wire':
  slot=p.slots[(req.execution_id,ctx.checkpoint.checkpoint_version)];wire=json.loads(slot['wire']);wire['integrity_hash']='sha256:'+'0'*64;slot['wire']=canonical_json(wire)
 evidence=ApprovalEvidence(req.tenant_id,req.execution_id,req.business_operation_id,req.approval_id,req.approval_request_id,req.skill_semantic_hash,ApprovalDecision.APPROVED,'approver-a',g.NOW,g.NOW+timedelta(hours=1),req.payload_hash,req.write_intent_hash,'opaque-signed-proof')
 if case.startswith('evidence-'):
  field=case[9:];evidence=replace(evidence,**{field:PayloadHash('sha256:'+'d'*64) if field in ('skill_semantic_hash','payload_hash') else 'other'})
 if case=='rejected':evidence=replace(evidence,decision=ApprovalDecision.REJECTED)
 if case=='no-expiry':evidence=replace(evidence,expires_at=None)
 now=g.NOW
 if case in ('before-expiry','at-expiry','after-expiry'):now=evidence.expires_at+timedelta(microseconds={'before-expiry':-1,'at-expiry':0,'after-expiry':1}[case])
 async def resume(version,*,valid=True,proof=evidence):
  request=ctx.request
  if case in ('principal-subject','principal-scopes'):request=replace(request,principal=replace(request.principal,**({'subject_id':'other'} if case=='principal-subject' else {'scopes':frozenset({'other'})})))
  action=dict(op='resume',version=version,evidence=proof.to_data(),verify=valid,now=now.isoformat(),current_intent='changed-intent' if case=='current-intent' else req.write_intent_hash,current_payload='sha256:'+'e'*64 if case=='current-payload' else req.payload_hash,tamper=case=='tamper-wire')
  p.verify=valid;p.now=now
  try:
   o=await ApprovalResumeCoordinator().resume(token=ResumeToken(req.execution_id,req.business_operation_id,version,proof),skill=ctx.skill,request=request,approval_id=req.approval_id,write_node_id=req.write_node_id,current_write_intent_hash=action['current_intent'],current_payload_hash=action['current_payload'],claim_id='claim-a',checkpoint_port=p,verifier=Proof(p),clock=Clock(p),runtime_clock=clock)
   v=o.validation;result=['ok',dict(validation=v.validation.value,approval_status=v.approval_status.value,reason_code=v.reason_code),v.approval_gate_passed,v.requires_reapproval,o.claim_consumed,_state_payload(o.context,o.context.checkpoint.checkpoint_version).hex(),o.checkpoint.to_bytes().hex()]
  except Exception as e:result=['error',type(e).__name__,str(e)]
  record(action,result)
 if case in ('suspend-retry','publish-retry'):await resume(old_version)
 await resume(ctx.checkpoint.checkpoint_version,valid=case!='proof-retry')
 if case=='proof-retry':await resume(ctx.checkpoint.checkpoint_version)
 if case in ('approved','populated','wide','rejected','at-expiry','after-expiry','suspend-retry','publish-retry','proof-retry','consume-before','consume-after'):await resume(ctx.checkpoint.checkpoint_version)
 return dict(recipe=case,request=req.to_data(),actions=actions,observations=observations)
async def main():
 print('# Python Oracle 28a949ec96581cbdc52c8133527b23bb5b72ee0a')
 cases=['approved','empty','populated','wide','rejected','before-expiry','at-expiry','after-expiry','no-expiry','proof-retry','current-intent','current-payload','principal-subject','principal-scopes','suspend-retry','publish-retry','consume-before','consume-after','tamper-wire']+['evidence-'+f for f in ('tenant_id','execution_id','business_operation_id','approval_id','approval_request_id','skill_semantic_hash','payload_hash','write_intent_hash')] if sys.argv[2]=='normal' else ['changed-payload','changed-intent','missing-payload','missing-intent','principal-unverified','principal-actor','returned-version']
 for ver in ('1-0','1-1'):
  for case in cases:print(ver,case,canonical_json(await scenario(ver,case)).hex(),sep='\t')
if __name__=='__main__':asyncio.run(main())
