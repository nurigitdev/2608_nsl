"""R7-S01: unmodified fixed-Oracle model/binding/time observations.

argv[1] selects a git archive of 28a949e; argv[2]: models|bindings|time|floats.
Opaque verifier is deterministic and used only to observe Python's binding/time
rules; this Slice does not add resume claim, authenticity or WRITE execution.
"""
import sys,json
from pathlib import Path
from datetime import UTC,datetime,timedelta,timezone
from dataclasses import replace
from itertools import product
sys.path.insert(0,str(Path(sys.argv[1]).resolve()))
from nsl.approval import (ApprovalRequest,ApprovalEvidence,ApprovalResumeBindings,
 ApprovalEvidenceValidator,_utc_instant)
from nsl.core import ApprovalDecision,RiskLevel,DataClassification
from nsl.ir import canonical_json
A='sha256:'+'a'*64;B='sha256:'+'b'*64
NOW=datetime(2026,10,20,1,2,3,tzinfo=UTC)
COMMON=dict(tenant_id='tenant-a',execution_id='exec-a',business_operation_id='business-a',approval_id='APPROVE',approval_request_id='request-a',skill_semantic_hash=A,payload_hash=B,write_intent_hash='write-intent-a')
SUMMARIES={
 'empty':{},'basic':{'line_count':2,'flags':['reviewed',True]},
 'all':{'none':None,'bool':False,'integer':-1234,'wide':10**100,'text':'가😀','decimal':'0.5','nested':{'items':[0,1,True]}},
 'secret-null':{'api_key':None,'secret':''},'blank-key':{' ':1},
 'credential-key':{'api_key':'synthetic-value'},'credential-text':{'note':'Bearer synthetic-value'},
 'not-mapping':[],
}
for i,v in enumerate((0.5,1.0,-0.0,1e-7,float('nan'),float('inf'),float('-inf'))):
 SUMMARIES['float'+str(i)]={'ratio':v}
 SUMMARIES['nested-float'+str(i)]={'nested':{'items':[0,{'ratio':v}]}}

def model(kind,recipe='basic',variant='-',value='-',risk='R2',classification='INTERNAL',expiry='2026-10-20T02:02:03+00:00',reference='snapshot:payload-a'):
 values=COMMON.copy()
 if kind=='request':values.update(skill_id='TEST.SKILL',skill_version='1.0.0',write_node_id='write0',tool_id='TEST.WRITE',risk_level=RiskLevel(risk),message='Review draft',expires_at=None if expiry=='~' else datetime.fromisoformat(expiry),display_safe_summary=SUMMARIES[recipe],protected_payload_ref=None if reference=='~' else reference,data_classification=DataClassification(classification))
 else:values.update(decision=ApprovalDecision.APPROVED,approver_id='approver-a',decided_at=NOW,expires_at=None if expiry=='~' else datetime.fromisoformat(expiry),evidence_integrity='opaque-proof-a')
 if variant=='required-order':
  values['payload_hash']='invalid';values['skill_id' if kind=='request' else 'approver_id']=''
 elif variant!='-':
  values[variant]=None if value=='~' else datetime.fromisoformat(value) if variant in ('expires_at','decided_at') else ApprovalDecision(value) if variant=='decision' else value
 return (ApprovalRequest if kind=='request' else ApprovalEvidence)(**values)

def observe(call):
 try:return ['ok',call()]
 except Exception as e:return ['error',type(e).__name__,str(e)]
def emit(*fields,observed):print(*fields,canonical_json(observed).hex(),sep='\t')

def main():
 print('# Python Oracle 28a949ec96581cbdc52c8133527b23bb5b72ee0a')
 mode=sys.argv[2]
 if mode in ('models','floats'):
  recipes=list(SUMMARIES) if mode=='models' else [k for k in SUMMARIES if 'float' in k]
  if mode=='models':recipes=[k for k in recipes if 'float' not in k]
  if mode=='floats':
   for recipe in recipes:emit('request',recipe,'-','2d','R2','INTERNAL','2026-10-20T02:02:03+00:00','snapshot:payload-a',observed=observe(lambda:model('request',recipe).to_data()))
   return
  for recipe,risk,classification,expiry,reference in product(recipes,('R0','R2','R5'),DataClassification,('~','2026-10-20T02:02:03+00:00'),('~','snapshot:payload-a')):
   emit('request',recipe,'-','2d',risk,classification.value,expiry,reference,observed=observe(lambda:model('request',recipe,risk=risk,classification=classification.value,expiry=expiry,reference=reference).to_data()))
  for kind in ('request','evidence'):
   required=list(COMMON) + (['skill_id','skill_version','write_node_id','tool_id','message','protected_payload_ref'] if kind=='request' else ['approver_id','evidence_integrity'])
   for field,value in product(required,('', ' \t','\x1c','\u2003','token=synthetic-value')):
    emit(kind,'basic',field,value.encode().hex() or '~empty','R2','INTERNAL','2026-10-20T02:02:03+00:00','snapshot:payload-a',observed=observe(lambda:model(kind,variant=field,value=value).to_data()))
   for field,value in product(('skill_semantic_hash','payload_hash'),('sha256:'+'A'*64,'sha256:'+'g'*64,'sha256:'+'a'*63,'sha256:'+'a'*65,'sha256:'+'😀'*64,'0'*64)):
    emit(kind,'basic',field,value.encode().hex(),'R2','INTERNAL','2026-10-20T02:02:03+00:00','snapshot:payload-a',observed=observe(lambda:model(kind,variant=field,value=value).to_data()))
  for kind in ('request','evidence'):
   emit(kind,'basic','required-order','2d','R2','INTERNAL','2026-10-20T02:02:03+00:00','snapshot:payload-a',observed=observe(lambda:model(kind,variant='required-order').to_data()))
  for field,value in [('decision','REJECTED'),('expires_at','~'),('expires_at',NOW.isoformat()),('expires_at',(NOW-timedelta(microseconds=1)).isoformat()),('expires_at',(NOW+timedelta(microseconds=1)).isoformat()),('decided_at','0001-01-01T00:00:00+00:00')]:
   emit('evidence','basic',field,value.encode().hex(),'R2','INTERNAL','2026-10-20T02:02:03+00:00','snapshot:payload-a',observed=observe(lambda:model('evidence',variant=field,value=value).to_data()))
  return
 if mode=='time':
  for text in ('0001-01-01T00:00:00+00:00','9999-12-31T23:59:59.999999+00:00','2000-02-29T00:00:00Z','1900-02-28T23:59:59+00:00','2026-10-20T01:02:03.1+00:00','2026-10-20T01:02:03.000001-00:00','2026-10-20T01:02:03.999999+00:00','2026-10-20T01:02:03','2026-10-20T10:02:03+09:00','2026-10-20T01:02:03+00:01','2026-10-20T01:02:03-00:01'):
   emit(text.encode().hex(),observed=observe(lambda:_utc_instant(datetime.fromisoformat(text),'instant',optional=False).isoformat()))
  return
 class Verifier:
  def verify(self,evidence):return True
 class Clock:
  def __init__(self,now):self.time=now
  def now(self):return self.time
 for decision,expiry,delta,mutation in product(ApprovalDecision,('none','zero','hour'),(-1,0,1,3599999999,3600000000,3600000001),('-',*COMMON,'all')):
  e=model('evidence',variant='decision',value=decision.value)
  e=replace(e,expires_at=None if expiry=='none' else NOW+timedelta(microseconds=0 if expiry=='zero' else 3600000000))
  binding=COMMON.copy()
  for k in (COMMON if mutation=='all' else [mutation]):
   if k!='-':binding[k]='sha256:'+'c'*64 if k in ('payload_hash','skill_semantic_hash') else 'other'
  b=ApprovalResumeBindings(**binding)
  r=ApprovalEvidenceValidator().validate(e,b,Verifier(),Clock(NOW+timedelta(microseconds=delta)))
  emit(decision.value,expiry,str(delta),mutation,observed=[r.validation.value,r.approval_status.value,r.reason_code])
if __name__=='__main__':main()
