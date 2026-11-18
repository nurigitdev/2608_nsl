"""R6-S01 observations from frozen Python Oracle 28a949e, no implementation patches."""
from dataclasses import replace
from itertools import product
from pathlib import Path
import json
import sys
sys.path.insert(0,str(Path(sys.argv[1]).resolve()))
from nsl.core import (INT,DECIMAL,BOOL,STRING,Presence,Completeness,DataClassification,CheckStatus,
    ExecutionStatus,ApprovalStatus,CommitState,PreWriteEligibility,VerificationStatus,ValueEnvelope)
from nsl.runtime_models import (ExecutionIdentity,ExecutionSecurityContext,ExecutionRequest,ExecutionContext,
    ResourceGuard,_ResourceMeter,ResourceUsage,CheckResult,ValidationSummary,ValidationSummaryStatus,
    EmitRecord,RuntimeErrorInfo,ApprovalSummary,WriteSummary,VerificationSummary,SuspensionInfo,ExecutionResult)
from nsl.security import ExecutionPrincipal,PrincipalVerification,DataHandlingPolicy
from nsl.ir import NsoCodec,ResourceLimits
from decimal import Decimal

def enc(value):return '~' if value is None else str(value).encode().hex() or '-'
def js(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def observe(action):
    try:
        result=action()
        return 'ok',enc(js(result))
    except Exception as error:
        return type(error).__name__,enc(str(error))
def row(*fields):print(*fields,sep='\t')
def principal():return ExecutionPrincipal('tenant-a','subject-a','USER',frozenset({'reader'}),frozenset({'read'}),'auth:reference',PrincipalVerification.UNVERIFIED)
def check(name='C',status=CheckStatus.PASS):return CheckResult(name,status,'WARN','확인😀','expr0001',Presence.PRESENT,Completeness.COMPLETE,('trace:a',),None)
def summary():return ValidationSummary.from_checks(())
def write(key='idem-short',payload='not-a-payload-hash'):return WriteSummary(CommitState.COMMITTED,'TEST.CREATE','write0001',key,payload,None,None)
def result(status,complete,flags):
    return ExecutionResult('exec-a','TEST','1','sha256:'+'a'*64,status,complete,(check(),),
        (EmitRecord({'z':ValueEnvelope.complete(Decimal('1.2300'),DECIMAL),'a':ValueEnvelope.complete(7,INT)}),),
        ResourceUsage(1,2,3,4),RuntimeErrorInfo('NSL-E8001','EVALUATION','safe message','expr0001','DETAIL'),
        summary() if flags&1 else None,PreWriteEligibility.ALLOWED if flags&2 else None,
        ApprovalSummary(ApprovalStatus.APPROVED,'A','approval-1') if flags&4 else None,
        write() if flags&8 else None,VerificationSummary(VerificationStatus.UNKNOWN,'WAIT','evidence:a') if flags&16 else None,
        SuspensionInfo('WAITING',1,'A','request-a') if flags&32 else None)
class Clock:
    def __init__(self,value=0):self.value=value
    def monotonic_ns(self):return self.value

def models():
    for value in ('',' ','\x1c',' exec-a ','가😀','Bearer synthetic','password=synthetic'):
        for business in (None,'','op-a','token=synthetic'):
            row('identity',enc(value),enc(business),*observe(lambda:(ExecutionIdentity(value,business).execution_id,business)))
    for snapshot,audit in product((-1,0,1,2,30),repeat=2):
        row('policy',snapshot,audit,*observe(lambda:(DataHandlingPolicy(snapshot_retention_days=snapshot,audit_retention_days=audit).max_trace_classification.value,snapshot,audit)))
    for variant in ('valid','verified','tenant','subject','actor','auth','roles','scopes','behalf','credential'):
        p=principal()
        changes={'verified':{'verification':PrincipalVerification.VERIFIED},'tenant':{'tenant_id':' '},'subject':{'subject_id':''},'actor':{'actor_type':'OTHER'},'auth':{'auth_context_ref':''},'roles':{'roles':frozenset({' '})},'scopes':{'scopes':frozenset({''})},'behalf':{'on_behalf_of':' '},'credential':{'auth_context_ref':'Bearer synthetic'}}
        p=replace(p,**changes.get(variant,{}))
        for require in (False,True):row('principal',variant,int(require),*observe(lambda:p.validate(require_verified=require)))
    for location in ('input','context'):
        for key,value in (('x','hello'),('password','synthetic'),('password',''),('password',None),('x','Bearer synthetic'),('x',{'nested':['safe','secret=synthetic']}),('x',{'token':None})):
            fields={key:value}
            row('request',location,enc(key),enc(js(value)),*observe(lambda:ExecutionRequest('exec-a',fields if location=='input' else {},fields if location=='context' else {},principal()).identity.execution_id))
    for execution,business in product(('', ' ', 'exec-a', 'Bearer synthetic'),(None,'','op-a','password=synthetic')):
        row('request_identity',enc(execution),enc(business),*observe(lambda:ExecutionRequest(execution,{}, {},principal(),business_operation_id=business).identity.execution_id))
    for field,value in product(('tool_id','node_id','provider_transaction_id','business_reference'),('', ' ', 'safe','token=synthetic')):
        row('write_field',field,enc(value),*observe(lambda:replace(write(),**{field:value}).to_data()))
    for statuses in (''.join(p) for n in range(5) for p in product('PFU',repeat=n)):
        checks=tuple(check(f'C{i}',{'P':CheckStatus.PASS,'F':CheckStatus.FAIL,'U':CheckStatus.UNKNOWN}[s]) for i,s in enumerate(statuses))
        row('checks',statuses or '-',*observe(lambda:ValidationSummary.from_checks(checks).to_data()))
    for status in ValidationSummaryStatus:
        for total,passed,failed,unknown in ((0,0,0,0),(1,1,0,0),(1,0,1,0),(1,0,0,1),(2,0,1,1),(-1,0,0,0),(2,1,0,0)):
            row('summary',status.value,total,passed,failed,unknown,*observe(lambda:ValidationSummary(status,total,passed,failed,unknown,('F',)*max(0,failed),('U',)*max(0,unknown)).to_data()))
    for field in ('failed','unknown','overlap'):
        for names in (('A','B'),('B','A'),('A','A'),('','B'),(' ','B'),('A',)):
            args=(ValidationSummaryStatus.HAS_FAIL,2,0,2,0,names,()) if field=='failed' else (ValidationSummaryStatus.HAS_UNKNOWN,2,0,0,2,(),names) if field=='unknown' else (ValidationSummaryStatus.HAS_FAIL_AND_UNKNOWN,4,0,2,2,names,('A','B'))
            row('ids',field,enc('\0'.join(names)),*observe(lambda:ValidationSummary(*args).to_data()))
    for name in ('',' ','field'):
        row('emit',enc(name),*observe(lambda:[dict(EmitRecord({name:ValueEnvelope.complete(7,INT)}).values),{k:v.value for k,v in EmitRecord({name:ValueEnvelope.complete(7,INT)}).classifications.items()}]))
    for key,payload in product(('',' ','idem-short','idem:sha256:'+'g'*64,'idem:sha256:'+'a'*64),('','not-a-payload-hash','sha256:'+'b'*64)):
        row('write',enc(key),enc(payload),*observe(lambda:write(key,payload).to_data()))
    for field in ('approval','approval_request','reason','evidence','suspension_reason','suspension_approval','suspension_request'):
        for value in ('',' ','safe','token=synthetic'):
            actions={'approval':lambda:ApprovalSummary(ApprovalStatus.APPROVED,value,None).to_data(),
                'approval_request':lambda:ApprovalSummary(ApprovalStatus.APPROVED,'A',value).to_data(),
                'reason':lambda:VerificationSummary(VerificationStatus.UNKNOWN,value,None).to_data(),
                'evidence':lambda:VerificationSummary(VerificationStatus.UNKNOWN,None,value).to_data(),
                'suspension_reason':lambda:SuspensionInfo(value,1,'A','R').to_data(),
                'suspension_approval':lambda:SuspensionInfo('WAIT',1,value,'R').to_data(),
                'suspension_request':lambda:SuspensionInfo('WAIT',1,'A',value).to_data()}
            row('optional',field,enc(value),*observe(actions[field]))
    for version in (-1,0,1,2):row('suspension',version,*observe(lambda:SuspensionInfo('WAIT',version,'A','R').to_data()))
    for status,complete in product(ExecutionStatus,Completeness):
        r=result(status,complete,0);row('result',status.value,complete.value,0,enc(r.to_json()),enc(js(r.semantic_view())))
    for flags in range(1,64):
        r=result(ExecutionStatus.COMPLETED,Completeness.PARTIAL,flags);row('result','COMPLETED','PARTIAL',flags,enc(r.to_json()),enc(js(r.semantic_view())))

def guards():
    for limit in (-1,0,1,2,3):
        for action in ('tool','loop','emit','observe','collection'):
            run_guard(f'{action}:{limit}',(limit,limit,limit,limit,10),(0,0,0,0),0,[f'{action}:{i}' for i in range(-1,5)])
    for duration in (0,1,2):
        for elapsed in (0,1,999999,1000000,1000001,1999999,2000000):
            run_guard(f'deadline:{duration}:{elapsed}',(1,1,1,1,duration),(0,0,0,0),10,[f'time:{10+elapsed}','remaining:0','deadline:0','tool:0','loop:0','emit:0','observe:2'])
    wide=10**100
    run_guard('wide',(wide,wide,wide,wide,wide),(wide-1,wide-1,wide-1,wide-1),-wide,['remaining:0','tool:0','tool:0','loop:0','loop:0','emit:0','emit:0','record:0','emit:0',f'observe:{wide+1}'])
    run_guard('emit_split',(1,1,1,1,1),(0,0,0,0),0,['emit:0','emit:0','record:0','record:0','emit:0','time:1000000','record:0','observe:2'])

def run_guard(label,limits,usage,start,actions):
    clock=Clock(start);meter=_ResourceMeter(*usage);guard=ResourceGuard(ResourceLimits(*limits),meter,clock);observed=[]
    for action in actions:
        kind,number=action.split(':');number=int(number)
        try:
            if kind=='time':clock.value=number;value=None
            elif kind=='remaining':value=str(guard.remaining_duration_ms())
            else:
                {'deadline':guard.check_deadline,'tool':guard.before_tool_call,'loop':guard.before_loop_iteration,'emit':guard.before_emit,'record':guard.record_emit,'observe':lambda:guard.observe_collection(number),'collection':lambda:guard.check_collection(number)}[kind]();value=None
            outcome='ok';message=value
        except Exception as error:outcome=type(error).__name__;message=str(error)
        observed.append([outcome,message,[meter.tool_calls,meter.loop_iterations,meter.emitted_rows,meter.max_collection_size_seen]])
    row(label,','.join(map(str,limits)),','.join(map(str,usage)),start,';'.join(actions),enc(js(observed)))

def contexts():
    base=Path(__file__).resolve().parents[5]/'compatibility/v0_2_1/nso/ir-1-0-canonical/expected.nso'
    skill=NsoCodec.decode(base.read_bytes());
    sequences=[]
    for a,b in product('icvk',repeat=2):sequences.append([f'bind:{a}:S:1',f'bind:{b}:S:2','resolve:S'])
    sequences += [['resolve:S','pop'],['bind:i:I:1','bind:c:C:2','bind:v:V:3','bind:k:K:4','push','bind:v:X:5','resolve:X','pop','resolve:X','push','bind:k:X:6','pop','push','bind:v:X:7','resolve:I','resolve:C','resolve:V','resolve:K'],['observe:PARTIAL','observe:COMPLETE','observe:UNKNOWN','observe:PARTIAL']]
    for observed,content in product(Completeness,('empty','check','output','both')):
        sequences.append([f'observe:{observed.value}',f'content:{content}',*[f'result:{s.value}' for s in ExecutionStatus]])
    for index,actions in enumerate(sequences):
        ctx=ExecutionContext(skill,ExecutionRequest('exec-a',{}, {},principal()),Clock());observations=[]
        for action in actions:
            parts=action.split(':');kind=parts[0]
            try:
                value=None
                if kind=='bind':getattr(ctx,{'i':'bind_input','c':'bind_context','v':'bind','k':'bind_check'}[parts[1]])(parts[2],ValueEnvelope.complete(int(parts[3]),INT))
                elif kind=='resolve':value=str(ctx.resolve(parts[1]).value)
                elif kind=='push':ctx.push_frame()
                elif kind=='pop':ctx.pop_frame()
                elif kind=='observe':ctx.observe_completeness(Completeness(parts[1]))
                elif kind=='content':
                    if parts[1] in ('check','both'):ctx.checks.append(check())
                    if parts[1] in ('output','both'):ctx.outputs.append(EmitRecord({}))
                elif kind=='result':value=ctx.result_completeness(ExecutionStatus(parts[1])).value
                outcome='ok';message=value
            except Exception as error:outcome=type(error).__name__;message=str(error)
            observations.append([outcome,message,len(ctx.frames),ctx.data_completeness.value])
        row(index,';'.join(actions),enc(js(observations)))

print('# Python Oracle 28a949ec96581cbdc52c8133527b23bb5b72ee0a')
{'models':models,'guards':guards,'contexts':contexts}[sys.argv[2]]()
