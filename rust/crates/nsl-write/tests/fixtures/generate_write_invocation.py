"""R9-S01 original fixed-archive observations. Python 3.12 -I -B ARCHIVE MODE.
Modes: models|invocation. Synthetic in-memory ports only; no pytest run.
"""
import asyncio
import dataclasses
import runpy
import sys
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
from nsl.write_execution import *
from nsl.core import ValueEnvelope, PreWriteEligibility
from nsl.ir import canonical_json, encode_value
from nsl.validation import WriteGuardDecision

PIN = '28a949ec96581cbdc52c8133527b23bb5b72ee0a'
h = runpy.run_path(str(Path(sys.argv[1]) / 'tests/test_v0_2_write_execution.py'))
request = h['_request']()

def thaw(v):
    if isinstance(v, Mapping): return {k: thaw(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)): return [thaw(x) for x in v]
    return v

def plain(v):
    if isinstance(v, ValueEnvelope):
        return dict(value=encode_value(thaw(v.value)), type=v.type_info.to_data(),
                    presence=v.presence, completeness=v.completeness,
                    classification=v.classification, provenance_refs=list(v.provenance_refs))
    if dataclasses.is_dataclass(v):
        return {f.name: plain(getattr(v, f.name)) for f in dataclasses.fields(v)}
    if isinstance(v, datetime): return v.isoformat()
    if isinstance(v, Mapping): return {k: plain(x) for k, x in v.items()}
    if isinstance(v, (tuple, list)): return [plain(x) for x in v]
    return v

def outcome(v):
    return dict(commit_state=v.commit_state.value,
                kind='committed' if isinstance(v, WriteCommitted) else
                     'not-committed' if isinstance(v, WriteNotCommitted) else 'unknown',
                data=plain(v.result if isinstance(v, WriteCommitted) else v))

def emit(name, data, fn):
    try: result = ['ok', plain(fn())]
    except Exception as e: result = ['error', type(e).__name__, str(e), getattr(e, 'detail_code', None)]
    print(name, canonical_json(plain(data)).hex(), canonical_json(result).hex(), sep='\t')

def models():
    identity = WriteOutcomeIdentity.from_request(request)
    result = h['_committed'](request).result
    for kind, base in [('identity', identity), ('result', result),
                       ('not-committed', h['_not_committed'](request)),
                       ('unknown', h['_unknown'](request))]:
        emit(kind+'-base', dict(kind=kind, value=plain(base)), lambda base=base: base)
        for f in dataclasses.fields(base):
            old = getattr(base, f.name)
            if not isinstance(old, str): continue
            values = ['', ' ', '\x1c', 'Bearer synthetic-only', '가😀']
            if f.name.endswith('hash'):
                values += ['bad', 'sha256:'+'A'*64] + ['sha256:'+'f'*n for n in (0,1,63,64,65)]
            if f.name == 'idempotency_key':
                values += ['bad'] + ['idem:sha256:'+'x'*n for n in (0,1,63,64,65)]
                values += ['idem:sha256:'+'😀'*64]
            for n, value in enumerate(values):
                data = plain(base); data[f.name] = value
                emit(kind+'-'+f.name+'-'+str(n), dict(kind=kind, value=data),
                     lambda base=base, f=f, value=value: dataclasses.replace(base, **{f.name:value}))
        if kind == 'identity': continue
        for value in (None, '', ' ', '\x1c', 'Bearer synthetic-only', 'tx:가😀'):
            data = plain(base); data['provider_transaction_id'] = value
            emit(kind+'-optional-'+repr(value), dict(kind=kind, value=data),
                 lambda base=base, value=value: dataclasses.replace(base, provider_transaction_id=value))
        for n in (0,1,63,64,65):
            refs = tuple('evidence:'+str(i) for i in range(n))
            data = plain(base); data['evidence_refs'] = list(refs)
            emit(kind+'-refs-'+str(n), dict(kind=kind, value=data),
                 lambda base=base, refs=refs: dataclasses.replace(base, evidence_refs=refs))
        for refs in (('',), (' ',), ('Bearer synthetic-only',), ('safe', 'api_key=synthetic')):
            data = plain(base); data['evidence_refs'] = list(refs)
            emit(kind+'-bad-refs-'+repr(refs), dict(kind=kind, value=data),
                 lambda base=base, refs=refs: dataclasses.replace(base, evidence_refs=refs))

async def invocation(case):
    events = []; calls = 0; context = h['_context']()
    class Evidence:
        async def record_requested(self, record):
            events.append('WRITE_REQUESTED')
            return WriteRequestedReceipt(record.record_hash, 'write-evidence:0053', h['NOW'], True)
    class Provider:
        async def execute(self, r):
            nonlocal calls
            calls += 1; events.append('PROVIDER_WRITE')
            if case in ('committed','not-committed','unknown'):
                return h[{'committed':'_committed','not-committed':'_not_committed','unknown':'_unknown'}[case]](r)
            if case == 'foreign-identity':
                return dataclasses.replace(h['_not_committed'](r), identity=dataclasses.replace(WriteOutcomeIdentity.from_request(r), tenant_id='other-tenant'))
            if case == 'malformed': return object()
            raise {'timeout':TimeoutError('protected timeout'),
                   'cancelled':asyncio.CancelledError('protected cancellation'),
                   'connection':ConnectionError('protected transport'),
                   'exception':RuntimeError('protected provider')}[case]
    try:
        raw = await WriteExecutionCoordinator(Provider(), Evidence()).execute(
            request=request, decision=WriteGuardDecision(PreWriteEligibility.ALLOWED),
            context=context, requested_at=h['NOW'])
    except ProviderInvocationUncertainError as e:
        raw = h['CommitAwareWriteExecutor']._uncertain(request, e.reason_code)
    else:
        if not isinstance(raw, (WriteCommitted, WriteNotCommitted, WriteCommitUnknown)):
            raw = h['CommitAwareWriteExecutor']._uncertain(request, 'PROVIDER_ACKNOWLEDGEMENT_MALFORMED')
    return dict(events=events, calls=calls, flag=context.checkpoint.write_request_durable,
                requested_record_hash=WriteAttemptRecord.from_request(request,h['NOW']).record_hash,
                request=plain(request), outcome=outcome(raw))

print('# Python Oracle '+PIN)
if sys.argv[2] == 'models': models()
elif sys.argv[2] == 'invocation':
    for case in ('committed','not-committed','unknown','foreign-identity','timeout','cancelled','connection','exception','malformed'):
        emit(case, dict(case=case), lambda case=case: asyncio.run(invocation(case)))
else: raise ValueError(sys.argv[2])
