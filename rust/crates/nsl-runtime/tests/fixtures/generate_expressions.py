"""R6-S03: unmodified frozen Oracle observations; no float compatibility patches."""
import asyncio
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from itertools import product
from pathlib import Path
import sys
sys.path.insert(0,str(Path(sys.argv[1]).resolve()))
from nsl.core import (BOOL,INT,STRING,DECIMAL,DATE,DATETIME,YEAR,Money,ValueEnvelope,
    TypeRef,Presence,Completeness,DataClassification,encode_value,list_type,money_type,record_type,enum_type,domain)
from nsl.ir import NsoCodec,LiteralExpr,SymbolRefExpr,FieldExpr,ProjectionExpr,CallExpr,BinaryExpr
from nsl.runtime import RuntimeEngine
from nsl.runtime_models import ExecutionContext,ExecutionRequest
from nsl.security import ExecutionPrincipal,PrincipalVerification
from nsl.tools import ToolContractCatalog
from nsl.ir import canonical_json

types=dict(Int=INT,Bool=BOOL,String=STRING,Decimal=DECIMAL,Year=YEAR,Date=DATE,DateTime=DATETIME,
    USD=money_type('USD'),EUR=money_type('EUR'),Domain=domain('Customer'),Enum=enum_type('E','A','B'),
    Record=record_type('R',a=INT),Nested=record_type('N',a=list_type(INT)),EmptyRecord=record_type('R'))
for name in ('Int','Bool','String','Decimal','USD','Record','Nested','EmptyRecord','Date','DateTime','Domain','Enum'):
    types['List'+name]=list_type(types[name])
types['ListListInt']=list_type(types['ListInt'])
values={'neg':-1,'zero':0,'one':1,'two':2,'seven':7,'wide':10**100,
    'false':False,'true':True,'empty':'','a':'A','b':'B','unicode':'가😀','null':None,
    'dm':Decimal('-1.2300'),'dz':Decimal('-0.00'),'dp':Decimal('1.2300'),
    'tie':Decimal('9999999999999999999999999999'),'half':Decimal('0.5'),
    'high':Decimal('1e999999'),'tiny':Decimal('1e-1000026'),
    'usd':Money(Decimal('12.500'),'USD'),'usdm':Money(Decimal('-1'),'USD'),
    'usdz':Money(Decimal('0'),'USD'),'eur':Money(Decimal('12.5'),'EUR'),
    'date0':date(2026,11,18),'date1':date(2026,11,19),
    'time0':datetime.fromisoformat('2026-11-18T01:02:03'),
    'time1':datetime.fromisoformat('2026-11-18T01:02:04.000001'),
    'utc':datetime.fromisoformat('2026-11-18T01:02:03+00:00'),
    'offset':datetime.fromisoformat('2026-11-18T10:02:03+09:00'),
    'offset_second':datetime.fromisoformat('2026-11-18T01:03:03.123456+00:01:00.123456'),
    'ancient':datetime.fromisoformat('0001-01-01T00:00:00-23:59:59.999999'),
    'leap':datetime.fromisoformat('2000-02-29T00:00:00+00:00'),
    'end':datetime.fromisoformat('9999-12-31T23:59:59.999999+23:59:59.999999'),
    'list0':[],'ints':[-1,0,2],'int1':[7],'bools':[True,False],
    'decimals':[Decimal('0.1'),Decimal('-0.2'),Decimal('0.3')],
    'money':[Money(Decimal('12.500'),'USD'),Money(Decimal('-1'),'USD')],
    'money_mixed':[Money(Decimal('12.500'),'USD'),Money(Decimal('1'),'EUR')],
    'money_wrong':[Money(Decimal('1'),'USD'),7],
    'rounding':[Decimal('9999999999999999999999999999'),Decimal('0.5'),Decimal('-9999999999999999999999999999')],
    'strings':['가','😀','A'],'domains':['B','A'],'enums':['B','A'],
    'dates':[date(2026,11,19),date(2026,11,18)],
    'times':[datetime.fromisoformat('2026-11-18T01:02:04+00:00'),datetime.fromisoformat('2026-11-18T10:02:03+09:00')],
    'times_mixed':[datetime.fromisoformat('2026-11-18T01:02:04'),datetime.fromisoformat('2026-11-18T10:02:03+09:00')],
    'records':[{'a':-1},{'a':0},{'a':2}],'record1':[{'a':7}],
    'records_bad':[{'a':7},{}],'records_wrong':[7],
    'record':{'a':7},'record_null':{'a':None},'record_list':{'a':[]},'record_empty':{},
    'nested':[[1,2],[1,3]],'nested_equal':[[1,2],[1,2]],'nested_short':[[1,2],[1]],
    'nested_bool':[[True],[1]],'records_same':[{},{}]}
values.update(record_other={'b':7},list_one=[1],list_true=[True],money_high=Money(Decimal('9e999999'),'USD'),money_high_list=[Money(Decimal('9e999999'),'USD'),Money(Decimal('9e999999'),'USD')])

def l(v,t='Int',node='e0'):return f'L,{v},{t},{node}'
def s(symbol='s0',t='Int',node='e0'):return f'S,{symbol},{t},{node}'
def binary(a,b,op='ADD',t='Int',node='e0'):return f'{a};{b};B,{op},{t},{node}'
def call(source,name='sum',t='Int',node='e0'):return f'{source};C,{name},1,{t},{node}'
def bind(v,t='Int',comp='COMPLETE',cls='INTERNAL',prov='-',target='s0',namespace='v',presence='PRESENT'):
    return ','.join((namespace,target,v,t,presence,comp,cls,prov))
def parse(program):
    stack=[]
    for token in program.split(';'):
        p=token.split(',');kind=p[0];t=types[p[-2]];node=p[-1]
        if kind=='L':expr=LiteralExpr(node,values[p[1]],t)
        elif kind=='S':expr=SymbolRefExpr(node,p[1],t)
        elif kind in ('F','P'):expr=(FieldExpr if kind=='F' else ProjectionExpr)(node,stack.pop(),p[1],t)
        elif kind=='C':
            n=int(p[2]);args=tuple(stack[-n:]) if n else ();stack=stack[:-n] if n else stack
            expr=CallExpr(node,p[1],args,t)
        elif kind=='B':
            right=stack.pop();left=stack.pop();expr=BinaryExpr(node,p[1],left,right,t)
        stack.append(expr)
    assert len(stack)==1
    return stack[0]
class Clock:
    def __init__(self,script):self.script=list(map(int,script.split(',')));self.calls=0
    def monotonic_ns(self):
        result=self.script[min(self.calls,len(self.script)-1)];self.calls+=1;return result
base=NsoCodec.decode((Path(__file__).resolve().parents[5]/'compatibility/v0_2_1/nso/ir-1-0-canonical/expected.nso').read_bytes())
engine=RuntimeEngine(ToolContractCatalog(()))
principal=ExecutionPrincipal('tenant-a','subject-a','USER',frozenset(),frozenset(),'auth:ref',PrincipalVerification.UNVERIFIED)
def snapshot(v):
    if isinstance(v.value,float):return ['oracle_float',repr(v.value),v.type_info.to_data()]
    return ['ok',encode_value(v.value),v.type_info.to_data(),v.presence,v.completeness,v.classification,list(v.provenance_refs)]
async def observe(mode,program,bindings,clock_script,collection):
    clock=Clock(clock_script)
    skill=replace(base,limits=replace(base.limits,collection_size=collection,duration_ms=1))
    ctx=ExecutionContext(skill,ExecutionRequest('exec-a',{}, {},principal),clock)
    if bindings!='-':
        for item in bindings.split(';'):
            namespace,target,v,t,presence,comp,cls,prov=item.split(',')
            envelope=ValueEnvelope(values[v],types[t],Presence(presence),Completeness(comp),DataClassification(cls),() if prov=='-' else tuple(prov.split('+')))
            getattr(ctx,{'i':'bind_input','c':'bind_context','v':'bind','k':'bind_check'}[namespace])(target,envelope)
    expression=parse(program)
    try:
        if mode=='eval' or mode=='division':out=snapshot(await engine._evaluate(ctx,expression,None))
        else:
            from nsl.ir import LetStatement
            statement=LetStatement('stmt','target',expression)
            if mode=='let_nested':ctx.push_frame()
            await engine._execute_let(ctx,statement,None, None)
            if mode=='let_twice':await engine._execute_let(ctx,statement,None,None)
            if mode=='let_siblings':
                ctx.pop_frame();ctx.push_frame();await engine._execute_let(ctx,statement,None,None)
            out=['ok',None]
    except Exception as error:out=[type(error).__name__,str(error),getattr(error,'node_id',None),getattr(error,'detail_code',None)]
    try:target=snapshot(ctx.resolve('target'))
    except RuntimeError:target=None
    r=ctx.resources
    return [out,ctx.data_completeness.value,[r.tool_calls,r.loop_iterations,r.emitted_rows,r.max_collection_size_seen],clock.calls,target]
async def row(mode,program,bindings='-',clock='0',collection=100):
    value=await observe(mode,program,bindings,clock,collection)
    print(mode,program,bindings,clock,collection,canonical_json(value).hex(),sep='\t')
async def main():
    print('# Python Oracle 28a949ec96581cbdc52c8133527b23bb5b72ee0a')
    if sys.argv[2]=='division':
        for result,denom in product(('Int','Year','Bool','String','USD','ListInt'),('neg','zero','one','two')):
            await row('division',binary(l('one',node='left'),l(denom,node='right'),'DIV',result,'divide'))
        return
    for v,t in (('neg','Int'),('zero','Int'),('one','Int'),('wide','Int'),('false','Bool'),('true','Bool'),('empty','String'),('unicode','String'),('dm','Decimal'),('dz','Decimal'),('dp','Decimal'),('usd','USD')):
        await row('eval',l(v,t))
    for v,t in (('zero','Int'),('unicode','String'),('record','Record'),('list0','ListInt')):
        for comp,cls,presence in product(Completeness,DataClassification,Presence):
            await row('eval',s(t=t),bind(v,t,comp.value,cls.value,'p2+p1+p2',presence=presence.value))
    for left_t,left_v,right_t,right_v in (('Int','neg','Int','two'),('Int','wide','Int','two'),('Decimal','dp','Decimal','dm'),('Decimal','tie','Decimal','half'),('USD','usd','USD','usdm'),('Bool','true','Bool','false'),('String','a','String','b'),('Date','date0','Date','date1'),('Record','record','Record','record_null'),('ListInt','ints','ListInt','int1')):
        operators=('ADD','SUB','MUL','DIV','LT','LE','GT','GE','EQ','NE') if left_t in ('Int','Decimal') else ('ADD','SUB','LT','LE','GT','GE','EQ','NE') if left_t=='USD' else ('EQ','NE')
        for op in operators:
            target='Bool' if op in ('LT','LE','GT','GE','EQ','NE') else 'Decimal' if op=='DIV' else left_t
            await row('eval',binary(s('s0',left_t,'left'),s('s1',right_t,'right'),op,target),bind(left_v,left_t)+';'+bind(right_v,right_t,target='s1'))
    for a,b in product(('time0','time1','utc','offset','offset_second','ancient','leap','end'),repeat=2):
        for op in ('EQ','NE'):
            await row('eval',binary(s('s0','DateTime','left'),s('s1','DateTime','right'),op,'Bool'),bind(a,'DateTime')+';'+bind(b,'DateTime',target='s1'))
    for compa,compb,clsa,clsb in product(Completeness,Completeness,DataClassification,DataClassification):
        await row('eval',binary(s('s0',node='left'),s('s1',node='right')),bind('one',comp=compa.value,cls=clsa.value,prov='p2+p1+p2')+';'+bind('two',comp=compb.value,cls=clsb.value,prov='p1+p3',target='s1'))
    for v,t,field,out,kind in (('record','Record','a','Int','F'),('record_null','Record','a','Int','F'),('record_list','Nested','a','ListInt','F'),('record_empty','Record','a','Int','F'),('one','Int','a','Int','F'),('records','ListRecord','a','ListInt','P'),('record1','ListRecord','a','ListInt','P'),('list0','ListRecord','a','ListInt','P'),('records_bad','ListRecord','a','ListInt','P'),('records_wrong','ListRecord','a','ListInt','P'),('record','Record','a','ListInt','P')):
        for comp in Completeness:
            await row('eval',s(t=t,node='source')+f';{kind},{field},{out},result',bind(v,t,comp.value,'RESTRICTED','p1+p1'))
    collections=(('ListInt','list0'),('ListInt','int1'),('ListInt','ints'),('ListBool','bools'),('ListDecimal','decimals'),('ListDecimal','rounding'),('ListUSD','money'),('ListUSD','money_mixed'),('ListUSD','money_wrong'),('ListString','strings'),('ListDomain','domains'),('ListEnum','enums'),('ListDate','dates'),('ListDateTime','times'),('ListDateTime','times_mixed'),('ListRecord','records'),('ListRecord','record1'),('ListEmptyRecord','records_same'),('ListListInt','nested'),('ListListInt','nested_equal'),('ListListInt','nested_short'),('ListListInt','nested_bool'))
    for t,v in collections:
        for name in ('count','min','max','sum'):
            expected='Int' if name=='count' else t.removeprefix('List')
            await row('eval',call(s(t=t,node='source'),name,expected),bind(v,t,'PARTIAL','CONFIDENTIAL','p1+p2'))
    for v in ('zero','one','null','empty','unicode','record','record_empty'):
        for name in ('count','min','max','sum'):
            await row('eval',call(s(t='ListInt',node='source'),name),bind(v,'ListInt'))
    for program,bindings in ((s('missing'),'-'),(s(t='String'),bind('one')),(call(s(t='Int'), 'sum'),bind('one')),(call(s(t='ListInt'),'custom'),bind('ints','ListInt')),(call(s(t='ListInt'),'sum','String'),bind('ints','ListInt')),('C,sum,0,Int,e0','-'),(l('one',node='a')+';'+l('two',node='b')+';C,sum,2,Int,e0','-'),(binary(l('one',node='left'),l('zero',node='right'),'DIV','Decimal'),'-'),(binary(l('one',node='left'),l('two',node='right'),'UNKNOWN'),'-'),(binary(s('missing',node='left'),s('other',node='right')),'-'),(binary(s('s0',node='left'),s('missing',node='right')),bind('one',comp='PARTIAL'))):
        await row('eval',program,bindings)
    for op in ('ADD','SUB','LT','LE','GT','GE','EQ','NE'):
        await row('eval',binary(s('s0','USD','left'),s('s1','USD','right'),op,'Bool' if op in ('LT','LE','GT','GE','EQ','NE') else 'USD'),bind('usd','USD')+';'+bind('eur','USD',target='s1'))
    for a,b,t in (('ints','ints','ListInt'),('list_one','list_true','ListInt'),('list0','list0','ListInt'),('record','record','Record'),('record','record_other','Record'),('record','record_empty','Record')):
        for op in ('EQ','NE'):
            await row('eval',binary(s('s0',t,'left'),s('s1',t,'right'),op,'Bool'),bind(a,t)+';'+bind(b,t,target='s1'))
    for a,b,t,op,out in (('a','unicode','String','ADD','String'),('ints','int1','ListInt','ADD','ListInt'),('true','false','Bool','ADD','Int'),('true','false','Bool','SUB','Int'),('true','false','Bool','MUL','Int'),('true','true','Bool','DIV','Decimal'),('one','unicode','Int','ADD','Int'),('unicode','one','Int','SUB','Int'),('usd','usd','USD','MUL','USD'),('dp','dp','Decimal','UNKNOWN','Decimal'),('utc','time0','DateTime','LT','Bool'),('money_high','money_high','USD','ADD','USD')):
        await row('eval',binary(s('s0',t,'left'),s('s1',t,'right'),op,out),bind(a,t)+';'+bind(b,t,target='s1'))
    for value_id in ('null','false','one','unicode','dp','date0','utc','ints','record'):
        for op in ('ADD','SUB','LT','GT'):
            await row('eval',binary(s('s0','USD','left'),s('s1','USD','right'),op,'Bool' if op in ('LT','GT') else 'USD'),bind('usd','USD')+';'+bind(value_id,'USD',target='s1'))
    for v in ('false','true','dz','dp','usd'):
        for name in ('min','max'):
            await row('eval',call(s(t='ListInt',node='source'),name),bind(v,'ListInt'))
    await row('eval',call(s(t='ListUSD',node='source'),'sum','USD'),bind('money_high_list','ListUSD'))
    for v,t,op in (('high','Decimal','MUL'),('tiny','Decimal','DIV')):
        await row('eval',binary(s(t=t,node='left'),s(t=t,node='right'),op,t),bind(v,t))
    for mode in ('let','let_twice','let_nested'):
        for v in ('neg','zero','one'):
            await row(mode,l(v))
        for namespace in ('i','c','v','k'):
            await row(mode,s(t='Int'),bind('one')+';'+bind('seven',target='target',namespace=namespace))
    for limit in (0,1,2,3,4):
        await row('eval',s(t='ListInt'),bind('ints','ListInt','PARTIAL'),collection=limit)
        await row('eval',call(s(t='ListInt',node='source'),'count'),bind('ints','ListInt','UNKNOWN'),collection=limit)
    for clock in ('0','0,999999','0,1000000','0,0,1000000','0,0,0,1000000','0,0,0,0,1000000','0,0,0,0,0,1000000'):
        await row('eval',binary(l('one',node='left'),l('two',node='right')),clock=clock)
    await row('let',binary(l('one',node='left'),l('zero',node='right'),'DIV','Decimal'))
    # Combined field/projection/call expression, with no mutation of its source.
    await row('let',binary(call(s(t='ListRecord',node='source')+';P,a,ListInt,projection',node='sum'),l('one',node='right')),bind('records','ListRecord'))
asyncio.run(main())
