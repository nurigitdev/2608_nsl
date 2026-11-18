"""Probe a frozen archive or current Python tree with in-memory fixtures only.

Run from the repository root; argv[1] selects the Python source tree.
No external business-system provider is constructed.
"""
import asyncio,json,sys
from hashlib import sha256
from dataclasses import replace
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from nsl.core import INT,DataClassification,RiskLevel,ToolCapability
from nsl.ir import NsoCodec,RequiredTool,ReadExpr,ResultPolicy,LetStatement,SymbolSpec
from nsl.tools import (ToolContract,ToolContractCatalog,MockToolExecutor,WriteSemantics,
    IdempotencySemantics,CommitSemantics,CommitConfirmation,Atomicity,OperationKind)
from nsl.runtime import RuntimeEngine
from nsl.runtime_models import ExecutionContext,ExecutionRequest
from nsl.security import ExecutionPrincipal,PrincipalVerification
from nsl.audit import InMemoryAuditSink

semantics=WriteSemantics(True,Atomicity.ATOMIC,
    IdempotencySemantics(True,'tenant','P1D','RETURN_PREVIOUS','REJECT'),
    CommitSemantics(CommitConfirmation.EXPLICIT),True,True)
contract=ToolContract('TEST.CREATE','1.0.0','WRITE',(),INT,'test:write',
    DataClassification.INTERNAL,operation_kind=OperationKind.CREATE,
    risk_level=RiskLevel.R2,write_semantics=semantics)
catalog=ToolContractCatalog((contract,))
principal=ExecutionPrincipal('tenant-a','subject-a','USER',frozenset(),
    frozenset({'nsl:skill:execute','test:read'}),'auth:ref',PrincipalVerification.VERIFIED)
assert 'test:write' not in principal.scopes
request=ExecutionRequest('exec-probe',{}, {},principal)

async def main():
    for version in ('1-0','1-1'):
        base=NsoCodec.decode(Path(f'compatibility/v0_2_1/nso/ir-{version}-canonical/expected.nso').read_bytes())
        required=RequiredTool('tool1',contract.tool_id,contract.version,'READ',contract.contract_hash,
            'test:read',DataClassification.INTERNAL,RiskLevel.R0 if version=='1-1' else None)
        expr=ReadExpr('read-probe','tool1',(),INT,ResultPolicy())
        skill=replace(base,required_tools=(required,),inputs=(),contexts=(),outputs=(),
            symbols=(SymbolSpec('s-result','result','VARIABLE',INT,DataClassification.INTERNAL),),
            body=(LetStatement('stmt-probe','s-result',expr),)).with_computed_hash()
        # Cross actual schema/integrity admission before invoking the runtime.
        decoded=NsoCodec.decode(NsoCodec.encode(skill))
        provider=MockToolExecutor(catalog,{contract.tool_id:lambda arguments:7})
        engine=RuntimeEngine(catalog)
        result=await engine.execute(decoded,request,provider,InMemoryAuditSink())
        print(json.dumps({'reference_oracle':'28a949ec96581cbdc52c8133527b23bb5b72ee0a',
            'source_runtime_sha256':sha256((Path(sys.argv[1])/'nsl/runtime.py').read_bytes()).hexdigest(),
            'ir_version':decoded.ir_version,'nso_admitted':True,
            'declared_capability':required.capability,'actual_capability':contract.capability,
            'declared_scope':required.required_scope,'actual_scope':contract.required_scope,
            'principal_has_actual_write_scope':contract.required_scope in principal.scopes,
            'approvals':0,'runtime_status':result.status.value,
            'error':None if result.error is None else {'code':result.error.code,'message':result.error.message},
            'provider_calls':provider.call_count,'resources_tool_calls':result.resources.tool_calls},sort_keys=True))
asyncio.run(main())
