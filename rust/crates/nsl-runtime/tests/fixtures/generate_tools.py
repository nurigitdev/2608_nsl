"""R6-S02: observations of unmodified Python Oracle 28a949e (Python 3.12)."""
import asyncio
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import sys
sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
from nsl.core import (INT, BOOL, STRING, TypeRef, ValueEnvelope, Money, encode_value,
    Presence, Completeness, DataClassification)
from nsl.ir import canonical_json
from nsl.security import ExecutionPrincipal, PrincipalVerification
from nsl.tools import (ToolContract, ToolContractCatalog, ToolCallRequest, ToolResultEnvelope,
    ToolContractValidator, tool_result_hash, value_conforms_to_type, MockToolResult,
    MockToolExecutor, ToolExecutionMeasurement, ToolExecutionOutcome, MeasuringToolExecutor,
    InMemoryToolMeasurementSink, execute_with_timeout, ToolExecutionError)

def emit(*fields):
    *inputs, result = fields
    print(*inputs, canonical_json(result).hex(), sep='\t')
def observed(action):
    try:
        action(); return ['ok', None]
    except Exception as e:
        return [getattr(e, 'code', type(e).__name__), str(e)]
def principal():
    return ExecutionPrincipal('tenant-a','subject-a','USER',frozenset({'reader'}),
        frozenset({'read'}),'auth:reference',PrincipalVerification.UNVERIFIED)
def contract(output=INT):
    return ToolContract('TEST.ECHO','1.0.0','READ',(('z',INT),('a',INT)),output,'read',DataClassification.INTERNAL)
def request(c=None):
    c = c or contract()
    return ToolCallRequest('exec-a','inv-a','read0001',c.tool_id,c.version,c.contract_hash,
        {'a':ValueEnvelope.complete(7,INT),'z':ValueEnvelope.complete(9,INT)},principal(),'authz:a')
def result(value=7, ty=INT):
    return ToolResultEnvelope('inv-a','TEST.ECHO','1.0.0',value,ty,Presence.PRESENT,
        Completeness.COMPLETE,DataClassification.INTERNAL,tool_result_hash(value))
def types():
    p=lambda s:TypeRef('primitive',name=s)
    opt=TypeRef('optional',item=INT)
    return {**{s:p(s) for s in ('Bool','Date','DateTime','Decimal','Int','String','Year','Unknown')},
        'Domain':TypeRef('domain',name='Customer'), 'USD':TypeRef('money',currency='USD'),
        'EUR':TypeRef('money',currency='EUR'), 'ListInt':TypeRef('list',item=INT),
        'Record':TypeRef('record',name='R',fields=(('a',INT),)),
        'Duplicate':TypeRef('record',name='R',fields=(('a',STRING),('a',INT))),
        'EmptyRecord':TypeRef('record',name='R'),
        'Enum':TypeRef('enum',name='E',enum_values=('YES','NO')),
        'Optional':opt,'ListOptional':TypeRef('list',item=opt),
        'RecordOptional':TypeRef('record',name='R',fields=(('a',opt),))}
def values():
    return {'null':None,'false':False,'true':True,'zero':0,'negative':-1,'seven':7,
        'wide':10**100,'string':'가😀','empty_string':'','enum':'YES',
        'decimal':Decimal('1.2300'),'negative_zero':Decimal('-0.00'),
        'money':Money(Decimal('12.500'),'USD'),'eur':Money(Decimal('12.5'),'EUR'),
        'date':date(2026,11,18),'datetime':datetime(2026,11,18,1,2,3),
        'list_empty':[],'list_int':[1,2],'list_bool':[True],'list_null':[None],
        'record_empty':{},'record_int':{'a':7},'record_string':{'a':'x'},
        'record_other':{'b':7},'record_null':{'a':None},'record_extra':{'a':7,'b':8},
        'nested':{'z':[Decimal('0.0100'),{'x':'확인😀'}],'a':False}}

def tools():
    for t,ty in types().items():
        for v,value in values().items():
            emit('conform',t,v,value_conforms_to_type(value,ty))
    for name,value in values().items(): emit('hash',name,tool_result_hash(value))
    validator=ToolContractValidator(); c=contract(); r=request()
    for variant in ('valid','hash','hash_and_names','missing','extra','renamed','type','value','both_values','identity','empty_auth'):
        q=r
        if variant in ('hash','hash_and_names'):q=replace(q,contract_hash='wrong')
        if variant in ('missing','hash_and_names'):q=replace(q,arguments={})
        if variant=='extra':q=replace(q,arguments={**q.arguments,'extra':ValueEnvelope.complete(1,INT)})
        if variant=='renamed':q=replace(q,arguments={'z':q.arguments['z'],'other':q.arguments['a']})
        if variant=='type':q=replace(q,arguments={**q.arguments,'a':ValueEnvelope.complete(7,STRING)})
        if variant=='value':q=replace(q,arguments={**q.arguments,'a':ValueEnvelope.complete(True,INT)})
        if variant=='both_values':q=replace(q,arguments={k:ValueEnvelope.complete(True,INT) for k in ('a','z')})
        if variant=='identity':q=replace(q,tool_id='OTHER',tool_version='9.0.0')
        if variant=='empty_auth':q=replace(q,authorization_decision_ref='')
        emit('request',variant,observed(lambda:validator.validate_request(q,c)))
    for variant in ('valid','none','invocation','tool','version','snapshot_empty','snapshot_blank','snapshot_value','identity_and_snapshot'):
        s=result()
        if variant=='none':s=None
        if variant in ('invocation','identity_and_snapshot'):s=replace(s,invocation_id='other')
        if variant=='tool':s=replace(s,tool_id='OTHER')
        if variant=='version':s=replace(s,tool_version='2.0.0')
        if variant in ('snapshot_empty','identity_and_snapshot'):s=replace(s,snapshot_ref='')
        if variant=='snapshot_blank':s=replace(s,snapshot_ref=' ')
        if variant=='snapshot_value':s=replace(s,snapshot_ref='snapshot:a')
        emit('structure',variant,observed(lambda:validator.validate_result_structure(s,r)))
    for variant in ('valid','type','class','value','metadata_and_value','presence','completeness','hash','hash_shape'):
        s=result()
        if variant in ('type','metadata_and_value'):s=replace(s,type_info=STRING)
        if variant=='class':s=replace(s,classification=DataClassification.RESTRICTED)
        if variant in ('value','metadata_and_value'):s=replace(s,value=True)
        if variant=='presence':s=replace(s,presence=Presence.EMPTY)
        if variant=='completeness':s=replace(s,completeness=Completeness.PARTIAL)
        if variant in ('hash','hash_shape'):s=replace(s,result_hash='sha256:'+'0'*64 if variant=='hash' else '')
        emit('result',variant,observed(lambda:validator.validate_result(s,c)))
        emit('result_hash',variant,observed(lambda:validator.validate_result_hash(s)))
    s=result().to_value('trace:a')
    emit('provenance','single',[encode_value(s.value),s.presence,s.completeness,s.classification,list(s.provenance_refs)])
    async def mocks():
        for variant in ('valid','partial','unknown','empty_list','empty_record','wrong_output','handler_error','missing_handler','missing_contract','version','bad_request','repeat'):
            out=TypeRef('list',item=INT) if variant=='empty_list' else types()['EmptyRecord'] if variant=='empty_record' else INT
            c=contract(out);q=request(c)
            value=[] if variant=='empty_list' else {} if variant=='empty_record' else True if variant=='wrong_output' else 7
            comp=Completeness.PARTIAL if variant=='partial' else Completeness.UNKNOWN if variant=='unknown' else Completeness.COMPLETE
            def handler(args):
                assert args=={'a':7,'z':9}
                if variant=='handler_error':raise RuntimeError('synthetic handler failure')
                return MockToolResult(value,comp)
            e=MockToolExecutor(ToolContractCatalog(()) if variant=='missing_contract' else ToolContractCatalog((c,)),{} if variant=='missing_handler' else {c.tool_id:handler})
            if variant=='version':q=replace(q,tool_version='2.0.0')
            if variant=='bad_request':q=replace(q,contract_hash='wrong')
            observations=[]
            for _ in range(2 if variant=='repeat' else 1):
                try:
                    s=await e.execute(q)
                    observations.append(['ok',encode_value(s.value),s.presence,s.completeness,s.classification,s.result_hash,s.snapshot_ref,e.call_count])
                except Exception as error:observations.append([getattr(error,'code',type(error).__name__),str(error),e.call_count])
            emit('mock',variant,observations)
    asyncio.run(mocks())
    for field in ('execution_id','invocation_id','node_id','tool_id','tool_version','tenant_id'):
        for text in ('',' ','\x1c','safe',' 가😀 '):
            args=dict(execution_id='exec-a',invocation_id='inv-a',node_id='read0001',tool_id='TEST.ECHO',tool_version='1.0.0',tenant_id='tenant-a',outcome=ToolExecutionOutcome.RETURNED,duration_ns=0)
            args[field]=text
            emit('measurement_id',field,text.encode().hex() or '-',observed(lambda:ToolExecutionMeasurement(**args)))
    for n in (-1,0,1,999999,1000000,1000001,10**100):
        for outcome in ToolExecutionOutcome:
            try:
                m=ToolExecutionMeasurement('exec-a','inv-a','read0001','TEST.ECHO','1.0.0','tenant-a',outcome,n)
                observed_value=['ok',m.duration_ms]
            except ValueError as error:observed_value=['ValueError',str(error)]
            emit('measurement_duration',str(n),outcome.value,observed_value)

class Clock:
    now=0
    def monotonic_ns(self):return self.now
async def timeouts():
    for mode in ('propagate','suppress','raise_error','provider_timeout','sink_error'):
        clock=Clock();events=[];cancel_seen=asyncio.Event();release=asyncio.Event()
        class Provider:
            async def execute(self,request):
                events.append('started');clock.now=1000000
                try:await asyncio.Future()
                except asyncio.CancelledError:
                    events.append('cancel_received');clock.now=2000000;cancel_seen.set()
                    await release.wait();events.append('cleanup_completed');clock.now=3000000
                    if mode in ('propagate','sink_error'):raise
                    if mode=='raise_error':raise RuntimeError('synthetic cleanup failure')
                    if mode=='provider_timeout':raise TimeoutError()
                    return result()
        class Sink(InMemoryToolMeasurementSink):
            def record(self,m):
                super().record(m)
                if mode=='sink_error':raise RuntimeError('synthetic sink failure')
        sink=Sink();e=MeasuringToolExecutor(Provider(),clock,sink)
        task=asyncio.create_task(execute_with_timeout(e,request(),1))
        await asyncio.wait_for(cancel_seen.wait(),2)
        pending=not task.done();release.set()
        try:s=await task;outcome=['ok',s.value]
        except Exception as error:outcome=[getattr(error,'code',type(error).__name__),str(error)]
        emit('cleanup',mode,[pending,events,outcome,[[m.outcome,m.duration_ns,m.duration_ms] for m in sink.measurements]])
    for mode in ('ok','error','cancelled','provider_timeout','backward','sink_error'):
        clock=Clock();clock.now=1000000;sink=InMemoryToolMeasurementSink()
        class Provider:
            async def execute(self,request):
                clock.now=0 if mode=='backward' else 2999999
                if mode=='error':raise RuntimeError('synthetic provider failure')
                if mode=='provider_timeout':raise TimeoutError()
                if mode=='cancelled':raise asyncio.CancelledError()
                return result()
        class FailingSink(InMemoryToolMeasurementSink):
            def record(self,m):super().record(m);raise RuntimeError('synthetic sink failure')
        if mode=='sink_error':sink=FailingSink()
        e=MeasuringToolExecutor(Provider(),clock,sink)
        try:s=await execute_with_timeout(e,request(),1);outcome=['ok',s.value]
        except asyncio.CancelledError:outcome=['CancelledError','']
        except Exception as error:outcome=[getattr(error,'code',type(error).__name__),str(error)]
        emit('immediate',mode,[outcome,[[m.outcome,m.duration_ns,m.duration_ms] for m in sink.measurements]])
    for n in (-1,0,1,2147483647):
        class Provider:
            calls=0
            async def execute(self,request):self.calls+=1;return result()
        e=Provider()
        try:s=await execute_with_timeout(e,request(),n);outcome=['ok',s.value]
        except Exception as error:outcome=[getattr(error,'code',type(error).__name__),str(error)]
        emit('timeout_bound',n,[outcome,e.calls])

print('# Python Oracle 28a949ec96581cbdc52c8133527b23bb5b72ee0a')
if sys.argv[2]=='tools':tools()
else:asyncio.run(timeouts())
