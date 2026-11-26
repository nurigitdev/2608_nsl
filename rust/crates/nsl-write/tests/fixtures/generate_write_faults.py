"""R9-S02 original fixed-archive fault observations. Python 3.12 -I -B ARCHIVE.
Only synthetic provider mutations and in-memory evidence; no pytest invocation.
"""
import asyncio
import dataclasses
import runpy
import sys
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
from nsl.write_execution import *
from nsl.validation import WriteGuardDecision
from nsl.core import PreWriteEligibility, ValueEnvelope
from nsl.ir import canonical_json, encode_value

PIN = '28a949ec96581cbdc52c8133527b23bb5b72ee0a'
h = runpy.run_path(str(Path(sys.argv[1])/'tests/test_v0_2_write_execution.py'))

def thaw(v):
    if isinstance(v, Mapping): return {k: thaw(x) for k,x in v.items()}
    if isinstance(v, (list,tuple)): return [thaw(x) for x in v]
    return v

def plain(v):
    if isinstance(v,ValueEnvelope):
        return dict(value=encode_value(thaw(v.value)),type=v.type_info.to_data(),presence=v.presence,
                    completeness=v.completeness,classification=v.classification,provenance_refs=list(v.provenance_refs))
    if dataclasses.is_dataclass(v): return {f.name:plain(getattr(v,f.name)) for f in dataclasses.fields(v)}
    if isinstance(v,datetime): return v.isoformat()
    if isinstance(v,Mapping): return {k:plain(x) for k,x in v.items()}
    if isinstance(v,(list,tuple)): return [plain(x) for x in v]
    return v

def outcome(v):
    return dict(kind='committed' if isinstance(v,WriteCommitted) else 'not-committed' if isinstance(v,WriteNotCommitted) else 'unknown',
                commit_state=v.commit_state.value,data=plain(v.result if isinstance(v,WriteCommitted) else v))

async def scenario(c):
    context=h['_context']();request=h['_request'](context=context)
    h['_seed_idempotency'](context,request)
    events=[];started=asyncio.Event();counts=dict(provider_calls=0,possible_mutations=0,cleanup_completed=0)
    def returned(mode):
        if mode=='committed': return h['_committed'](request)
        if mode=='not-committed': return h['_not_committed'](request)
        if mode=='unknown': return h['_unknown'](request)
        if mode=='malformed': return object()
        raise {'propagate':asyncio.CancelledError('protected cancellation'),
               'cleanup-error':RuntimeError('protected cleanup error'),
               'timeout-error':TimeoutError('protected timeout'),
               'connection-error':ConnectionError('protected connection'),
               'cancelled-error':asyncio.CancelledError('protected cancellation'),
               'exception-error':RuntimeError('protected provider error')}[mode]
    class Provider:
        async def operation(self,r):
            events.append('PROVIDER_WRITE');counts['provider_calls']+=1
            if c['mutate']:
                counts['possible_mutations']+=1;events.append('POSSIBLE_MUTATION')
            started.set()
            if c['trigger']=='direct' or c.get('immediate',False):
                await asyncio.sleep(0)
                return returned(c['mode'])
            try: await asyncio.Event().wait()
            except asyncio.CancelledError:
                events.extend(('CANCEL_RECEIVED','CLEANUP_STARTED'))
                for _ in range(c['cleanup_turns']): await asyncio.sleep(0)
                counts['cleanup_completed']+=1;events.append('CLEANUP_COMPLETED')
                return returned(c['mode'])
        async def execute(self,r):
            if c['trigger']=='timeout':
                return await asyncio.wait_for(self.operation(r),timeout=c['timeout_ms']/1000)
            return await self.operation(r)
    outcome_port=h['_OutcomePort'](events)
    executor=CommitAwareWriteExecutor(WriteExecutionCoordinator(Provider(),h['_EvidencePort'](events)),WriteOutcomeCoordinator(outcome_port))
    task=asyncio.create_task(executor.execute(request=request,decision=WriteGuardDecision(PreWriteEligibility.ALLOWED),context=context,requested_at=h['NOW'],recorded_at=h['NOW']+timedelta(seconds=2)))
    if c['trigger']=='cancel':
        await started.wait();task.cancel()
    decision=await task
    return dict(**counts,automatic_write_retries=max(0,counts['provider_calls']-1),
                flag=context.checkpoint.write_request_durable,
                outcome=outcome(decision.outcome),automatic_retry_allowed=decision.automatic_retry_allowed,
                reconciliation_required=decision.reconciliation_required,
                recovery_status=decision.execution_status.value if decision.reconciliation_required else None,
                events=[e for e in events if e!='WRITE_OUTCOME'],
                oracle_final_status=decision.execution_status.value,
                oracle_outcome_records=len(outcome_port.records))

async def main():
    print('# Python Oracle '+PIN)
    cases=[]
    for mutate in (False,True):
        for mode in ('timeout-error','connection-error','cancelled-error','exception-error','malformed','committed','not-committed','unknown'):
            cases.append(dict(trigger='direct',mode=mode,mutate=mutate,cleanup_turns=0))
        for trigger in ('cancel','timeout'):
            for mode in ('propagate','committed','not-committed','unknown','cleanup-error'):
                for turns in (0,1,3):
                    cases.append(dict(trigger=trigger,mode=mode,mutate=mutate,cleanup_turns=turns,timeout_ms=1))
    for ms in (-1,0,1,2**63-2,2**63-1):
        cases.append(dict(trigger='timeout',mode='committed',mutate=False,cleanup_turns=0,timeout_ms=ms,immediate=True))
    for i,c in enumerate(cases):
        result=await scenario(c)
        name=f"{i}-{c['trigger']}-{c['mode']}-{int(c['mutate'])}-{c['cleanup_turns']}"
        print(name,canonical_json(c).hex(),canonical_json(result).hex(),sep='\t')
asyncio.run(main())
