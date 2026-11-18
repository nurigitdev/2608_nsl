"""R6-S04 observations: unmodified Oracle; separate approved security exceptions."""
import asyncio,importlib.util,json,sys
from dataclasses import replace
from pathlib import Path
from itertools import product
spec=importlib.util.spec_from_file_location('expr_fixture',Path(__file__).with_name('generate_expressions.py'))
e=importlib.util.module_from_spec(spec);spec.loader.exec_module(e)
from nsl.core import INT,DataClassification,Completeness,Presence,ValueEnvelope,RiskLevel,encode_value
from nsl.ir import RequiredTool,NsoCodec,canonical_json
from nsl.tools import (ToolContract,ToolContractCatalog,ToolResultEnvelope,ToolExecutionError,tool_result_hash,
    WriteSemantics,IdempotencySemantics,CommitSemantics,CommitConfirmation,Atomicity,OperationKind)
from nsl.runtime import RuntimeEngine
from nsl.runtime_models import ExecutionContext,ExecutionRequest
from nsl.security import ExecutionPrincipal,PrincipalVerification,StaticAuthorizer

def contract(output='Int',cap='READ',scope='read',timeout=30000,tool='TEST.READ'):
    kwargs={}
    if cap=='WRITE':kwargs=dict(operation_kind=OperationKind.CREATE,risk_level=RiskLevel.R2,
        write_semantics=WriteSemantics(True,Atomicity.ATOMIC,IdempotencySemantics(True,'tenant','P1D','RETURN_PREVIOUS','REJECT'),CommitSemantics(CommitConfirmation.EXPLICIT),True,True))
    return ToolContract(tool,'1.0.0',cap,() if tool=='TEST.INNER' else (('z',INT),('a',INT)),e.types[output],scope,DataClassification.INTERNAL,timeout_ms=timeout,**kwargs)
def read(flags=5,ref='t0',ty='Int',args=None,node='read0'):
    return (';'.join(args) if args else '')+(';' if args else '')+f'R,{ref},{"z+a" if args else "-"},{flags},{ty},{node}'
def normal(flags=5,ty='Int'):return read(flags,ty=ty,args=[e.l('seven',node='z'),e.l('one',node='a')])
class Authorizer(StaticAuthorizer):
    def __init__(self):super().__init__();self.calls=[]
    def authorize(self,principal,action,scopes):
        self.calls.append([action,sorted(scopes)]);return super().authorize(principal,action,scopes)
def envelope(v):return [encode_value(v.value),v.type_info.to_data(),v.presence,v.completeness,v.classification,list(v.provenance_refs)]
async def observe(version,program,output,value,comp,mutation,scopes,clock_script,collection,tool_limit,seed,setup):
    cap='WRITE' if setup in ('write','write_scope') else 'CUSTOM' if setup=='custom' else 'TRANSACTION' if setup=='transaction' else 'READ'
    actual_scope='admin' if setup=='scope' else 'write' if setup=='write_scope' else '' if setup=='empty_scope' else 'read'
    c=contract(output,cap,actual_scope,timeout=1 if setup=='short_timeout' else 30000);inner=contract(tool='TEST.INNER')
    declared_cap='WRITE' if setup=='declared_write' else 'READ'
    required_scope='read' if setup in ('scope','write_scope') else actual_scope
    catalog=ToolContractCatalog(() if setup=='missing_catalog' else (replace(c,version='2.0.0'),inner) if setup=='version' else (c,inner))
    base=NsoCodec.decode((Path(__file__).resolve().parents[5]/f'compatibility/v0_2_1/nso/ir-{version}-canonical/expected.nso').read_bytes())
    required=RequiredTool('t0',c.tool_id,c.version,declared_cap,'sha256:'+'0'*64 if setup=='hash' else c.contract_hash,required_scope,c.output_classification,RiskLevel.R0 if version=='1-1' else None)
    required_inner=RequiredTool('t1',inner.tool_id,inner.version,'READ',inner.contract_hash,'read',DataClassification.INTERNAL,RiskLevel.R0 if version=='1-1' else None)
    skill=replace(base,required_tools=(required,required_inner),limits=replace(base.limits,collection_size=collection,tool_calls=tool_limit,duration_ms=10))
    principal=ExecutionPrincipal('tenant-a','subject-a','USER',frozenset(),frozenset(scopes.split('+') if scopes!='-' else ()),'auth:reference',PrincipalVerification.VERIFIED)
    clock=e.Clock(clock_script);ctx=ExecutionContext(skill,ExecutionRequest('exec-a',{}, {},principal),clock);ctx.invocation_counter=int(seed)
    authorizer=Authorizer();engine=RuntimeEngine(catalog,authorizer=authorizer);requests=[]
    class Provider:
        async def execute(self,request):
            requests.append([request.execution_id,request.invocation_id,request.node_id,request.tool_id,request.tool_version,request.contract_hash,
                {k:envelope(v) for k,v in request.arguments.items()},request.principal.tenant_id,request.principal.subject_id,sorted(request.principal.scopes),request.authorization_decision_ref])
            if mutation=='provider_error':raise RuntimeError('synthetic provider failure')
            if mutation=='provider_timeout':raise TimeoutError()
            if mutation=='tool_timeout':raise ToolExecutionError('TOOL_TIMEOUT','synthetic timeout')
            v=7 if request.tool_id=='TEST.INNER' else e.values[value]
            contract_used=inner if request.tool_id=='TEST.INNER' else c
            result=ToolResultEnvelope(request.invocation_id,request.tool_id,request.tool_version,v,contract_used.output_type,
                Presence.EMPTY if v==[] or v is None else Presence.PRESENT,Completeness(comp),DataClassification.INTERNAL,tool_result_hash(v),None)
            changes={'invocation':dict(invocation_id='wrong'),'tool':dict(tool_id='wrong'),'version':dict(tool_version='2.0.0'),
                'type':dict(type_info=e.types['String']),'class':dict(classification=DataClassification.RESTRICTED),
                'bad_value':dict(value=True),'hash':dict(result_hash='sha256:'+'0'*64),'hash_empty':dict(result_hash=''),
                'snapshot_empty':dict(snapshot_ref=''),'snapshot_blank':dict(snapshot_ref=' '),'snapshot':dict(snapshot_ref='snapshot:a'),
                'force_empty':dict(presence=Presence.EMPTY)}
            return replace(result,**changes.get(mutation,{}))
    expr=e.parse(program)
    try:result=await engine._evaluate(ctx,expr,Provider());out=['ok',envelope(result)]
    except Exception as error:out=[type(error).__name__,str(error),getattr(error,'node_id',None),getattr(error,'detail_code',None),getattr(error,'code',None),getattr(error,'decision_ref',None)]
    r=ctx.resources
    return [out,ctx.data_completeness,[r.tool_calls,r.loop_iterations,r.emitted_rows,r.max_collection_size_seen],ctx.invocation_counter,clock.calls,authorizer.calls,requests]
async def row(version='1-0',program=None,output='Int',value='seven',comp='COMPLETE',mutation='valid',scopes='read',clock='0',collection=100,tool_limit=10,seed='0',setup='valid'):
    program=program or normal(ty=output)
    fields=(version,program,output,value,comp,mutation,scopes,clock,collection,tool_limit,seed,setup)
    print(*fields,canonical_json(await observe(*fields)).hex(),sep='\t')
async def main():
    print('# Python Oracle 28a949ec96581cbdc52c8133527b23bb5b72ee0a')
    if sys.argv[2]=='auth':
        for variant,action,required,granted in (
            ('valid','tool:TEST.READ:execute','read','read'),('valid',' ','read','read'),('valid','tool:TEST.READ:execute','-','read'),
            ('valid','tool:TEST.READ:execute',' ','read'),('valid','tool:TEST.READ:execute','read+z','read'),
            ('valid','tool:TEST.READ:execute','read+z+가','read+z+가'),('valid','tool:TEST.READ:execute','read+z+가','-'),
            ('tenant','tool:TEST.READ:execute','read','read'),('actor','tool:TEST.READ:execute','read','read'),('credential','tool:TEST.READ:execute','read','read')):
            p=replace(e.principal,scopes=frozenset(granted.split('+') if granted!='-' else ()))
            if variant=='tenant':p=replace(p,tenant_id='')
            if variant=='actor':p=replace(p,actor_type='OTHER')
            if variant=='credential':p=replace(p,auth_context_ref='Bearer synthetic')
            try:d=StaticAuthorizer().authorize(p,action,frozenset(required.split('+') if required!='-' else ()));out=['ok',d.decision_id]
            except Exception as error:out=[type(error).__name__,str(error),getattr(error,'decision_ref',None)]
            print(variant,action.encode().hex(),required.encode().hex(),granted.encode().hex(),canonical_json(out).hex(),sep='\t')
        return
    if sys.argv[2]=='security':
        for version,setup,scopes in product(('1-0','1-1'),('write','write_scope','scope','custom','transaction','declared_write'),('read','admin+read')):
            if version=='1-0' and setup=='declared_write':continue
            await row(version=version,setup=setup,scopes=scopes)
        return
    for version,flags,complete,value in product(('1-0','1-1'),range(8),Completeness,('seven','list0','ints','record_empty')):
        output='ListInt' if value in ('list0','ints') else 'EmptyRecord' if value=='record_empty' else 'Int'
        await row(version,normal(flags,output),output,value,complete.value)
    for version,mutation in product(('1-0','1-1'),('invocation','tool','version','type','class','bad_value','hash','hash_empty','snapshot_empty','snapshot_blank','snapshot','force_empty','provider_error','provider_timeout','tool_timeout')):
        await row(version=version,mutation=mutation)
    for setup in ('missing_catalog','version','hash','empty_scope'):
        await row(setup=setup)
    for mutation in ('valid','provider_timeout','tool_timeout'):await row(setup='short_timeout',mutation=mutation)
    for scopes in ('-','other','READ','read+other'):
        await row(scopes=scopes)
    for ref in ('missing','t0'):
        await row(program=read(ref=ref))
    for names in ('z+z','z+a+z','z+x','z','-'):
        n=0 if names=='-' else len(names.split('+'));args=[e.l('one',node=f'arg{i}') for i in range(n)]
        await row(program=';'.join(args+[f'R,t0,{names},5,Int,read0']))
    await row(program=normal().replace('L,seven,Int,z','L,a,String,z'))
    await row(program=normal().replace('L,seven,Int,z','S,missing,Int,z'))
    for seed in ('-2','-1','0','998','999','9998','9999','1'+'0'*100):await row(seed=seed)
    for limit in (-1,0,1,2):await row(tool_limit=limit)
    for size in (0,1,2,3,4):await row(output='ListInt',value='ints',collection=size,comp='PARTIAL')
    for script in ('0,10000000','0,0,10000000','0,0,0,10000000','0,0,0,0,10000000','0,0,0,0,0,10000000','0,0,0,0,0,0,10000000','0,0,0,0,0,0,0,10000000','0,0,0,0,0,0,0,0,10000000','0,0,0,0,0,0,0,0,0,10000000'):
        await row(clock=script)
    await row(program=normal(ty='String'))
    await row(program=normal(ty='ListInt')+';C,sum,1,Int,total',output='ListInt',value='ints',comp='PARTIAL')
    await row(program=read(ref='t1',node='inner')+';'+e.l('one',node='a')+';R,t0,z+a,5,Int,outer')
    await row(program=normal()+';'+normal().replace('read0','read1')+';B,ADD,Int,total')
    await row(program=normal()+';'+normal().replace('read0','read1')+';B,ADD,Int,total',tool_limit=1)
asyncio.run(main())
