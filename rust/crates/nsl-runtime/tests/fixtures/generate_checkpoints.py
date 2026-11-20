"""R7-S02 unmodified fixed Oracle, argv[1]=archive, argv[2]=normal|negative|exceptions.
No expectation rewriting and no provider/enterprise transport.
"""
import sys,json,copy
from pathlib import Path
from dataclasses import replace
from decimal import Decimal
from datetime import date,datetime
from itertools import product
sys.path.insert(0,str(Path(sys.argv[1]).resolve()))
from nsl.core import (INT,STRING,BOOL,DECIMAL,TypeRef,ValueEnvelope,Presence,Completeness,DataClassification,
 ApprovalStatus,CheckStatus,BusinessOperationId,list_type,record_type,money_type,Money,encode_value)
from nsl.ir import NsoCodec,canonical_json
from nsl.runtime_models import ExecutionContext,ExecutionRequest,CheckResult,EmitRecord
from nsl.security import ExecutionPrincipal,PrincipalVerification,DataHandlingPolicy
from nsl.checkpoints import CheckpointCoordinator,ExecutionCheckpoint
ROOT=Path(__file__).resolve().parents[5]
class Clock:
 def __init__(self):self.calls=0
 def monotonic_ns(self):self.calls+=1;return 1_000_000_000

def request():return ExecutionRequest('exec-checkpoint',{'year':2026},{'user':{'team_id':'TEAM-FINANCE'}},ExecutionPrincipal('tenant-a','subject-a','USER',frozenset({'z','a'}),frozenset({'write','read'}),'auth:reference',PrincipalVerification.VERIFIED),business_operation_id=BusinessOperationId('business-a'))
def skill(ver):return NsoCodec.decode((ROOT/f'compatibility/v0_2_1/nso/ir-{ver}-canonical/expected.nso').read_bytes())
def populated(ver,recipe,prior=0):
 c=ExecutionContext(skill(ver),request(),Clock());c.checkpoint.checkpoint_version=prior
 if recipe=='empty':return c
 v=ValueEnvelope(2026,INT,Presence.PRESENT,Completeness.COMPLETE,DataClassification.CONFIDENTIAL,('input:year',))
 text=ValueEnvelope('TEAM-FINANCE',STRING,Presence.PRESENT,Completeness.COMPLETE,DataClassification.INTERNAL,('context:team',))
 c.bind_input('sym-input',v);c.bind_context('sym-context',text);c.bind('sym-root',v);c.bind_check('check-root',text)
 c.push_frame();c.bind('sym-inner',text);c.bind_check('check-inner',v)
 c.checks.append(CheckResult('rule',CheckStatus.PASS,'ERROR','확인😀','condition',Presence.PRESENT,Completeness.COMPLETE,('p:z','p:a'),None))
 c.outputs.append(EmitRecord({'team':text,'year':v}));c.resources.tool_calls=1;c.resources.loop_iterations=1 if recipe.startswith('safe-') else 2;c.resources.emitted_rows=1;c.resources.max_collection_size_seen=2
 c.data_completeness=Completeness.PARTIAL;c.invocation_counter=3;c.foreach_depth=1;c.current_node_id='approval0'
 c.approvals.records['approval0']=ApprovalStatus.APPROVED;c.writes.entries.append({'event_type':'WRITE_REQUESTED','node_id':'write0'})
 c.idempotency.keys_by_write_node['write0']='idem-write0';c.idempotency.payload_hashes_by_write_node['write0']='sha256:'+'a'*64
 c.checkpoint.last_completed_node_id='approval0';c.checkpoint.suspended_at_node_id='write0';c.checkpoint.trace_sequence=4;c.checkpoint.approval_request_published=True;c.checkpoint.write_request_durable=True
 c.checkpoint.approval_request_ids_by_write_node['write0']='request0';c.checkpoint.write_intent_hashes_by_write_node['write0']='intent0';c.checkpoint.payload_hashes_by_write_node['write0']='sha256:'+'b'*64
 if recipe.endswith('values'):
  vals=[('list',[1,2],list_type(INT)),('record',{'a':[1,2]},record_type('R',a=list_type(INT))),('decimal',Decimal('-0.000'),DECIMAL),('money',Money(Decimal('12.500'),'USD'),money_type('USD')),('date',date(2026,11,20),TypeRef('primitive','Date')),('time',datetime.fromisoformat('2026-11-20T10:02:03.123456+09:00'),TypeRef('primitive','DateTime')),('bool',False,BOOL)]
  for name,value,ty in vals:c.bind('value-'+name,ValueEnvelope.complete(value,ty))
 if recipe.startswith('completeness-'):c.data_completeness=Completeness(recipe[13:])
 return c

def resign(cp,state=None,**changes):
 d={k:getattr(cp,k) for k in ('execution_id','business_operation_id','checkpoint_version','skill_id','skill_version','semantic_hash','ir_version','runtime_version','state_payload')}
 if state is not None:d['state_payload']=canonical_json(state)
 d.update(changes);return ExecutionCheckpoint.create(**d)
def observed(fn):
 try:return ['ok',fn()]
 except Exception as e:return ['error',type(e).__name__,str(e)]
def emit(*f,result):print(*f,canonical_json(result).hex(),sep='\t')
def main():
 print('# Python Oracle 28a949ec96581cbdc52c8133527b23bb5b72ee0a')
 mode=sys.argv[2];coord=CheckpointCoordinator()
 if mode=='normal':
  for ver,recipe,prior in product(('1-0','1-1'),('empty','populated','values','safe-populated','safe-values','completeness-COMPLETE','completeness-PARTIAL','completeness-UNKNOWN'),(0,1,2,10**40)):
   ctx=populated(ver,recipe,prior);clock=Clock();cp=coord.capture(ctx);restored=coord.restore(ExecutionCheckpoint.from_bytes(cp.to_bytes()),skill(ver),request(),clock);again=coord.capture(restored)
   emit(ver,recipe,str(prior),cp.state_payload.hex(),cp.to_bytes().hex(),cp.integrity_hash,again.state_payload.hex(),again.to_bytes().hex(),str(clock.calls),result=['ok'])
  return
 cp=coord.capture(populated('1-1','populated'));base=json.loads(cp.state_payload)
 if mode=='wire':
  for size in (0,1,2,3,4,5,63,64,65,255,256,257):
   payload=bytes(i%256 for i in range(size));c=resign(cp,state_payload=payload)
   emit('binary',payload.hex() or '-',c.to_bytes().hex(),c.integrity_hash,result=['ok'])
  for text in ('Zh==','Zg===','TWFu==','====','Zg==\n','A','Zg=','Zg==?','Zg==AA','Zm8==','Zm8','????'):
   c=resign(cp,state_payload=b'Man' if text.startswith('TWFu') else b'f')
   wire=canonical_json(dict(c.to_data(),state_payload=text))
   emit('alias',wire.hex(),c.to_bytes().hex(),c.integrity_hash,result=observed(lambda:ExecutionCheckpoint.from_bytes(wire).to_bytes().hex()))
  return
 if mode=='values':
  dates=['2026-11-20','20261120','bad','2026-02-29','2026-13-01','','가😀a']
  times=['2026-11-20T01:02:03','2026-11-20T01:02:03Z','2026-11-20T01:02:03-00:00','2026-11-20 01:02:03.1+09:00','2026-11-20T010203.123456789-0900','2026-11-20T01','2026-11-20T01:02','2026-11-20T01:02:03+00:00:00.123456','2026-11-20T01:02:03-00:01:00.123456','bad','2026-11-20T24:00:00','2026-11-20T01:60:00','2026-11-20T01:02:60','2026-11-20T01:02:03.bad','2026-11-20T01:02:03+25:00','2026-11-20T01:02:03.','2026-11-20T01:02:03:04','2026-11-20T01:02:03+00:00:01','2026-11-20T0x:02:03','2026-11-20T1:02:03','2026-11-20T123','2026-11-20T01:02:03:04.5','2026-11-20T가']
  values=[{'$type':'Date','value':v} for v in dates]+[{'$type':'DateTime','value':v} for v in times]+[{'$type':'Decimal','value':'1e2'},{'$type':'Decimal','value':7},{'$type':'Decimal','value':'bad'},{'$type':'Money','amount':'12.500','currency':'USD','extra':1},{'$type':'Money','amount':'1','currency':'bad'},{'$type':'Money','amount':1,'currency':'USD'},{'nested':[True,None,[],{}],'$type':'Other'},{'$type':'Date','value':7},{'$type':'DateTime','value':False}]
  for index,value in enumerate(values):
   state=copy.deepcopy(base);state['writes']=[value];c=resign(cp,state)
   result=observed(lambda:coord.capture(coord.restore(c,skill('1-1'),request())).to_bytes().hex())
   emit(str(index),canonical_json(value).hex(),c.to_bytes().hex(),result=result)
  return
 if mode=='negative':
  for field in ('execution_id','business_operation_id','skill_id','skill_version','semantic_hash','ir_version','runtime_version'):
   changed=resign(cp,**{field:'other'})
   emit('binding',field,changed.to_bytes().hex(),result=observed(lambda:coord.restore(changed,skill('1-1'),request()) and None))
  for case,mutate in [('frames-empty',lambda s:s['symbols'].update(frames=[])),('frames-mismatch',lambda s:s['symbols'].update(check_frames=[])),('negative-resource',lambda s:s['resources'].update(tool_calls=-1)),('bad-completeness',lambda s:s['execution'].update(data_completeness='BAD')),('bad-approval',lambda s:s['approvals'].update(approval0='BAD')),('writes-not-list',lambda s:s.update(writes={})),('invalid-value',lambda s:s['symbols']['inputs']['sym-input'].update(value={'$type':'Decimal','value':7})),('state-schema',lambda s:s.update(schema_version=2)),('version-mismatch',lambda s:s.update(checkpoint_version=2)),('nested-version-mismatch',lambda s:s['checkpoint'].update(checkpoint_version=2)),('not-bool',lambda s:s['checkpoint'].update(write_request_durable=1)),('security',lambda s:s['security']['principal'].update(tenant_id='other')),('extra',lambda s:s.update(extra=1))]:
   s=copy.deepcopy(base);mutate(s);changed=resign(cp,s)
   emit('state',case,changed.to_bytes().hex(),result=observed(lambda:coord.restore(changed,skill('1-1'),request()) and None))
  for case,payload in [('invalid-utf8',b'\xff'),('duplicate',b'{"schema_version":1,"schema_version":1}'),('nonfinite',b'NaN'),('array',b'[]'),('extra',canonical_json(dict(cp.to_data(),extra=1))),('schema',canonical_json(dict(cp.to_data(),schema_version=2))),('base64',canonical_json(dict(cp.to_data(),state_payload='?'))),('integrity',canonical_json(dict(cp.to_data(),integrity_hash='sha256:'+'0'*64)))]:
   emit('wire',case,payload.hex(),result=observed(lambda:ExecutionCheckpoint.from_bytes(payload) and None))
  return
 for case,mutate in [('schema-bool',lambda s:s.update(schema_version=True)),('version-bool',lambda s:s.update(checkpoint_version=True)),('wrong-list',lambda s:s['symbols']['inputs']['sym-input'].update(value=[1,True],type=list_type(INT).to_data())),('scalar-list',lambda s:s['symbols']['inputs']['sym-input'].update(value=17,type=list_type(INT).to_data())),('float',lambda s:s['symbols']['inputs']['sym-input'].update(value=0.5)),('nan',lambda s:s['symbols']['inputs']['sym-input'].update(value={'$type':'Decimal','value':'NaN'},type=DECIMAL.to_data())),('credential',lambda s:s['symbols']['inputs']['sym-input'].update(value='token=synthetic-only',type=STRING.to_data())),('depth-17',lambda s:s['execution'].update(foreach_depth=17)),('depth-wide',lambda s:s['execution'].update(foreach_depth=10**100)),('check-refs-string',lambda s:s['checks'][0].update(provenance_refs='ab')),('check-message-number',lambda s:s['checks'][0].update(message=7))]:
  s=copy.deepcopy(base);mutate(s);changed=resign(cp,s)
  emit('state',case,changed.to_bytes().hex(),result=observed(lambda:coord.restore(changed,skill('1-1'),request()) and None))
 for value in (True,1.0):
  wire=canonical_json(dict(cp.to_data(),schema_version=value));emit('wire','schema-'+str(value),wire.hex(),result=observed(lambda:ExecutionCheckpoint.from_bytes(wire) and None))
if __name__=='__main__':main()
