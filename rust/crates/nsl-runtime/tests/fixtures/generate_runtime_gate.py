"""R6-S07 full RuntimeEngine observations from an explicitly selected Oracle.

Modes: normal, admission, summary, security. No expectation patching; only
in-memory providers. argv[1] is the pinned Python archive.
"""
import asyncio,contextlib,importlib.util,io,json,sys
from dataclasses import asdict,replace
from itertools import product
from pathlib import Path
spec=importlib.util.spec_from_file_location('statement_fixture',Path(__file__).with_name('generate_statements.py'))
g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
e=g.e
from nsl.core import CHECK_RESULT,BusinessOperationId,Completeness,DataClassification,RiskLevel
from nsl.ir import (ApprovalPolicy,ApprovalStatement,InputSpec,ContextSpec,SymbolSpec,RequiredTool,WriteExpression,
                    LetStatement,CheckStatement,EmitStatement,NsoCodec,canonical_json)
from nsl.runtime import RuntimeEngine
from nsl.runtime_models import ExecutionRequest
from nsl.security import PrincipalVerification,RuntimeEnvironment
from nsl.audit import InMemoryAuditSink
from nsl.tools import (ToolContract,ToolContractCatalog,ToolResultEnvelope,ToolExecutionError,ToolExecutionMeasurement,
                       WriteSemantics,IdempotencySemantics,Atomicity,CommitSemantics,CommitConfirmation,OperationKind)
e.values['secret']='CONFIDENTIAL-PROJECT'
e.values['int2']=[1,2]

def write_contract():
 return ToolContract('TEST.WRITE','1.0.0','WRITE',(),e.types['Int'],'write',DataClassification.INTERNAL,
  operation_kind=OperationKind.CREATE,risk_level=RiskLevel.R2,write_semantics=WriteSemantics(True,Atomicity.ATOMIC,
  IdempotencySemantics(True,'tenant','P1D','RETURN_PREVIOUS','REJECT'),CommitSemantics(CommitConfirmation.EXPLICIT),True,True))

def check(id='rule',condition=None):
 return CheckStatement('check-'+id,id,g.literal('true','Bool') if condition is None else condition,'ERROR','CONTINUE','검증😀','result-'+id)

async def row(case, version='1-0', mode=None, statements=None, outputs=None, bindings='-',
              tool_output='Int',tool_value='seven',comp='COMPLETE',provider='valid',scopes='nsl:skill:execute+read+write',
              clock='0',limits='100,100,100,100,100',mutation='-',principal='valid',environment='production',business='yes',extras='-',catalog='valid',debug='off',template='-'):
 mode=mode or ('read' if version=='1-0' else 'prefix')
 # Existing fixture builders supply validated canonical NSO and controlled IR.
 with contextlib.redirect_stdout(io.StringIO()) as captured:
  await g.row(case,version,statements=(g.emit(g.read(tool_output)),) if statements is None else statements,
              outputs=(g.output(tool_output),) if outputs is None else outputs,bindings=bindings,
              tool_output=tool_output,tool_value=tool_value,comp=comp,provider_mutation='valid',limits=limits)
 old=captured.getvalue().strip().split('\t')
 s=NsoCodec.decode(bytes.fromhex(old[3]))
 inputs=[];contexts=[];symbols=list(s.symbols);input_values={};context_values={}
 for token in ([] if bindings=='-' else bindings.split(';')):
  ns,target,value,ty,presence,complete,classification,refs=token.split(',')
  classification=DataClassification(classification)
  symbols=[sym for sym in symbols if sym.symbol_id!=target]
  symbols.append(SymbolSpec(target,target.replace('-','_'),'CONTEXT' if ns=='c' else 'INPUT',e.types[ty],classification))
  if ns=='c':
   contexts.append(ContextSpec(target,target,e.types[ty],('nested',target),classification));context_values.setdefault('nested',{})[target]=e.values[value]
  else:
   inputs.append(InputSpec(target,target,e.types[ty],True,classification));input_values[target]=e.values[value]
 body=s.body;required=list(s.required_tools)
 c=g.contract(tool_output);inner=g.contract(inner=True);write=write_contract()
 if catalog in ('scope','write','custom','transaction'):
  c=replace(c,required_scope='admin') if catalog=='scope' else replace(write,tool_id='TEST.READ',input_types=c.input_types) if catalog=='write' else replace(c,capability=catalog.upper())
  required[0]=replace(required[0],contract_hash=c.contract_hash)
 if mode in ('prefix','controlled'):
  approval=ApprovalStatement('approval','approval-id','approve',ApprovalPolicy())
  body=body+(approval,LetStatement('write-stmt','write-result',WriteExpression('write','tw',(),'approval-id',e.types['Int'])),g.emit(g.read(),'suffix','suffix'))
  required.append(RequiredTool('tw',write.tool_id,write.version,'WRITE',write.contract_hash,'write',DataClassification.INTERNAL,RiskLevel.R2))
  symbols.append(SymbolSpec('write-result','write_result','VARIABLE',e.types['Int'],DataClassification.INTERNAL))
 s=replace(s,body=body,inputs=tuple(inputs),contexts=tuple(contexts),symbols=tuple(symbols),required_tools=tuple(required)).with_computed_hash()
 wire=NsoCodec.encode(s)
 if mutation=='hash':s=replace(s,semantic_hash='sha256:'+'0'*64)
 elif mutation=='profile':s=replace(s,semantics_profile='UNSUPPORTED')
 elif mutation=='language':s=replace(s,language_version='9.9')
 elif mutation=='unbounded':s=replace(s,analysis=replace(s.analysis,bounded=False))
 elif mutation=='required-hash':s=replace(s,required_tools=(replace(s.required_tools[0],contract_hash='sha256:'+'0'*64),)+s.required_tools[1:])
 elif mutation=='optional-missing':s=replace(s,inputs=(replace(s.inputs[0],required=False),)+s.inputs[1:]);input_values.pop(s.inputs[0].name)
 elif mutation=='input-missing':input_values.pop(s.inputs[0].name)
 elif mutation=='context-missing':context_values={}
 elif mutation=='business-missing':business='no'
 elif mutation=='emit-declaration':s=replace(s,outputs=(replace(s.outputs[0],type_info=e.types['String']),)+s.outputs[1:])
 elif mutation=='no-approval':s=replace(s,body=tuple(node for node in s.body if not isinstance(node,ApprovalStatement)))
 elif mutation=='approval-ref':
  s=replace(s,body=tuple(replace(node,value=replace(node.value,approval_ref='wrong')) if isinstance(node,LetStatement) and isinstance(node.value,WriteExpression) else node for node in s.body))
 elif mutation!='-':raise ValueError(mutation)
 collection,loops,emitted,tools,duration=map(int,limits.split(','))
 s=replace(s,limits=replace(s.limits,collection_size=collection,loop_iterations=loops,emitted_rows=emitted,tool_calls=tools,duration_ms=duration))
 if mutation!='hash':s=s.with_computed_hash()
 if extras=='input':input_values['extra']='unused'
 if extras=='context':context_values['extra']='unused'
 p=replace(e.principal,scopes=frozenset() if scopes=='-' else frozenset(scopes.split('+')),verification=PrincipalVerification.VERIFIED)
 if principal=='unverified':p=replace(p,verification=PrincipalVerification.UNVERIFIED)
 if principal=='tenant':p=replace(p,tenant_id='')
 if principal=='actor':p=replace(p,actor_type='OTHER')
 catalog_values=(c,inner,write)
 if catalog=='missing':catalog_values=(inner,write)
 if catalog=='version':catalog_values=(replace(c,version='2.0.0'),inner,write)
 auth=g.Authorizer();clock_port=e.Clock(clock);requests=[];measurements=[];debug_events=[]
 class Sink:
  def record(self,measurement):
   measurements.append(asdict(measurement))
   if provider=='sink-error':raise RuntimeError('synthetic measurement failure')
 class Provider:
  async def execute(self,request):
   requests.append([request.execution_id,request.invocation_id,request.node_id,request.tool_id,request.tool_version,request.contract_hash,
    {k:g.env(v) for k,v in request.arguments.items()},request.principal.tenant_id,request.principal.subject_id,sorted(request.principal.scopes),request.authorization_decision_ref])
   if provider in ('error','sink-error'):raise RuntimeError('synthetic provider failure CONFIDENTIAL-PROJECT')
   if provider=='tool-error':raise ToolExecutionError('CUSTOM_FAILURE','missing CONFIDENTIAL-PROJECT')
   if provider=='timeout':raise TimeoutError()
   result=ToolResultEnvelope(request.invocation_id,request.tool_id,request.tool_version,e.values[tool_value],c.output_type,
    g.Presence.EMPTY if e.values[tool_value] in (None,[]) else g.Presence.PRESENT,Completeness(comp),DataClassification.INTERNAL,
    g.tool_result_hash(e.values[tool_value]),None)
   return replace(result,**{'hash':dict(result_hash='sha256:'+'0'*64),'type':dict(type_info=e.types['String']),
    'identity':dict(invocation_id='wrong')}.get(provider,{}))
 def debug_sink(text):
  debug_events.append(text)
  if debug=='fail':raise RuntimeError('debug sink failure')
 request=ExecutionRequest('exec-a',input_values,context_values,p,business_operation_id=BusinessOperationId('business-a') if business=='yes' else None)
 engine=RuntimeEngine(ToolContractCatalog(catalog_values),authorizer=auth,runtime_clock=clock_port,environment=RuntimeEnvironment.PRODUCTION if environment=='production' else RuntimeEnvironment.DEVELOPMENT,
  tool_measurement_sink=Sink(),debug_mode=debug!='off',debug_trace_sink=debug_sink if debug!='off' else None)
 try:
  result=(await engine.execute_controlled_prefix(s,request,Provider(),InMemoryAuditSink())).result if mode=='prefix' else await engine.execute(s,request,Provider(),InMemoryAuditSink())
  outcome=['ok',result.to_data(),result.semantic_view()]
 except Exception as error:outcome=['exception',type(error).__name__,str(error)]
 print(case,version,mode,wire.hex(),bindings,tool_output,tool_value,comp,provider,scopes,clock,limits,mutation,principal,environment,business,extras,catalog,debug,s.semantic_hash,
  canonical_json([outcome,auth.calls,requests,measurements,clock_port.calls,len(debug_events)]).hex(),sep='\t')

async def main():
 print('# Python Oracle 28a949ec96581cbdc52c8133527b23bb5b72ee0a')
 mode=sys.argv[2]
 if mode=='summary':
  for version,entry,count in product(('1-0','1-1'),('read','prefix'),(0,1,2,3)):
   if version=='1-0' and entry=='prefix':continue
   await row('repeated-check',version,entry,statements=(LetStatement('read-let','unused',g.read()),g.loop(body=(check(),g.emit()))),bindings=g.bind('list0' if count==0 else 'int1' if count==1 else 'int2' if count==2 else 'ints'))
  return
 if mode=='admission':
  for version,ns,ty,value in product(('1-0','1-1'),('i','c'),('ListInt','Record','Nested'),('one','a','null','bools','record_other','record_null')):
   await row('recursive-admission',version,statements=(),outputs=(),bindings=g.bind(value,ty,namespace=ns))
  for version in ('1-0','1-1'):
   await row('statement-type',version,statements=(g.emit(),),mutation='emit-declaration')
   await row('division-exception',version,statements=(LetStatement('let','target',e.parse('L,one,Int,left;L,two,Int,right;B,DIV,Int,division')),),outputs=())
  return
 if mode=='security':
  for version,setup in product(('1-0','1-1'),('scope','write','custom','transaction')):
   await row('metadata-security',version,catalog=setup)
  return
 for version,complete,value,output in product(('1-0','1-1'),Completeness,('seven','ints','list0'),('Int','ListInt')):
  if (output=='Int')!=(value=='seven'):continue
  await row('read-results',version,tool_output=output,tool_value=value,comp=complete.value)
 for version,truth,complete in product(('1-0','1-1'),('true','false'),Completeness):
  await row('check-status',version,statements=(check(condition=g.read('Bool')),),outputs=(),tool_output='Bool',tool_value=truth,comp=complete.value)
 for truth,complete in product(('true','false'),Completeness):
  await row('mixed-check-status','1-1',statements=(check('a',g.read('Bool')),check('b',g.literal('false','Bool'))),outputs=(),tool_output='Bool',tool_value=truth,comp=complete.value)
 for version,ns,ty,value in product(('1-0','1-1'),('i','c'),('Int','ListInt'),('seven','ints')):
  if (ty=='Int')!=(value=='seven'):continue
  await row('request-binding',version,statements=(g.emit(g.symbol('source',ty)),),outputs=(g.output(ty),),bindings=g.bind(value,ty,namespace=ns))
 for version,complete in product(('1-0','1-1'),Completeness):
  await row('read-loop',version,statements=(LetStatement('let','source',g.read('ListInt')),g.loop(body=(g.emit(g.symbol('iterator','Int')),))),tool_output='ListInt',tool_value='ints',comp=complete.value,outputs=(g.output(),))
  await row('loop-check',version,statements=(g.loop(body=(check(),g.emit())),),bindings=g.bind('int1'))
 for version,provider in product(('1-0','1-1'),('error','tool-error','timeout','hash','type','identity','sink-error')):
  await row('tool-failures',version,provider=provider,bindings=g.bind('secret','String',classification='CONFIDENTIAL'))
 for debug in ('off','on','fail'):
  await row('protected-debug',provider='error',debug=debug,bindings=g.bind('secret','String',classification='RESTRICTED'))
 for version,mutation in product(('1-0','1-1'),('hash','profile','language','unbounded','required-hash')):
  await row('preflight',version,mutation=mutation)
 for catalog in ('missing','version'):
  await row('catalog',catalog=catalog)
 for principal,environment,scopes in product(('valid','unverified','tenant','actor'),('production','development'),('-', 'read','nsl:skill:execute','nsl:skill:execute+read+write')):
  await row('authority',principal=principal,environment=environment,scopes=scopes)
 for namespace,mutation in (('i','input-missing'),('i','optional-missing'),('c','context-missing')):
  await row('missing-binding',bindings=g.bind('seven','Int',namespace=namespace),mutation=mutation)
 for extras in ('input','context'):
  await row('extra-request',extras=extras)
 for mutation in ('no-approval','approval-ref','business-missing'):
  await row('prefix-admission','1-1',mutation=mutation)
 await row('ordinary-controlled','1-1',mode='controlled',mutation='-')
 for kind,maximum in product(('collection','loop','emit','tool','duration'),(0,1,2,3,4)):
  limits=[100,100,100,100,100];limits[('collection','loop','emit','tool','duration').index(kind)]=maximum
  await row('limits-'+kind,statements=(LetStatement('read-let','source',g.read('ListInt')),g.loop()),tool_output='ListInt',tool_value='ints',outputs=(g.output(),),limits=','.join(map(str,limits)))
 for position in range(1,18):
  await row('clock-deadline',clock=','.join(['0']*position+['100000000']))
 for version,namespace in product(('1-0','1-1'),('i','c')):
  for ty,value in (('Record','record'),('Nested','record_list'),('ListRecord','records'),('ListListInt','nested'),('Decimal','dp'),('USD','usd'),('String','unicode'),('Bool','true'),('Date','date0'),('DateTime','utc'),('Domain','a'),('Enum','b')):
   await row('recursive-valid',version,statements=(g.emit(g.symbol('source',ty)),),outputs=(g.output(ty),),bindings=g.bind(value,ty,namespace=namespace))
  await row('binding-collection-limit',version,statements=(),outputs=(),bindings=g.bind('ints',namespace=namespace),limits='2,100,100,100,100')
 for ty,value in (('Int','wide'),('Int','seven'),('String','empty'),('Bool','true'),('Nested','record_list'),('ListString','strings'),('USD','usd')):
  await row('protected-sensitive',provider='error',debug='on',bindings=g.bind(value,ty,classification='RESTRICTED'))
 for version,program,output,value,ty in (('1-0','S,source,Record,source;F,a,Int,field','Int','record','Record'),('1-1','S,source,ListRecord,source;P,a,ListInt,projection','ListInt','records','ListRecord'),('1-1','S,source,ListInt,source;C,sum,1,Int,sum','Int','ints','ListInt')):
  await row('expression-shapes',version,statements=(g.emit(e.parse(program)),),outputs=(g.output(output),),bindings=g.bind(value,ty))
 for version in ('1-0','1-1'):
  await row('check-binding-error',version,statements=(check(),),outputs=(),bindings=g.bind('seven','Int',target='result-rule'))
  await row('per-node-loop-limit',version,statements=(g.loop(maximum=2),),bindings=g.bind('ints'))
  await row('money-error',version,statements=(g.emit(e.parse('L,usd,USD,left;L,eur,EUR,right;B,ADD,USD,money')),),outputs=(g.output('USD'),))
 for version in ('1-0','1-1'):
  await row('empty-body',version,statements=(),outputs=())
  await row('expression-error',version,statements=(g.emit(e.parse('L,one,Int,left;L,zero,Int,right;B,DIV,Decimal,division')),),outputs=(g.output('Decimal'),))
  await row('binding-error',version,statements=(LetStatement('let','source',g.literal()),),outputs=(),bindings=g.bind('seven','Int'))
  await row('check-type',version,statements=(check(condition=g.literal()),),outputs=())
  await row('missing-reference',version,statements=(g.emit(g.symbol('missing','Int')),))

if __name__=='__main__':asyncio.run(main())
