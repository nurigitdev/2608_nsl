"""Frozen compiler outputs for the R5-S08 API and canonical lowering."""
from pathlib import Path
import sys
from dataclasses import fields
sys.path.insert(0,str(Path(sys.argv[1]).resolve()))
from nsl.compiler import NslCompiler
from nsl.core import INT,DECIMAL,BOOL,DataClassification,list_type,money_type,record_type
from nsl.tools import ToolContract,ToolContractCatalog,WriteSemantics,IdempotencySemantics,CommitSemantics
from nsl.source import SourceFile
from nsl.includes import MemoryIncludeResolver
from nsl.diagnostics import CompileError

ROW=record_type("Row",number=INT,cost=money_type("KRW"),nested=record_type("Meta",flag=BOOL),values=list_type(INT))
CATALOG=ToolContractCatalog((
    ToolContract("TEST.RECORD","1.0.0","READ",(("z",INT),("a",DECIMAL)),ROW,"test:scope",DataClassification.CONFIDENTIAL),
    ToolContract("TEST.LIST","1.0.0","READ",(),list_type(ROW),"test:scope",DataClassification.INTERNAL),
    ToolContract("TEST.SCALAR","1.0.0","READ",(("amount",INT),),DECIMAL,"test:scope",DataClassification.PUBLIC),
    ToolContract("TEST.CREATE","1.0.0","WRITE",(("amount",INT),),ROW,"test:scope",DataClassification.INTERNAL,
                 operation_kind="CREATE",risk_level="R1",write_semantics=WriteSemantics(True,"ATOMIC",IdempotencySemantics(True,"TOOL_AND_TENANT","PROVIDER_DEFINED","RETURN_ORIGINAL_RESULT","IDEMPOTENCY_CONFLICT"),CommitSemantics("EXPLICIT"),True,True)),
))
def enc(value):return "~" if value is None else value.encode().hex() or "-"
DECL='input { i: Int classification PUBLIC; d: Decimal classification INTERNAL; items: List<Int> classification INTERNAL; bools: List<Bool>; krw: Money<KRW> classification CONFIDENTIAL; } context { ctx: Int from "prior" classification RESTRICTED; }'
REQ='tool TEST.RECORD version "1.0.0"; tool TEST.LIST version "1.0.0"; tool TEST.SCALAR version "1.0.0";'
LIMITS='limits { tool_calls 100; loop_iterations 100; emitted_rows 100; collection_size 100; }'
def emit(label,version,body,*,decl=DECL,req=REQ,output='',controlled=False,fragment=None,limits=LIMITS,resolve=True,risk=None):
    root=SourceFile.from_text('compiler/root.ns',f'// 컴파일 😀\r\nlanguage NSL "{version}";\r\nskill TEST {{ version "1"; risk {risk or ("WRITE" if controlled else "READ_ONLY")};\nrequires {{ {req} }}\n{limits}\n'+('include "part.ns";\n' if fragment is not None else '')+decl+'\n'+output+'\n'+body+'\n}')
    fragments=() if fragment is None else (SourceFile.from_text('compiler/part.ns',fragment),)
    print('case',label,version,enc(root.text),enc(fragment),'resolve' if resolve else 'unresolved',sep='\t')
    try:result=NslCompiler(CATALOG,MemoryIncludeResolver(fragments) if resolve else None).compile(root)
    except CompileError as error:
        print('error',error.code,error.diagnostic.phase.value,f'{error.location.line},{error.location.column}',enc(error.logical_path),enc(error.snippet),enc(error.public_message),sep='\t')
    else:
        print('ok',result.semantic_hash,result.source_bundle_hash,result.nso_bytes.hex(),sep='\t')
        print('safety',*(str(getattr(result.write_safety,f.name)).lower() for f in fields(result.write_safety)),sep='\t')
        for item in result.source_manifest: print('manifest',item.source_id,enc(item.logical_path),item.content_hash,item.size_bytes,int(item.is_root),sep='\t')
        for edge in result.include_edges: print('edge',edge.from_source,edge.to_source,edge.include_span.start.offset,edge.include_span.end.offset,sep='\t')

print('# Python Oracle 28a949ec96581cbdc52c8133527b23bb5b72ee0a')
for version in ('0.1','0.2'):
    emit('empty',version,'',decl='',req='')
    emit('read_validate',version,'',decl='',req='',risk='READ_VALIDATE')
    emit('bad_risk',version,'let a = missing;',risk='INVALID')
    emit('control_literal',version,'let text = "' + '\\' * 2 + '\b\f\r\x01\x1f' + '";')
    emit('literals',version,'let a = 1; let b = 1.2300; let c = true; let text = "한글😀\\n\\t\\\"";')
    for op in ('+','-','*','/','<','<=','>','>=','==','!='):emit('binary_'+op,version,f'let a = i {op} i;')
    emit('precedence',version,'let a = i + i * i; let b = (i + i) * i; let c = d / d; let cash = krw + krw;')
    emit('builtin',version,'let a = sum(items); let b = count(items); let c = min(bools);')
    emit('scopes',version,'let a = i; foreach row in items max 2 { let x = row + a; foreach other in items max 2 { let y = x + other; } } foreach row in items max 2 { let x = row; } let row = i;')
    emit('check',version,'check C { assert i == i; severity WARN; on_fail CONTINUE; message "확인😀"; } let status = C.status;')
    emit('records_projection',version,'let row = read TEST.RECORD(z: i, a: d); let rows = read TEST.LIST(); let cost = row.cost; let flags = rows.nested.flag; let values = rows.values; let total = sum(rows.cost);')
    emit('reads_and_emits',version,'foreach row in items max 2 { let result = read TEST.SCALAR(amount: row); emit { value: result; } }',output='output { value: Decimal classification RESTRICTED; }')
    emit('argument_order',version,'let result = read TEST.RECORD(a: d, z: i);')
    emit('includes',version,'let x = extra; let rows = read TEST.LIST();',fragment='// 조각 😀\r\ncontext { extra: Int from "included" classification PUBLIC; } requires { tool TEST.LIST version "1.0.0"; }')
    emit('included_limits',version,'let result = extra;',limits='',fragment=LIMITS+' context { extra: Int from "prior"; }')
    for label,body in (
        ('type_before_later_name','let a = true + 1; let b = missing;'),
        ('type_before_duplicate','let i = true + 1;'),
        ('shape_before_unknown','let a = read TEST.SCALAR(other: missing);'),
        ('unknown_before_type','let a = missing; let b = true + 1;'),
        ('field_before_right_unknown','let a = i.nope + missing;'),
        ('foreach_before_body_name','foreach row in i max 1 { let x = missing; }'),
        ('duplicate_after_initializer','let a = i; let a = i;'),
        ('check_bool','check C { assert i; severity WARN; on_fail CONTINUE; message "x"; }'),
        ('bool_order','let a = true < false;'),
        ('undeclared','let a = read MISSING();'),
        ('sum_invalid','let a = sum(bools);'),
    ):emit(label,version,body)
    emit('emit_shape_before_name',version,'emit { bad: missing; }',output='output { value: Int; }')
    emit('duplicate_output_before_name',version,'let a = missing;',output='output { a: Int; a: Int; }')
    emit('classification',version,'emit { value: ctx; }',output='output { value: Int classification PUBLIC; }')
    emit('bound',version,'foreach row in items max 101 {}')
    emit('missing_resolver',version,'',fragment='context { extra: Int from "prior"; }',resolve=False)
emit('controlled_write','0.2','approve A { message "생성 😀"; } let result = write TEST.CREATE(amount = i) approved_by A; emit { value: result.number; }',req=REQ+' tool TEST.CREATE version "1.0.0";',controlled=True,output='output { value: Int classification RESTRICTED; }')
emit('approval_priority','0.2','let result = write TEST.CREATE(amount = missing) approved_by UNKNOWN;',req=REQ+' tool TEST.CREATE version "1.0.0";',controlled=True)
emit('write_priority','0.2','approve A { message "go"; } foreach row in items max 1 { let result = write TEST.CREATE(amount = missing) approved_by A; }',req=REQ+' tool TEST.CREATE version "1.0.0";',controlled=True)
