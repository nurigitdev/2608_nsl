"""R8-S04 original fixed-archive observations. Python 3.12 -I -B ARCHIVE MODE.
Modes: models|build|records|durability|dispositions. Only synthetic local ports.
"""
import sys,runpy,dataclasses,asyncio
from pathlib import Path
from datetime import datetime,timedelta,UTC
from collections.abc import Mapping
sys.path.insert(0,str(Path(sys.argv[1]).resolve()))
from nsl.write_execution import *
from nsl.idempotency import *
from nsl.validation import WriteGuardDecision,BusinessPreconditionEvidence,BusinessPreconditionMechanism
from nsl.core import *
from nsl.ir import canonical_json,encode_value
h=runpy.run_path(str(Path(sys.argv[1])/'tests/test_v0_2_write_execution.py'))
NOW=h['NOW'];US=timedelta(microseconds=1);PIN='28a949ec96581cbdc52c8133527b23bb5b72ee0a'
def thaw(v):
 if isinstance(v,Mapping):return {k:thaw(x) for k,x in v.items()}
 if isinstance(v,(list,tuple)):return [thaw(x) for x in v]
 return v

def plain(v):
 if isinstance(v,ValueEnvelope):return dict(value=encode_value(thaw(v.value)),type=v.type_info.to_data(),presence=v.presence,completeness=v.completeness,classification=v.classification,provenance_refs=list(v.provenance_refs))
 if dataclasses.is_dataclass(v):return {f.name:plain(getattr(v,f.name)) for f in dataclasses.fields(v)}
 if isinstance(v,datetime):return v.isoformat()
 if isinstance(v,Mapping):return {k:plain(x) for k,x in v.items()}
 if isinstance(v,(tuple,list)):return [plain(x) for x in v]
 return v

def observe(fn):
 try:return ['ok',plain(fn())]
 except Exception as e:return ['error',type(e).__name__,str(e),getattr(e,'detail_code',None)]
def emit(name,d,fn):print(name,canonical_json(plain(d)).hex(),canonical_json(observe(fn)).hex(),sep='\t')
def recorded(r):return dict(record=plain(r),canonical=canonical_json(plain(r)).hex(),record_hash=r.record_hash)
def fixture():
 i=h['_intent']();c=h['_contract_evidence']();ctx=h['_context']();return dict(intent=plain(i),contract=plain(c),context=dict(tenant_id=ctx.tenant_id,execution_id=ctx.identity.execution_id,business_operation_id=ctx.identity.business_operation_id),reservation=plain(h['_reservation'](i,c)),precondition=plain(h['_precondition'](i,c)),approval_request_id='approval-request-0053',invocation_id='invoke-0053',trace_refs=['trace:0053'])
def build(d):
 i=h['_intent'](**{k:d['intent'][k] for k in ('tenant_id','business_operation_id','node_id','tool_id')});ctx=h['_context'](**d['context']);c=dataclasses.replace(h['_contract_evidence'](),**d['contract']);dr=dict(d['reservation']['evidence']);dr['retained_until']=datetime.fromisoformat(dr['retained_until']);reservation=IdempotencyReservation(IdempotencyDisposition(d['reservation']['disposition']),IdempotencyEvidenceRecord(**dr),d['reservation']['business_result_ref']);pre=dict(d['precondition']);pre['mechanism']=BusinessPreconditionMechanism(pre['mechanism'])
 for k in ('observed_at','expires_at'):pre[k]=datetime.fromisoformat(pre[k])
 return WriteCallRequestBuilder().build(intent=i,context=ctx,contract_evidence=c,reservation=reservation,approval_request_id=d['approval_request_id'],invocation_id=d['invocation_id'],business_preconditions=BusinessPreconditionEvidence(**pre),trace_refs=tuple(d['trace_refs']))
def copied(v):
 import copy
 return copy.deepcopy(v)
def main():
 print('# Python Oracle '+PIN);mode=sys.argv[2];request=h['_request']();record=WriteAttemptRecord.from_request(request,NOW);receipt=WriteRequestedReceipt(record.record_hash,'write-evidence:0053',NOW,True)
 if mode=='models':
  for kind,base in [('call',request),('record',record),('receipt',receipt)]:
   emit(kind+'-base',dict(kind=kind,value=plain(base)),lambda base=base:recorded(base) if isinstance(base,WriteAttemptRecord) else base)
   for f in dataclasses.fields(base):
    val=getattr(base,f.name)
    if not isinstance(val,str):continue
    vals=['',' ','\x1c','Bearer synthetic-only']
    if f.name.endswith('hash'):vals+=['bad','sha256:'+'A'*64,'sha256:'+'a'*63,'sha256:'+'a'*65,'sha256:'+'f'*64]
    if f.name=='idempotency_key':vals+=['idem:sha256:'+'x'*n for n in (0,1,63,64,65)]+['idem:sha256:'+'😀'*64]
    for value in vals:
     d=plain(base);d[f.name]=value
     emit(kind+'-'+f.name+'-'+repr(value),dict(kind=kind,value=d),lambda base=base,k=f.name,value=value:recorded(dataclasses.replace(base,**{k:value})) if isinstance(base,WriteAttemptRecord) else dataclasses.replace(base,**{k:value}))
  for n in (0,1,63,64,65):
   refs=tuple('trace:'+str(i) for i in range(n));d=plain(request);d['trace_refs']=list(refs);emit('traces-'+str(n),dict(kind='call',value=d),lambda refs=refs:dataclasses.replace(request,trace_refs=refs))
  for name in ('',' ','\x1c','payload','가😀','Bearer synthetic-only'):
   args={name:request.arguments['detail']};p=compute_payload_hash(args);d=plain(request);d['arguments']=plain(args);d['payload_hash']=p;emit('argument-name-'+repr(name),dict(kind='call',value=d),lambda args=args,p=p:dataclasses.replace(request,arguments=args,payload_hash=p))
  for refs in [('',),(' ',),('Bearer synthetic-only',),('trace:ok','api_key=synthetic')]:
   d=plain(request);d['trace_refs']=list(refs);emit('traces-invalid-'+repr(refs),dict(kind='call',value=d),lambda refs=refs:dataclasses.replace(request,trace_refs=refs))
  return
 if mode=='build':
  base=fixture();cases=[('base',base)]
  for f,v in [('tenant_id','other-tenant'),('business_operation_id','other-business'),('business_operation_id',None)]:
   d=copied(base);d['context'][f]=v;cases.append(('context-'+f+'-'+str(v),d))
  for f,v in [('capability',ToolCapability.READ),('capability',ToolCapability.TRANSACTION),('tool_id','OTHER.TOOL')]:
   d=copied(base);d['contract'][f]=v;cases.append(('contract-'+f+'-'+str(v),d))
  for f in ('tenant_id','business_operation_id','write_node_id','tool_id','contract_hash','payload_hash'):
   d=copied(base);d['reservation']['evidence'][f]='sha256:'+'e'*64 if f.endswith('hash') else 'other';cases.append(('reservation-'+f,d))
  for f in ('tenant_id','business_operation_id','write_node_id','tool_id','contract_hash','payload_hash','write_intent_hash','provider_atomic_enforcement'):
   d=copied(base);d['precondition'][f]=False if f=='provider_atomic_enforcement' else 'sha256:'+'e'*64 if f.endswith('hash') else 'other';cases.append(('precondition-'+f,d))
  for f in ('invocation_id','approval_request_id'):
   for v in ('','Bearer synthetic-only'):
    d=copied(base);d[f]=v;cases.append((f+'-'+repr(v),d))
  for refs in ([],['trace:one'],['trace:'+str(i) for i in range(65)],['']):
   d=copied(base);d['trace_refs']=refs;cases.append(('traces-'+str(len(refs))+'-'+str(bool(refs and refs[0])),d))
  for name,d in cases:emit(name,d,lambda d=d:build(d))
  return
 if mode=='records':
  emit('base',plain(record),lambda:recorded(record))
  for f in dataclasses.fields(record):
   old=getattr(record,f.name)
   v=NOW+US if f.name=='requested_at' else 'sha256:'+'b'*64 if f.name.endswith('hash') else 'idem:sha256:'+'x'*64 if f.name=='idempotency_key' else old+'-changed'
   rr=dataclasses.replace(record,**{f.name:v});emit('changed-'+f.name,plain(rr),lambda rr=rr:recorded(rr))
  for t in (datetime.min.replace(tzinfo=UTC),NOW-US,NOW,NOW+US,datetime.max.replace(tzinfo=UTC)):
   rr=dataclasses.replace(record,requested_at=t);emit('time-'+t.isoformat(),plain(rr),lambda rr=rr:recorded(rr))
  for value in ('가😀','quoted"\\\n\x00'):
   rr=dataclasses.replace(record,invocation_id=value);emit('unicode-'+repr(value),plain(rr),lambda rr=rr:recorded(rr))
  # Omitted fields are intentionally outside this original 13-field hash.
  for f,v in [('tool_ref','other-ref'),('approval_id','OTHER_APPROVAL'),('trace_refs',('other-trace',))]:
   rr=WriteAttemptRecord.from_request(dataclasses.replace(request,**{f:v}),NOW);emit('omitted-'+f,plain(rr),lambda rr=rr:recorded(rr))
  return
 if mode=='durability':
  cases=[dict(mode=mode,flag=flag,eligibility='ALLOWED',context={},delta=delta) for flag in (False,True) for mode,delta in [('valid',0),('valid',1),('valid',-1),('not_durable',0),('mismatch',0),('raise',0)]]
  cases += [dict(mode='valid',flag=False,eligibility=e,context={},delta=0) for e in ('BLOCKED','REQUIRES_APPROVAL')]
  cases += [dict(mode='valid',flag=False,eligibility='ALLOWED',context={f:v},delta=0) for f,v in [('execution_id','other'),('tenant_id','other'),('business_operation_id','other')]]
  async def execute(d):
   events=[];ctx=h['_context'](**d['context']);ctx.checkpoint.write_request_durable=d['flag'];provider=h['_WritePort'](events)
   class Port:
    def __init__(self):self.records=[]
    async def record_requested(self,r):
     events.append('WRITE_REQUESTED');self.records.append(r)
     if d['mode']=='raise':raise OSError('protected synthetic storage failure')
     return WriteRequestedReceipt('sha256:'+'f'*64 if d['mode']=='mismatch' else r.record_hash,'write-evidence:0053',r.requested_at+int(d['delta'])*US,d['mode']!='not_durable')
   port=Port()
   try:
    value=await WriteExecutionCoordinator(provider,port).execute(request=request,decision=WriteGuardDecision(PreWriteEligibility(d['eligibility'])),context=ctx,requested_at=NOW);result=['ok',value]
   except Exception as e:result=['error',type(e).__name__,str(e),getattr(e,'detail_code',None)]
   return dict(result=result,events=events,flag=ctx.checkpoint.write_request_durable,records=[recorded(r) for r in port.records],provider_calls=len(provider.requests))
  for idx,d in enumerate(cases):emit(str(idx)+'-'+d['mode'],d,lambda d=d:asyncio.run(execute(d)))
  return
 if mode=='dispositions':
  for disposition,ref,native in [('CREATED',None,'CREATED'),('CREATED','result:created','CREATED'),('CONFLICT',None,'CONFLICT'),('REUSED',None,'PENDING'),('REUSED','result:original','REUSED')]:
   d=fixture();d['reservation']['disposition']=disposition;d['reservation']['business_result_ref']=ref;d['native']=native
   def execute(d=d):
    req=build(d);ctx=h['_context']();events=[];p=h['_WritePort'](events);e=h['_EvidencePort'](events);value=asyncio.run(WriteExecutionCoordinator(p,e).execute(request=req,decision=WriteGuardDecision(PreWriteEligibility.ALLOWED),context=ctx,requested_at=NOW));return dict(request=plain(req),events=events,result=value,provider_calls=len(p.requests),records=len(e.records))
   emit(disposition+'-'+str(ref),d,execute)
  return
 raise ValueError(mode)
main()
