"""R8-S01 unmodified Oracle, argv[1]=archive; mode values|models|goldens|negative.
No provider, auth or idempotency reservation. Sample data only.
"""
import sys,json,runpy
from pathlib import Path
from dataclasses import replace
from datetime import datetime,date,UTC,timezone,timedelta
from decimal import Decimal
from collections.abc import Mapping
sys.path.insert(0,str(Path(sys.argv[1]).resolve()))
from nsl.core import *
from nsl.write_intent import *
from nsl.ir import canonical_json,encode_value
PIN='28a949ec96581cbdc52c8133527b23bb5b72ee0a'
ROOT=Path(__file__).resolve().parents[5]
def emit(*fields,observed):print(*fields,canonical_json(observed).hex(),sep='\t')
def observe(fn):
 try:return ['ok',fn()]
 except Exception as e:return ['error',type(e).__name__,str(e),getattr(e,'detail_code',None)]
def envelope(v):return ValueEnvelope.complete(v,domain('Payload'),DataClassification.CONFIDENTIAL,('source:test',))
def thaw(v):
 if isinstance(v,Mapping):return {k:thaw(x) for k,x in v.items()}
 if isinstance(v,(list,tuple)):return [thaw(x) for x in v]
 return v
def encoded(arguments):return {k:dict(value=encode_value(thaw(v.value)),type=v.type_info.to_data(),presence=v.presence.value,completeness=v.completeness.value,classification=v.classification.value,provenance_refs=list(v.provenance_refs)) for k,v in arguments.items()}
def intent_data(i):return dict(tenant_id=i.tenant_id,business_operation_id=i.business_operation_id,node_id=i.node_id,tool_ref=i.tool_ref,tool_id=i.tool_id,approval_id=i.approval_id,skill_semantic_hash=i.skill_semantic_hash,canonical_arguments=encoded(i.canonical_arguments),payload_hash=i.payload_hash,write_intent_hash=i.write_intent_hash)
def hashes(a,skill='sha256:'+'a'*64,node='write0001',tool='FINANCE.CREATE_DRAFT'):
 canonical=canonical_write_arguments(a);p=compute_payload_hash(a);return dict(canonical=canonical.hex(),payload_hash=p,write_intent_hash=compute_write_intent_hash(skill_semantic_hash=skill,write_node_id=node,tool_id=tool,payload_hash=p))
def main():
 print('# Python Oracle '+PIN);mode=sys.argv[2]
 if mode=='values':
  values=[('null',None),('bool-false',False),('bool-true',True),('int-0',0),('int-neg',-1),('int-wide',10**100),('text','가😀\"\\\n\x00'),('empty-text',''),('empty-list',[]),('empty-map',{}),('decimal-scale',Decimal('1.2300')),('decimal-negzero',Decimal('-0.000')),('decimal-exp',Decimal('1e8')),('money',Money(Decimal('84000.00'),'KRW')),('date',date(2026,10,20)),('datetime-utc',datetime(2026,10,20,1,2,3,tzinfo=UTC)),('datetime-offset',datetime(2026,10,20,9,30,1,123456,tzinfo=timezone(timedelta(hours=9)))),('datetime-offset-seconds',datetime(2026,10,20,tzinfo=timezone(timedelta(seconds=-61,microseconds=-2)))),('enum',CheckStatus.PASS),('nested',{'z':(None,True,7,'text'),'a':[Decimal('1.00'),{'money':Money(Decimal('-0.00'),'USD')}]})]
  values += [('datetime-zone-us-'+str(us),datetime(2026,10,20,1,2,3,tzinfo=timezone(timedelta(microseconds=us)))) for us in (1,-1,123456,-123456,999999,-999999)]
  values += [('datetime-zone-max',datetime(1,1,1,tzinfo=timezone(timedelta(hours=23,minutes=59,seconds=59,microseconds=999999)))),('datetime-zone-min',datetime(9999,12,31,23,59,59,999999,tzinfo=timezone(-timedelta(hours=23,minutes=59,seconds=59,microseconds=999999))))]
  for name,value in values:
   a={'payload':envelope(value)};emit(name,canonical_json(encoded(a)).hex(),observed=observe(lambda:hashes(a)))
  for count in (0,1,2,31,32,33):
   a={f'field-{i}':envelope(i) for i in reversed(range(count))};emit(f'fields-{count}',canonical_json(encoded(a)).hex(),observed=observe(lambda:hashes(a)))
  # Envelope metadata and map insertion order do not enter payload identity.
  for name,a in [('metadata',{'payload':ValueEnvelope(Decimal('1.2300'),STRING,Presence.EMPTY,Completeness.UNKNOWN,DataClassification.PUBLIC,('different',))}),('tag-record',{'payload':envelope({'$type':'Decimal','value':'1.2300'})})]:emit(name,canonical_json(encoded(a)).hex(),observed=observe(lambda:hashes(a)))
  return
 h=runpy.run_path(str(Path(sys.argv[1])/'tests/test_v0_2_write_intent.py'))
 if mode=='metadata':
  expr,a,ctx=h['_write_fixture']();baseline=WriteIntentBuilder().build(expr,a,ctx)
  for shape,depth,provenance in [('primitive',0,()),('domain',0,()),('list',1,()),('record',0,()),('enum',0,()),('money',0,()),('optional',1,()),('list',256,()),('domain',0,('x'*256,)),('enum',256,())]:
   ty=INT
   if shape=='domain':ty=domain('Payload')
   if shape in ('list','optional'):
    for _ in range(depth):ty=list_type(ty) if shape=='list' else TypeRef('optional',item=ty)
   if shape=='record':ty=record_type('R',description=STRING,amount=DECIMAL)
   if shape=='enum':ty=enum_type('E',*(str(i) for i in range(depth if depth else 3)))
   if shape=='money':ty=money_type('USD')
   args={'detail':ValueEnvelope(a['detail'].value,ty,Presence.EMPTY,Completeness.UNKNOWN,DataClassification.PUBLIC,provenance)}
   i=WriteIntentBuilder().build(expr,args,ctx)
   assert i.payload_hash==baseline.payload_hash and i.write_intent_hash==baseline.write_intent_hash
   emit(shape,str(depth),canonical_json(list(provenance)).hex(),observed=['ok',hashes(args,ctx.skill.semantic_hash,expr.node_id,ctx.skill.required_tools[0].tool_id)])
  return
 if mode=='admission':
  expr,a,ctx=h['_write_fixture']();intent=WriteIntentBuilder().build(expr,a,ctx)
  for field in ('tenant_id','business_operation_id','node_id','tool_ref','tool_id','approval_id'):
   for val in ('',' ','Bearer synthetic-only'):emit('model',field,canonical_json(val).hex(),observed=observe(lambda:replace(intent,**{field:val}) and None))
  for field in ('skill_semantic_hash','payload_hash','write_intent_hash'):
   for val in ('bad','sha256:'+'A'*64,'sha256:'+'0'*64):emit('model',field,canonical_json(val).hex(),observed=observe(lambda:replace(intent,**{field:val}) and None))
  for name,payload,write in [('same',intent.payload_hash,intent.write_intent_hash),('payload','sha256:'+'d'*64,intent.write_intent_hash),('intent',intent.payload_hash,'sha256:'+'e'*64),('both','sha256:'+'d'*64,'sha256:'+'e'*64),('malformed-payload','bad',intent.write_intent_hash),('malformed-intent',intent.payload_hash,'bad')]:
   class Proof:
    payload_hash=payload
    write_intent_hash=write
   emit('approval',name,canonical_json(dict(payload=payload,intent=write)).hex(),observed=observe(lambda:intent.require_approval_identity(Proof())))
  return
 if mode=='models':
  for case in ('base','business','tenant','execution','approval','tool-ref','tool-id','contract','skill','node','payload','missing-business','unknown-tool','duplicate-tool','read-tool','transaction-tool','missing-args','extra-args','wrong-name'):
   expr,a,ctx=h['_write_fixture']();tools=ctx.skill.required_tools
   if case=='business':ctx=type(ctx)(ctx.skill,replace(ctx.request,business_operation_id=BusinessOperationId('other-business')))
   if case=='tenant':ctx=type(ctx)(ctx.skill,replace(ctx.request,principal=replace(ctx.request.principal,tenant_id='other-tenant')))
   if case=='execution':ctx=type(ctx)(ctx.skill,replace(ctx.request,execution_id='other-execution'))
   if case=='approval':expr=replace(expr,approval_ref='OTHER_APPROVAL')
   if case=='tool-ref':expr=replace(expr,tool_ref='other-ref');tools=(replace(tools[0],tool_ref='other-ref'),)
   if case=='tool-id':tools=(replace(tools[0],tool_id='OTHER.TOOL'),)
   if case=='contract':tools=(replace(tools[0],contract_hash='sha256:'+'e'*64),)
   if case=='skill':ctx.skill=replace(ctx.skill,semantic_hash='sha256:'+'f'*64)
   if case=='node':expr=replace(expr,node_id='write0002')
   if case=='payload':a={'detail':envelope({'description':'changed','amount':Decimal('84001'),'lines':[{'sequence':1}]})}
   if case=='missing-business':ctx=type(ctx)(ctx.skill,replace(ctx.request,business_operation_id=None))
   if case=='unknown-tool':expr=replace(expr,tool_ref='unknown')
   if case=='duplicate-tool':tools=tools*2
   if case in ('read-tool','transaction-tool'):tools=(replace(tools[0],capability=ToolCapability.READ if case=='read-tool' else ToolCapability.TRANSACTION),)
   if case=='missing-args':a={}
   if case=='extra-args':a={**a,'extra':envelope(1)}
   if case=='wrong-name':a={'other':a['detail']}
   ctx.skill=replace(ctx.skill,required_tools=tools)
   # Contract changes in an admitted Skill recompute semantic identity. Other
   # direct-model mutations deliberately characterize the constructor boundary.
   if case=='contract':ctx.skill=replace(ctx.skill,semantic_hash='').with_computed_hash()
   data=dict(tenant=ctx.tenant_id,business=ctx.identity.business_operation_id,semantic=ctx.skill.semantic_hash,node=expr.node_id,tool_ref=expr.tool_ref,approval=expr.approval_ref,names=[k for k,_ in expr.arguments],tools=[dict(tool_ref=t.tool_ref,tool_id=t.tool_id,capability=t.capability.value,contract_hash=t.contract_hash) for t in tools],arguments=encoded(a))
   emit(case,canonical_json(data).hex(),observed=observe(lambda:dict(model=intent_data(WriteIntentBuilder().build(expr,a,ctx)),**hashes(a,ctx.skill.semantic_hash,expr.node_id,tools[0].tool_id))))
  return
 if mode=='goldens':
  for path in sorted((ROOT/'compatibility/v0_2_1').rglob('*.json')):
   data=json.loads(path.read_text());candidates=list(data.get('intents',[])) if isinstance(data,dict) else []
   if isinstance(data,dict) and isinstance(data.get('provider_request'),dict):
    item=dict(data['provider_request']);semantic=data.get('runtime_result',{}).get('semantic_hash')
    if semantic and item.get('arguments') is not None:
     candidates.append(dict(tenant_id=item['tenant_id'],business_operation_id=item['business_operation_id'],node_id=item['node_id'],tool_ref=item['tool_ref'],tool_id=item['tool_id'],approval_id=item['approval_id'],skill_semantic_hash=semantic,canonical_arguments=item['arguments'],payload_hash=item['payload_hash'],write_intent_hash=item['write_intent_hash']))
   for index,item in enumerate(candidates):
    a={k:ValueEnvelope(decode_value(v['value']),TypeRef.from_data(v.get('type_info',v.get('type'))),Presence(v['presence']),Completeness(v['completeness']),DataClassification(v['classification']),tuple(v['provenance_refs'])) for k,v in item['canonical_arguments'].items()}
    i=WriteIntent(**dict(item,canonical_arguments=a));result=hashes(a,i.skill_semantic_hash,i.node_id,i.tool_id);assert result['payload_hash']==item['payload_hash'] and result['write_intent_hash']==item['write_intent_hash'];emit(str(path.relative_to(ROOT/'compatibility/v0_2_1')),str(index),canonical_json(item).hex(),observed=['ok',result])
  return
 for name,value in [('nan',Decimal('NaN')),('inf',Decimal('Infinity')),('neg-inf',Decimal('-Infinity')),('naive',datetime(2026,10,20)),('float',0.5),('bytes',b'bad'),('set',{'bad'})]:
  emit(name,observed=observe(lambda:hashes({'payload':envelope(value)})))
 for digits in (4300,4301):emit('int-'+str(digits),observed=observe(lambda:hashes({'payload':envelope(10**(digits-1))})))
 for field in ('skill_semantic_hash','write_node_id','tool_id','payload_hash'):
  for value in ('',' ','bad','Bearer synthetic-only'):
   args=dict(skill_semantic_hash='sha256:'+'a'*64,write_node_id='write0001',tool_id='FINANCE.CREATE_DRAFT',payload_hash='sha256:'+'b'*64);args[field]=value;emit(field,canonical_json(value).hex(),observed=observe(lambda:compute_write_intent_hash(**args)))
if __name__=='__main__':main()
