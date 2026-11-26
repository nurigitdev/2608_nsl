"""R9-S04 fixed Oracle models|hashes|normal|exceptions. Python 3.12 -I -B ARCHIVE MODE.
Synthetic READ/WRITE/storage only. Original observations are never rewritten.
"""
import asyncio,dataclasses,runpy,sys
from datetime import datetime,timedelta,UTC
from collections.abc import Mapping
from pathlib import Path
sys.path.insert(0,str(Path(sys.argv[1]).resolve()))
from nsl.core import *
from nsl.tools import PostWriteVerification
from nsl.verification import *
from nsl.write_execution import *
from nsl.validation import WriteGuardDecision
from nsl.ir import canonical_json,encode_value
root=Path(sys.argv[1]);w=runpy.run_path(str(root/'tests/test_v0_2_write_execution.py'));v=runpy.run_path(str(root/'tests/test_v0_2_post_write_verification.py'))
PIN='28a949ec96581cbdc52c8133527b23bb5b72ee0a'
def thaw(x):
 if isinstance(x,Mapping):return {k:thaw(y) for k,y in x.items()}
 if isinstance(x,(list,tuple)):return [thaw(y) for y in x]
 return x
def plain(x):
 if isinstance(x,ValueEnvelope):return dict(value=encode_value(thaw(x.value)),type=x.type_info.to_data(),presence=x.presence,completeness=x.completeness,classification=x.classification,provenance_refs=list(x.provenance_refs))
 if isinstance(x,TypeRef):return x.to_data()
 if isinstance(x,datetime):return x.isoformat()
 if dataclasses.is_dataclass(x):return {f.name:plain(getattr(x,f.name)) for f in dataclasses.fields(x)}
 if isinstance(x,Mapping):return {k:plain(y) for k,y in x.items()}
 if isinstance(x,(tuple,list)):return [plain(y) for y in x]
 return x
def emit(name,d,fn):
 try:e=['ok',plain(fn())]
 except Exception as error:e=['error',type(error).__name__,str(error)]
 print(name,canonical_json(plain(d)).hex(),canonical_json(e).hex(),sep='\t')
def hashed(r):
 i=plain(r.identity);i['write_tool_id']=i.pop('tool_id')
 i.update({f.name:plain(getattr(r,f.name)) for f in dataclasses.fields(r) if f.name!='identity'})
 return dict(request=plain(r),canonical=canonical_json(i).hex(),request_hash=r.request_hash)
def models():
 request=PostWriteVerifier._request(v['_committed'](),v['_verification_hint']())
 observation=VerificationObservation(request.request_hash,'DRAFT-0057',True,'verification:0057',v['NOW'])
 results=[v['_verify'](),v['_verify'](port=v['_ReadPort'](mode='timeout')),v['_verify'](port=v['_ReadPort'](matched=False,reason_code='MISMATCH')),v['_verify'](contract=v['_contract']())]
 for kind,base in [('request',request),('observation',observation)]+[('result',r) for r in results]:
  name=kind+'-'+str(getattr(base,'status','base'));emit(name,dict(kind=kind,value=plain(base)),lambda base=base:base)
  for f in dataclasses.fields(base):
   if f.name in ('identity','reference_schema'):continue
   old=getattr(base,f.name)
   if isinstance(old,str) or old is None:
    values=['',' ','\x1c','Bearer synthetic-only','가😀']
    if f.name in ('reason_code','evidence_ref','verification_tool_id','provider_transaction_id','consistency_hint','observed_at'):values=[None]+values
    if f.name.endswith('hash'):values+=['sha256:'+'f'*n for n in (0,1,63,64,65)]+['sha256:'+'A'*64]
    for n,value in enumerate(values):
     d=plain(base);d[f.name]=value
     emit(name+'-'+f.name+'-'+str(n),dict(kind=kind,value=d),lambda base=base,f=f,value=value:dataclasses.replace(base,**{f.name:value}))
  if kind=='observation':
   for matched,reason in [(False,None),(False,'MISMATCH'),(True,None),(True,'MISMATCH')]:
    d=plain(base);d.update(matched=matched,reason_code=reason)
    emit('matched-'+str(matched)+'-'+str(reason),dict(kind=kind,value=d),lambda matched=matched,reason=reason:dataclasses.replace(base,matched=matched,reason_code=reason))
  if kind=='result':
   for at in (None,v['NOW'],datetime.min.replace(tzinfo=UTC),datetime.max.replace(tzinfo=UTC)):
    d=plain(base);d['observed_at']=plain(at)
    emit(name+'-time-'+str(at),dict(kind=kind,value=d),lambda base=base,at=at:dataclasses.replace(base,observed_at=at))
   for commit in (CommitState.NOT_COMMITTED,CommitState.UNKNOWN_COMMIT_STATE):
    d=plain(base);d['commit_state']=commit.value;emit(name+'-commit-'+commit.value,dict(kind=kind,value=d),lambda base=base,commit=commit:dataclasses.replace(base,commit_state=commit))
def hashes():
 base=PostWriteVerifier._request(v['_committed'](),v['_verification_hint']());emit('base',plain(base),lambda:hashed(base))
 for f in dataclasses.fields(base.identity):
  old=getattr(base.identity,f.name);value='sha256:'+'a'*64 if f.name.endswith('hash') else 'idem:sha256:'+'x'*64 if f.name=='idempotency_key' else old+'-other'
  r=dataclasses.replace(base,identity=dataclasses.replace(base.identity,**{f.name:value}));emit('identity-'+f.name,plain(r),lambda r=r:hashed(r))
 for f,value in [('verification_tool_id','OTHER.READ'),('business_reference','BUSINESS-OTHER'),('provider_transaction_id',None),('provider_transaction_id','tx:other'),('consistency_hint',None),('consistency_hint','EVENTUAL')]:
  r=dataclasses.replace(base,**{f:value});emit(f+'-'+str(value),plain(r),lambda r=r:hashed(r))
 for schema in (None,STRING,domain('Reference'),list_type(STRING),record_type('Reference',id=STRING),record_type('Empty'),enum_type('Reference','A','B'),enum_type('Empty'),money_type('KRW')):
  r=dataclasses.replace(base,reference_schema=schema);emit('schema-'+str(schema),plain(r),lambda r=r:hashed(r))
 for value in ('가😀','quote"\\\n\x00'):
  r=dataclasses.replace(base,business_reference=value);emit('unicode-'+repr(value),plain(r),lambda r=r:hashed(r))
def outcome(x):return dict(kind='committed' if isinstance(x,WriteCommitted) else 'not-committed' if isinstance(x,WriteNotCommitted) else 'unknown',commit_state=x.commit_state.value,data=plain(x.result if isinstance(x,WriteCommitted) else x))
async def scenario(c):
 hint=PostWriteVerification(tool_id='FINANCE.GET_DRAFT',reference_schema=STRING,consistency_hint='READ_AFTER_WRITE_STRONG')
 case=c['case'];verification=hint
 if case=='not-requested':verification=None
 if case=='schema-only':verification=PostWriteVerification(reference_schema=STRING)
 if case=='hint-only':verification=PostWriteVerification(consistency_hint='EVENTUAL')
 original=dataclasses.replace(w['_tool_contract'](),write_semantics=dataclasses.replace(w['_tool_contract']().write_semantics,verification=verification))
 selected=original
 if case=='contract-removed':selected=dataclasses.replace(original,write_semantics=dataclasses.replace(original.write_semantics,verification=None))
 if case=='contract-redirected':selected=dataclasses.replace(original,write_semantics=dataclasses.replace(original.write_semantics,verification=dataclasses.replace(hint,tool_id='FINANCE.GET_OTHER')))
 if case=='contract-timeout':selected=dataclasses.replace(original,timeout_ms=original.timeout_ms+1)
 if case=='contract-tool':selected=dataclasses.replace(original,tool_id='OTHER.WRITE')
 if case=='contract-read':selected=w['_tool_contract'](capability=ToolCapability.READ)
 ctx=w['_context']();request=w['_request'](context=ctx,contract=w['_contract_evidence'](original));w['_seed_idempotency'](ctx,request)
 raw=w['_not_committed'](request) if case=='not-committed' else w['_unknown'](request) if case=='unknown-commit' else w['_committed'](request)
 events=[];inv=await WriteExecutionCoordinator(w['_WritePort'](events,result=raw),w['_EvidencePort'](events)).execute(request=request,decision=WriteGuardDecision(PreWriteEligibility.ALLOWED),context=ctx,requested_at=w['NOW'])
 decision=await WriteOutcomeCoordinator(w['_OutcomePort'](events)).finalize(request=request,context=ctx,requested_record_hash=WriteAttemptRecord.from_request(request,w['NOW']).record_hash,outcome=inv,recorded_at=w['NOW']+timedelta(seconds=2))
 if case=='tenant':ctx.security_context=dataclasses.replace(ctx.security_context,principal=dataclasses.replace(ctx.security_context.principal,tenant_id='other-tenant'))
 if case in ('business','business-missing'):ctx.identity=dataclasses.replace(ctx.identity,business_operation_id=None if case.endswith('missing') else 'other-business')
 for kind,mapping in [('key',ctx.idempotency.keys_by_write_node),('payload',ctx.idempotency.payload_hashes_by_write_node)]:
  if case=='missing-'+kind:mapping.clear()
  if case=='foreign-'+kind:mapping[request.node_id]='other'
 requests=[]
 class Read:
  async def read(self,r):
   requests.append(hashed(r))
   if case=='timeout':raise TimeoutError('protected timeout')
   if case=='dependency':raise ConnectionError('protected dependency')
   if case=='cancelled':raise asyncio.CancelledError()
   if case=='malformed':return object()
   return VerificationObservation('sha256:'+'0'*64 if case in ('wrong-hash','wrong-hash-stale') else r.request_hash,'OTHER' if case=='wrong-reference' else r.business_reference,c.get('matched',True),'verification:0053',w['NOW']+timedelta(seconds=1,microseconds=c.get('delta',1_000_000)),None if c.get('matched',True) else 'PROVIDER_STATE_MISMATCH')
 before=plain(ctx.writes.entries)
 try:
  res=await PostWriteVerifier().verify(decision.outcome.result if isinstance(decision.outcome,WriteCommitted) else decision.outcome,selected,ctx,None if case=='missing-port' else Read())
  observed=['ok',plain(res)];summary=plain(res.to_summary())
 except asyncio.CancelledError:observed=['error','CancelledError'];summary=None
 except Exception as e:observed=['error',type(e).__name__];summary=None
 assert before==plain(ctx.writes.entries)
 return dict(config=c,original_contract=original.to_canonical_data(),selected_contract=selected.to_canonical_data(),input_outcome=outcome(raw),context=dict(tenant_id=ctx.tenant_id,business_operation_id=ctx.identity.business_operation_id,keys_by_write_node=plain(ctx.idempotency.keys_by_write_node),payload_hashes_by_write_node=plain(ctx.idempotency.payload_hashes_by_write_node)),observed=dict(result=observed,summary=summary,requests=requests,journal_unchanged=True))
async def main():
 print('# Python Oracle '+PIN);mode=sys.argv[2]
 if mode=='models':models();return
 if mode=='hashes':hashes();return
 cases=[dict(case=x) for x in ('valid','failed','timeout','dependency','malformed','wrong-hash','wrong-reference','cancelled','not-requested','schema-only','hint-only','missing-port','contract-tool','contract-read','tenant','business','business-missing','missing-key','missing-payload','foreign-key','foreign-payload','not-committed','unknown-commit')]
 cases[1]['matched']=False
 cases += [dict(case='boundary',delta=d,matched=m) for d in (0,1) for m in (True,False)]
 cases += [dict(case='wrong-hash-stale',delta=-1)]
 if mode=='exceptions':cases=[dict(case=x) for x in ('contract-removed','contract-redirected','contract-timeout')]+[dict(case='stale',delta=-1,matched=m) for m in (True,False)]
 for i,c in enumerate(cases):print(str(i)+'-'+c['case'],canonical_json(await scenario(c)).hex(),sep='\t')
if sys.argv[2]=='models':
 print('# Python Oracle '+PIN);models()
else:asyncio.run(main())
