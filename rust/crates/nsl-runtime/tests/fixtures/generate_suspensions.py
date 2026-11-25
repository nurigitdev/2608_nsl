"""R7-S03 fixed, unmodified Oracle observations; argv[1]=archive, [2]=normal|exceptions.
Only in-memory ports; original exception outcomes are retained without rewriting.
"""
import asyncio,json,sys
from pathlib import Path
from dataclasses import replace
sys.path.insert(0,str(Path(sys.argv[1]).resolve()))
sys.path.insert(1,str(Path(__file__).resolve().parent))
import generate_checkpoints as h
from nsl.approval import ApprovalRequest,ApprovalSuspensionCoordinator
from nsl.checkpoints import _state_payload
from nsl.core import *
from nsl.runtime_models import CheckResult,ValidationSummary
from nsl.validation import PreWriteEvidence
from nsl.ir import canonical_json
CONTROLS=('runtime_preflight_passed','trust_valid','current_policy_allowed','tool_preconditions_passed','business_preconditions_passed','intent_binding_valid','idempotency_ready','durability_ready','replay_live_write_allowed')
class Ports:
 def __init__(self,mode):self.events=[];self.saved=[];self.published={};self.calls=0;self.mode=mode
 async def save(self,cp):
  self.events.append('save')
  if self.mode=='save-before':self.mode='ok';raise RuntimeError('synthetic private transport')
  cp.verify();self.saved.append(cp)
  if self.mode=='save-after':self.mode='ok';raise RuntimeError('synthetic private transport')
 async def publish(self,req):
  self.events.append('publish');self.calls+=1
  if self.mode=='publish-before':self.mode='ok';raise RuntimeError('synthetic private transport')
  previous=self.published.setdefault(req.approval_request_id,req)
  if previous!=req:raise RuntimeError('ApprovalRequestId collision')
  if self.mode=='publish-after':self.mode='ok';raise RuntimeError('synthetic private transport')
def setup(ver,case):
 ctx=h.populated(ver,'safe-values' if case=='populated' else 'empty',10**40 if case=='wide' else 0)
 if case!='populated':ctx.checks.append(CheckResult('rule',CheckStatus.PASS,'ERROR','reviewed','condition',Presence.PRESENT,Completeness.COMPLETE,(),None))
 if case not in ('partial','unknown'):ctx.data_completeness=Completeness.COMPLETE
 if case=='partial':ctx.data_completeness=Completeness.PARTIAL
 if case=='unknown':ctx.data_completeness=Completeness.UNKNOWN
 if case=='no-check':ctx.checks.clear()
 if case in ('fail','check-unknown'):ctx.checks[0]=replace(ctx.checks[0],status=CheckStatus.FAIL if case=='fail' else CheckStatus.UNKNOWN)
 ev=PreWriteEvidence(ValidationSummary.from_checks(tuple(ctx.checks)),**dict.fromkeys(CONTROLS,True))
 if case=='duplicate-check':ctx.checks.append(ctx.checks[0])
 if case=='validation-mismatch':ev=replace(ev,validation=ValidationSummary.from_checks(()))
 if case=='missing-evidence':ev=None
 if case.startswith('control-'):ev=replace(ev,**{case[8:]:False})
 if case=='defaults':ev=PreWriteEvidence(ev.validation)
 if case.startswith('approval-'):ev=replace(ev,approval_status=ApprovalStatus(case[9:]))
 if case=='approved-valid':ev=replace(ev,approval_status=ApprovalStatus.APPROVED,approval_evidence_valid=True)
 if case=='evidence-valid':ev=replace(ev,approval_evidence_valid=True)
 if case=='legacy-empty':ctx.checks.clear();ev=None
 req=ApprovalRequest(ctx.tenant_id,ctx.identity.execution_id,ctx.identity.business_operation_id,'PROJECT_DEACTIVATE',ApprovalRequestId('request-a'),ctx.skill.skill_id,ctx.skill.skill_version,ctx.skill.semantic_hash,'write0001','project.deactivate',PayloadHash('sha256:'+'b'*64),'intent-a',RiskLevel.R2,'Review draft',None,{'line_count':1},None,DataClassification.INTERNAL)
 if case.startswith('binding-'):
  name=case[8:];req=replace(req,**{name:PayloadHash('sha256:'+'d'*64) if name=='skill_semantic_hash' else 'other'})
 if case=='prior-id':ctx.checkpoint.approval_request_ids_by_write_node[req.write_node_id]=ApprovalRequestId('other')
 return ctx,req,ev
async def scenario(ver,case):
 ctx,req,ev=setup(ver,case);p=Ports(case);coord=ApprovalSuspensionCoordinator();rows=[]
 attempts=2 if case in ('retry','save-before','save-after','publish-before','publish-after') or case.startswith(('changed-','missing-')) and case!='missing-evidence' else 1
 for i in range(attempts):
  if i and case=='changed-payload':req=replace(req,payload_hash=PayloadHash('sha256:'+'c'*64))
  if i and case=='changed-intent':req=replace(req,write_intent_hash='intent-b')
  if i and case in ('missing-payload','missing-intent'):
   target=ctx.checkpoint.payload_hashes_by_write_node if case=='missing-payload' else ctx.checkpoint.write_intent_hashes_by_write_node
   del target[req.write_node_id]
  try:
   s=await coord.suspend(ctx,req,p,p,ev);outcome=['ok',s.result.to_data(),s.result.semantic_view(),s.checkpoint.to_bytes().hex()]
  except Exception as e:outcome=['error',type(e).__name__,str(e)]
  rows.append(dict(outcome=outcome,events=list(p.events),saved=[c.to_bytes().hex() for c in p.saved],published=[r.to_data() for _,r in sorted(p.published.items())],publish_calls=p.calls,version=ctx.checkpoint.checkpoint_version,published_flag=ctx.checkpoint.approval_request_published,state=_state_payload(ctx,ctx.checkpoint.checkpoint_version).hex()))
 return rows
async def main():
 print('# Python Oracle 28a949ec96581cbdc52c8133527b23bb5b72ee0a')
 normal=['pass','populated','wide','retry','save-before','save-after','publish-before','publish-after','missing-evidence','defaults','no-check','fail','check-unknown','partial','unknown','duplicate-check','validation-mismatch','prior-id','evidence-valid','approved-valid']+['control-'+s for s in CONTROLS]+['approval-'+s.value for s in ApprovalStatus]+['binding-'+s for s in ('tenant_id','execution_id','business_operation_id','skill_id','skill_version','skill_semantic_hash')]
 cases=normal if sys.argv[2]=='normal' else ['changed-payload','changed-intent','missing-payload','missing-intent']
 for ver in ('1-0','1-1'):
  for case in cases+(['legacy-empty'] if ver=='1-0' and sys.argv[2]=='normal' else []):print(ver,case,canonical_json(await scenario(ver,case)).hex(),sep='\t')
if __name__=='__main__':asyncio.run(main())
