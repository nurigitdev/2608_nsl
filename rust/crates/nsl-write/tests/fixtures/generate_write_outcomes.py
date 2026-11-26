"""R9-S03 original frozen Oracle models|records|persistence; Python 3.12 -I -B ARCHIVE MODE."""
import asyncio,dataclasses,runpy,sys
from collections.abc import Mapping
from datetime import datetime,timedelta,UTC
from pathlib import Path
sys.path.insert(0,str(Path(sys.argv[1]).resolve()))
from nsl.write_execution import *
from nsl.validation import WriteGuardDecision
from nsl.core import *
from nsl.ir import canonical_json,encode_value
h=runpy.run_path(str(Path(sys.argv[1])/'tests/test_v0_2_write_execution.py'))
PIN='28a949ec96581cbdc52c8133527b23bb5b72ee0a';NOW=h['NOW'];AT=NOW+timedelta(seconds=2)
request=h['_request']();requested_hash=WriteAttemptRecord.from_request(request,NOW).record_hash
def thaw(v):
 if isinstance(v,Mapping):return {k:thaw(x) for k,x in v.items()}
 if isinstance(v,(tuple,list)):return [thaw(x) for x in v]
 return v
def plain(v):
 if isinstance(v,ValueEnvelope):return dict(value=encode_value(thaw(v.value)),type=v.type_info.to_data(),presence=v.presence,completeness=v.completeness,classification=v.classification,provenance_refs=list(v.provenance_refs))
 if dataclasses.is_dataclass(v):return {f.name:plain(getattr(v,f.name)) for f in dataclasses.fields(v)}
 if isinstance(v,datetime):return v.isoformat()
 if isinstance(v,Mapping):return {k:plain(x) for k,x in v.items()}
 if isinstance(v,(tuple,list)):return [plain(x) for x in v]
 return v
def outcome(v):return dict(kind='committed' if isinstance(v,WriteCommitted) else 'not-committed' if isinstance(v,WriteNotCommitted) else 'unknown',commit_state=v.commit_state.value,data=plain(v.result if isinstance(v,WriteCommitted) else v))
def selected(kind):return h[{'committed':'_committed','not-committed':'_not_committed','unknown':'_unknown'}[kind]](request)
def record(kind='unknown'):return WriteOutcomeCoordinator._record(requested_hash,selected(kind),AT)
def recorded(r):
 d=plain(r);identity=d.pop('identity');d.update(identity)
 return dict(record=plain(r),canonical=canonical_json(d).hex(),record_hash=r.record_hash)
def emit(name,data,fn):
 try:result=['ok',plain(fn())]
 except Exception as e:result=['error',type(e).__name__,str(e),getattr(e,'detail_code',None)]
 print(name,canonical_json(plain(data)).hex(),canonical_json(result).hex(),sep='\t')
def models():
 r=record();receipt=WriteOutcomeReceipt(r.record_hash,'outcome:0054',AT)
 for kind,base in [('record',r),('receipt',receipt)]:
  emit(kind+'-base',dict(kind=kind,value=plain(base)),lambda base=base:recorded(base) if isinstance(base,WriteOutcomeRecord) else base)
  for f in dataclasses.fields(base):
   old=getattr(base,f.name)
   if not isinstance(old,str) and f.name not in ('reason_code','provider_transaction_id','business_reference'):continue
   values=['',' ','\x1c','Bearer synthetic-only','가😀']
   if f.name in ('reason_code','provider_transaction_id','business_reference'):values=[None]+values
   if f.name.endswith('hash'):values+=['bad','sha256:'+'A'*64]+['sha256:'+'f'*n for n in (0,1,63,64,65)]
   for i,value in enumerate(values):
    d=plain(base);d[f.name]=value
    emit(kind+'-'+f.name+'-'+str(i),dict(kind=kind,value=d),lambda base=base,f=f,value=value:recorded(dataclasses.replace(base,**{f.name:value})) if isinstance(base,WriteOutcomeRecord) else dataclasses.replace(base,**{f.name:value}))
 for refs in [tuple('evidence:'+str(i) for i in range(n)) for n in (0,1,63,64,65)]+[('',),(' ',),('Bearer synthetic-only',)]:
  rr=plain(r);rr['evidence_refs']=list(refs)
  emit('refs-'+str(len(refs))+'-'+repr(refs[:1]),dict(kind='record',value=rr),lambda refs=refs:recorded(dataclasses.replace(r,evidence_refs=refs)))
 for ref in ('',' ','\x1c','Bearer synthetic-only','outcome:가😀'):
  emit('journal-'+repr(ref),dict(kind='journal',record=plain(r),durable_ref=ref),lambda ref=ref:WriteJournalEntry(r,ref))
def records():
 for kind in ('committed','not-committed','unknown'):
  base=record(kind);emit(kind,plain(base),lambda base=base:recorded(base))
 base=record()
 for f in dataclasses.fields(base.identity):
  old=getattr(base.identity,f.name);v='sha256:'+'f'*64 if f.name.endswith('hash') else 'idem:sha256:'+'x'*64 if f.name=='idempotency_key' else old+'-changed'
  rr=dataclasses.replace(base,identity=dataclasses.replace(base.identity,**{f.name:v}));emit('identity-'+f.name,plain(rr),lambda rr=rr:recorded(rr))
 for f,v in [('requested_record_hash','sha256:'+'f'*64),('commit_state',CommitState.NOT_COMMITTED),('reason_code','OTHER'),('provider_transaction_id','tx:other'),('business_reference','business:other'),('evidence_refs',('other:ref',)),('recorded_at',AT+timedelta(microseconds=1))]:
  rr=dataclasses.replace(base,**{f:v});emit('record-'+f,plain(rr),lambda rr=rr:recorded(rr))
 for value in ('가😀','quote"\\\n\x00'):
  rr=dataclasses.replace(base,provider_transaction_id=value);emit('unicode-'+repr(value),plain(rr),lambda rr=rr:recorded(rr))
 for at in (datetime.min.replace(tzinfo=UTC),AT-timedelta(microseconds=1),AT,AT+timedelta(microseconds=1),datetime.max.replace(tzinfo=UTC)):
  rr=dataclasses.replace(base,recorded_at=at);emit('time-'+at.isoformat(),plain(rr),lambda rr=rr:recorded(rr))
async def persist(c):
 context=h['_context']();r=h['_request'](context=context);h['_seed_idempotency'](context,r);events=[]
 raw=selected(c['kind']);case=c['case']
 if case.startswith('identity-'):
  f=case.removeprefix('identity-');old=getattr(raw.identity,f);value='sha256:'+'f'*64 if f.endswith('hash') else 'idem:sha256:'+'x'*64 if f=='idempotency_key' else old+'-other'
  raw=dataclasses.replace(raw,identity=dataclasses.replace(raw.identity,**{f:value}))
 class Provider:
  async def execute(self,r):events.append('PROVIDER_WRITE');return raw
 actual=await WriteExecutionCoordinator(Provider(),h['_EvidencePort'](events)).execute(request=r,decision=WriteGuardDecision(PreWriteEligibility.ALLOWED),context=context,requested_at=NOW)
 if case=='context-execution':context.identity=dataclasses.replace(context.identity,execution_id='other-execution')
 if case=='context-tenant':context.security_context=dataclasses.replace(context.security_context,principal=dataclasses.replace(context.security_context.principal,tenant_id='other-tenant'))
 if case in ('context-business','context-business-missing'):context.identity=dataclasses.replace(context.identity,business_operation_id=None if case.endswith('missing') else 'other-business')
 for field,mapping in [('key',context.idempotency.keys_by_write_node),('payload',context.idempotency.payload_hashes_by_write_node)]:
  if case=='missing-'+field:mapping.clear()
  if case=='foreign-'+field:mapping[r.node_id]='other'
 before_keys=plain(context.idempotency)
 if c.get('prefix',False):context.writes.entries.append(WriteJournalEntry(record(),'prior-outcome:0054'))
 calls=0;stored=[]
 class Evidence:
  async def record_outcome(self,rr):
   nonlocal calls
   calls+=1;events.append('WRITE_OUTCOME');stored.append(rr)
   if case=='storage-error':raise OSError('protected storage failure')
   if case=='malformed-receipt':return object()
   return WriteOutcomeReceipt('sha256:'+'f'*64 if case=='receipt-hash' else rr.record_hash,'outcome:0054',AT+timedelta(microseconds=c.get('delta',0)))
 try:
  decision=await WriteOutcomeCoordinator(Evidence()).finalize(request=r,context=context,requested_record_hash=requested_hash,outcome=actual,recorded_at=AT)
  result=['ok',outcome(decision.outcome)]
 except Exception as e:result=['error',type(e).__name__,getattr(e,'detail_code',None)]
 return dict(result=result,events=events,calls=calls,stored=[recorded(x) for x in stored],journal=plain(context.writes.entries),keys=plain(context.idempotency),keys_before=before_keys,flag=context.checkpoint.write_request_durable,input_outcome=outcome(actual))
async def main():
 print('# Python Oracle '+PIN);mode=sys.argv[2]
 if mode=='models':models();return
 if mode=='records':records();return
 cases=[]
 for kind in ('committed','not-committed','unknown'):
  for case in ('valid','storage-error','malformed-receipt','receipt-hash'):
   for prefix in (False,True):cases.append(dict(kind=kind,case=case,prefix=prefix))
  for delta in (-1,0,1):cases.append(dict(kind=kind,case='receipt-time',delta=delta,prefix=True))
 for case in ['identity-'+f.name for f in dataclasses.fields(record().identity)]+['context-execution','context-tenant','context-business','context-business-missing','missing-key','missing-payload','foreign-key','foreign-payload']:
  cases.append(dict(kind='unknown',case=case,prefix=True))
 for i,c in enumerate(cases):print(str(i)+'-'+c['kind']+'-'+c['case'],canonical_json(c).hex(),canonical_json(await persist(c)).hex(),sep='\t')
asyncio.run(main())
