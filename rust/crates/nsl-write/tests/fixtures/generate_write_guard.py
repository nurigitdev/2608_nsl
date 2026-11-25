"""R8-S03 frozen observations, Python 3.12 -I -B ARCHIVE models|decisions|prewrite|exceptions.
No provider I/O; original outcomes and native admission exceptions stay distinct.
"""
import sys,runpy,dataclasses
from pathlib import Path
from datetime import datetime,timedelta,UTC
sys.path.insert(0,str(Path(sys.argv[1]).resolve()))
from nsl.validation import *
from nsl.runtime_models import ExecutionContext,ValidationSummary
from nsl.security import PrincipalVerification
from nsl.core import *
from nsl.ir import canonical_json
h=runpy.run_path(str(Path(sys.argv[1])/'tests/test_v0_2_write_guard.py'))
NOW=h['NOW'];US=timedelta(microseconds=1)
PIN='28a949ec96581cbdc52c8133527b23bb5b72ee0a'

def plain(v):
 if dataclasses.is_dataclass(v):return {f.name:plain(getattr(v,f.name)) for f in dataclasses.fields(v)}
 if isinstance(v,datetime):return v.isoformat()
 if isinstance(v,dict):return {k:plain(x) for k,x in v.items()}
 if isinstance(v,(tuple,list)):return [plain(x) for x in v]
 if isinstance(v,(set,frozenset)):return sorted(plain(x) for x in v)
 return v

def observe(fn):
 try:return ['ok',plain(fn())]
 except Exception as e:return ['error',type(e).__name__,str(e)]

def emit(name,inputs,fn):print(name,canonical_json(plain(inputs)).hex(),canonical_json(observe(fn)).hex(),sep='\t')

def fixture():
 i=h['_intent']();ctx=h['_context']();c=h['_contract']()
 ctx=ExecutionContext(ctx.skill,dataclasses.replace(ctx.request,principal=dataclasses.replace(ctx.request.principal,scopes=ctx.request.principal.scopes|frozenset({c.required_scope}))))
 return h['_request'](intent=i,context=ctx,contract=c),c

def execution(e):return {**plain(e),'validation':e.validation.status.value}

def inputs(r,c,**extras):
 return dict(context=dict(principal=plain(r.context.request.principal),business_operation_id=r.context.identity.business_operation_id),current_contract=dict(tool_id=c.tool_id,version=c.version,capability=c.capability,required_scope=c.required_scope,risk_level=c.risk_level),intent=dict(tenant_id=r.intent.tenant_id,business_operation_id=r.intent.business_operation_id,node_id=r.intent.node_id,tool_id=r.intent.tool_id),contract_evidence=plain(r.contract_evidence),policy_decision=plain(r.policy_decision),business_preconditions=plain(r.business_preconditions),execution=execution(r.execution_evidence),now=NOW.isoformat(),**extras)

def parse_model(kind,d):
 d=dict(d)
 if kind=='decision':return WriteGuardDecision(PreWriteEligibility(d['eligibility']),d['reason_code'])
 for k in ('evaluated_at','expires_at','observed_at'):
  if k in d:d[k]=datetime.fromisoformat(d[k])
 if 'risk_level' in d:d['risk_level']=RiskLevel(d['risk_level'])
 if kind=='contract':d['capability']=ToolCapability(d['capability']);return WriteContractEvidence(**d)
 if kind=='request':return WritePolicyRequest(**d)
 if kind=='policy':d['effect']=PolicyEffect(d['effect']);return PolicyDecision(**d)
 d['mechanism']=BusinessPreconditionMechanism(d['mechanism']);return BusinessPreconditionEvidence(**d)

def main():
 print('# Python Oracle '+PIN);mode=sys.argv[2];r,c=fixture()
 if mode=='models':
  bases=dict(contract=plain(r.contract_evidence),request=plain(write_policy_request(r.intent,r.context,r.contract_evidence)),policy=plain(r.policy_decision),precondition=plain(r.business_preconditions),decision=plain(WriteGuardDecision(PreWriteEligibility.ALLOWED)))
  enums={'effect','risk_level','capability','mechanism','eligibility'};times={'evaluated_at','expires_at','observed_at'}
  for kind,base in bases.items():
   emit(kind+'-base',dict(kind=kind,value=base),lambda k=kind,d=base:parse_model(k,d))
   for field,value in base.items():
    if field in enums|times or isinstance(value,bool):continue
    values=('', ' ', '\x1c', 'Bearer synthetic-only')
    if field=='action':values+=('READ',)
    if field.endswith('hash'):values+=('sha256:'+'A'*64,'sha256:'+'a'*63,'sha256:'+'a'*65)
    for val in values:
     d={**base,field:val};emit(kind+'-'+field+'-'+repr(val),dict(kind=kind,value=d),lambda k=kind,d=d:parse_model(k,d))
   if kind in ('policy','precondition'):
    t=base['evaluated_at' if kind=='policy' else 'observed_at']
    for n in (-1,0,1):
     d={**base,'expires_at':(datetime.fromisoformat(t)+n*US).isoformat()};emit(kind+'-expiry-'+str(n),dict(kind=kind,value=d),lambda k=kind,d=d:parse_model(k,d))
    for value in (datetime.min.replace(tzinfo=UTC).isoformat(),datetime.max.replace(tzinfo=UTC).isoformat()):
     d={**base,('evaluated_at' if kind=='policy' else 'observed_at'):value};emit(kind+'-instant-'+value,dict(kind=kind,value=d),lambda k=kind,d=d:parse_model(k,d))
  for level in RiskLevel:
   d={**bases['request'],'risk_level':level};emit('request-risk-'+level,dict(kind='request',value=d),lambda d=d:parse_model('request',d))
  return
 if mode=='prewrite':
  cases=[('base',r.execution_evidence)]
  for status,checks in [('no-check',()),('all-pass',(CheckStatus.PASS,)),('fail',(CheckStatus.FAIL,)),('unknown',(CheckStatus.UNKNOWN,)),('both',(CheckStatus.FAIL,CheckStatus.UNKNOWN))]:
   from nsl.runtime_models import CheckResult
   from nsl.core import Presence,Completeness
   results=tuple(CheckResult(check_id=f'C{idx}',status=v,severity='ERROR',message='test',condition_node_id=f'n{idx}',presence=Presence.PRESENT,completeness=Completeness.COMPLETE,provenance_refs=(),reason_code=None) for idx,v in enumerate(checks))
   cases.append((status,dataclasses.replace(r.execution_evidence,validation=ValidationSummary.from_checks(results),current_policy_allowed=True,tool_preconditions_passed=True,business_preconditions_passed=True)))
  base=dataclasses.replace(r.execution_evidence,current_policy_allowed=True,tool_preconditions_passed=True,business_preconditions_passed=True)
  cases.append(('all-controls',base))
  for field in ('runtime_preflight_passed','trust_valid','current_policy_allowed','tool_preconditions_passed','business_preconditions_passed','intent_binding_valid','idempotency_ready','durability_ready','replay_live_write_allowed'):
   cases.append((field,dataclasses.replace(base,**{field:False})))
  for status in ApprovalStatus:
   for valid in (False,True):cases.append((status+'-'+str(valid),dataclasses.replace(base,approval_status=status,approval_evidence_valid=valid)))
  for name,e in cases:emit(name,execution(e),lambda e=e:PreWriteEvaluator().evaluate(e))
  return
 if mode=='decisions':
  cases=[('base',r,c,None)]
  def add(name,request,contract=c,levels=None):cases.append((name,request,contract,levels))
  for field,value in [('tenant_id','other'),('business_operation_id','other')]:
   ctx=h['_context'](**{field:value});ctx=ExecutionContext(ctx.skill,dataclasses.replace(ctx.request,principal=dataclasses.replace(ctx.request.principal,scopes=r.context.request.principal.scopes)));add('context-'+field,dataclasses.replace(r,context=ctx))
  add('context-missing-business',dataclasses.replace(r,context=ExecutionContext(r.context.skill,dataclasses.replace(r.context.request,business_operation_id=None))))
  for name,changes in [('tool',dict(tool_id='OTHER.TOOL')),('read',dict(capability=ToolCapability.READ)),('transaction',dict(capability=ToolCapability.TRANSACTION)),('no-precondition',dict(preconditions_supported=False))]:add('contract-'+name,dataclasses.replace(r,contract_evidence=dataclasses.replace(r.contract_evidence,**changes)))
  for field,value in [('tenant_id','other'),('subject_id','other'),('tool_id','OTHER.TOOL'),('risk_level',RiskLevel.R4),('business_operation_id','other'),('write_node_id','write9999'),('write_intent_hash','sha256:'+'f'*64)]:add('policy-binding-'+field,dataclasses.replace(r,policy_decision=dataclasses.replace(r.policy_decision,**{field:value})))
  add('policy-missing',dataclasses.replace(r,policy_decision=None));add('policy-deny',dataclasses.replace(r,policy_decision=dataclasses.replace(r.policy_decision,effect=PolicyEffect.DENY)))
  for field in ('evaluated_at','expires_at'):
   for n in (-1,0,1):add('policy-'+field+'-'+str(n),dataclasses.replace(r,policy_decision=dataclasses.replace(r.policy_decision,**{field:NOW+n*US})))
  add('precondition-missing',dataclasses.replace(r,business_preconditions=None))
  for field,value in [('tenant_id','other'),('business_operation_id','other'),('write_node_id','write9999'),('tool_id','OTHER.TOOL'),('contract_hash','sha256:'+'f'*64),('payload_hash','sha256:'+'f'*64),('write_intent_hash','sha256:'+'f'*64)]:add('precondition-binding-'+field,dataclasses.replace(r,business_preconditions=dataclasses.replace(r.business_preconditions,**{field:value})))
  for field in ('observed_at','expires_at'):
   for n in (-1,0,1):add('precondition-'+field+'-'+str(n),dataclasses.replace(r,business_preconditions=dataclasses.replace(r.business_preconditions,**{field:NOW+n*US})))
  add('non-atomic',dataclasses.replace(r,business_preconditions=dataclasses.replace(r.business_preconditions,provider_atomic_enforcement=False)))
  for mechanism in BusinessPreconditionMechanism:add('mechanism-'+mechanism,dataclasses.replace(r,business_preconditions=dataclasses.replace(r.business_preconditions,mechanism=mechanism)))
  for level in RiskLevel:
   cc=h['_contract'](risk_level=level);rr=h['_request'](intent=r.intent,context=r.context,contract=cc);add('risk-'+level,rr,cc)
   add('custom-risk-'+level,rr,cc,list(RiskLevel))
  for field in ('runtime_preflight_passed','trust_valid','intent_binding_valid','idempotency_ready','durability_ready','replay_live_write_allowed','approval_evidence_valid'):
   add('execution-'+field,dataclasses.replace(r,execution_evidence=dataclasses.replace(r.execution_evidence,**{field:False})))
  for status in ApprovalStatus:add('approval-'+status,dataclasses.replace(r,execution_evidence=dataclasses.replace(r.execution_evidence,approval_status=status)))
  add('policy-before-precondition',dataclasses.replace(r,policy_decision=None,business_preconditions=None))
  add('binding-before-deny',dataclasses.replace(r,policy_decision=dataclasses.replace(r.policy_decision,subject_id='other',effect=PolicyEffect.DENY)))
  for name,rr,cc,levels in cases:emit(name,inputs(rr,cc,accepted_levels=levels),lambda rr=rr,levels=levels:WriteGuard(None if levels is None else RiskAcceptancePolicy(frozenset(levels))).evaluate(rr,now=NOW))
  return
 if mode=='exceptions':
  def exception(name,rr,cc,native):emit(name,inputs(rr,cc,native=native),lambda:WriteGuard().evaluate(rr,now=NOW))
  blocked=lambda reason:dict(eligibility='BLOCKED',reason_code=reason)
  principal=lambda field:dict(error='Principal',path='$.context.principal.'+field)
  for name,changes,native in [('missing-write-scope',dict(scopes=frozenset({'nsl:skill:execute'})),blocked('WRITE_SCOPE_DENIED')),('empty-scopes',dict(scopes=frozenset()),blocked('WRITE_SCOPE_DENIED')),('unverified',dict(verification=PrincipalVerification.UNVERIFIED),principal('verification')),('actor',dict(actor_type='AGENT'),principal('actor_type')),('auth-context',dict(auth_context_ref=''),principal('auth_context_ref')),('blank-role',dict(roles=frozenset({' '})),principal('roles')),('blank-scope',dict(scopes=frozenset({' '})),principal('scopes')),('on-behalf',dict(on_behalf_of=''),principal('on_behalf_of'))]:
   p=dataclasses.replace(r.context.request.principal,**changes);ctx=ExecutionContext(r.context.skill,dataclasses.replace(r.context.request,principal=p));exception(name,dataclasses.replace(r,context=ctx),c,native)
  for field,val in [('tool_id','OTHER.TOOL'),('risk_level',RiskLevel.R4),('required_scope','other:scope'),('version','1.0.1')]:exception('current-contract-'+field,r,dataclasses.replace(c,**{field:val}),blocked('WRITE_CONTRACT_INVALID'))
  for scope in ('',' '):
   cc=dataclasses.replace(c,required_scope=scope);ce=h['_contract_evidence'](cc);pre=h['_precondition'](r.intent,r.context,cc);rr=dataclasses.replace(r,contract_evidence=ce,business_preconditions=pre);exception('blank-required-scope-'+repr(scope),rr,cc,blocked('WRITE_SCOPE_DENIED'))
  for changes in [dict(contract_hash='sha256:'+'f'*64),dict(risk_level=RiskLevel.R3)]:
   rr=dataclasses.replace(r,contract_evidence=dataclasses.replace(r.contract_evidence,**changes),business_preconditions=dataclasses.replace(r.business_preconditions,contract_hash=changes.get('contract_hash',r.business_preconditions.contract_hash)))
   cc=c if 'contract_hash' in changes else h['_contract'](risk_level=RiskLevel.R4)
   exception('self-consistent-stale-'+next(iter(changes)),rr,cc,blocked('WRITE_CONTRACT_INVALID'))
  return
 raise ValueError(mode)
main()
