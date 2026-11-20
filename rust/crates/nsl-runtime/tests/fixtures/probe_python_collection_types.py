"""Synthetic type-boundary probe. No business provider or external I/O."""
import asyncio,json,sys
from dataclasses import replace
from pathlib import Path
sys.path.insert(0,str(Path(sys.argv[1]).resolve()))
from nsl.core import INT,STRING,DataClassification,list_type,record_type
from nsl.ir import NsoCodec,InputSpec,OutputField,SymbolSpec,SymbolRefExpr,ForeachStatement,EmitStatement,LiteralExpr
from nsl.runtime import RuntimeEngine
from nsl.runtime_models import ExecutionRequest
from nsl.security import ExecutionPrincipal,PrincipalVerification
from nsl.tools import ToolContractCatalog,MockToolExecutor
from nsl.audit import InMemoryAuditSink
root=Path(__file__).resolve().parents[5]
principal=ExecutionPrincipal('tenant-a','subject-a','USER',frozenset(),frozenset({'nsl:skill:execute'}),'auth:reference',PrincipalVerification.VERIFIED)
catalog=ToolContractCatalog(())
async def main():
 for version in ('1-0','1-1'):
  base=NsoCodec.decode((root/f'compatibility/v0_2_1/nso/ir-{version}-canonical/expected.nso').read_bytes())
  for name,declared,value in (
    ('foreach_string_as_list',list_type(INT),'ab'),
    ('foreach_wrong_list_element',list_type(INT),['not-an-int']),
    ('emit_list_wrong_element',list_type(INT),['not-an-int']),
    ('emit_record_wrong_field',record_type('R',n=INT),{'n':'not-an-int'}),
    ('emit_record_extra_field',record_type('R',n=INT),{'n':1,'extra':'unexpected'}),
    ('emit_list_scalar',list_type(INT),17),
  ):
   ref=SymbolRefExpr('input-ref','s-input',declared)
   if name.startswith('foreach'):
    body=(ForeachStatement('foreach','s-item',ref,4,(EmitStatement('emit',(('value',LiteralExpr('constant',7,INT)),)),)),)
    output_type=INT
    symbols=(SymbolSpec('s-input','items','INPUT',declared,DataClassification.INTERNAL),SymbolSpec('s-item','item','ITERATOR',INT,DataClassification.INTERNAL))
   else:
    body=(EmitStatement('emit',(('value',ref),)),)
    output_type=declared
    symbols=(SymbolSpec('s-input','items','INPUT',declared,DataClassification.INTERNAL),)
   skill=replace(base,required_tools=(),contexts=(),symbols=symbols,
     inputs=(InputSpec('s-input','items',declared,True,DataClassification.INTERNAL),),
     outputs=(OutputField('value',output_type,DataClassification.INTERNAL),),body=body,
     analysis=replace(base.analysis,max_tool_calls=0,max_loop_iterations=4,max_emit_records=4),
     limits=replace(base.limits,tool_calls=1,loop_iterations=10,emitted_rows=10,collection_size=10,duration_ms=60000)).with_computed_hash()
   admitted=NsoCodec.decode(NsoCodec.encode(skill))
   provider=MockToolExecutor(catalog,{})
   result=await RuntimeEngine(catalog).execute(admitted,ExecutionRequest('exec-type-probe',{'items':value},{},principal),provider,InMemoryAuditSink())
   print(json.dumps({'source':str(Path(sys.argv[1]).resolve()),'oracle_reference':'28a949ec96581cbdc52c8133527b23bb5b72ee0a','ir_version':admitted.ir_version,
    'case':name,'nso_admitted':True,'input':value,'status':result.status.value,
    'outputs':[dict(row.values) for row in result.outputs],'iterations':result.resources.loop_iterations,
    'emitted_rows':result.resources.emitted_rows,'provider_calls':provider.call_count,
    'error':None if result.error is None else result.error.message},ensure_ascii=False,sort_keys=True))
asyncio.run(main())
