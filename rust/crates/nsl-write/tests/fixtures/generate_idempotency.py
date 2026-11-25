"""R8-S02 observations from an unmodified pin archive; no business provider I/O.

Usage: Python 3.12 -I -B generate_idempotency.py ARCHIVE keys|models|retention|reservations
Each TSV row is name, canonical input JSON hex, canonical original observation hex.
"""
import asyncio
import dataclasses
import runpy
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
from nsl.idempotency import *
from nsl.ir import canonical_json
from nsl.tools import ToolContract
from nsl.core import ToolCapability

PIN = '28a949ec96581cbdc52c8133527b23bb5b72ee0a'
h = runpy.run_path(str(Path(sys.argv[1]) / 'tests/test_v0_2_idempotency.py'))
NOW = h['NOW']
US = timedelta(microseconds=1)

def plain(v):
    if dataclasses.is_dataclass(v):
        return {f.name: plain(getattr(v, f.name)) for f in dataclasses.fields(v)}
    if isinstance(v, datetime): return v.isoformat()
    if isinstance(v, dict): return {k: plain(x) for k, x in v.items()}
    if isinstance(v, (tuple, list)): return [plain(x) for x in v]
    return v

def observe(fn):
    try: return ['ok', plain(fn())]
    except Exception as e: return ['error', type(e).__name__, str(e), getattr(e, 'detail_code', None)]

def emit(name, inputs, fn):
    print(name, canonical_json(plain(inputs)).hex(), canonical_json(observe(fn)).hex(), sep='\t')

def contract(config):
    c = h['_contract'](idempotency=h['_idempotency'](**{k:v for k,v in config.items() if k in ('scope','retention','duplicate_behavior','payload_conflict_behavior')}))
    if 'tool_id' in config: c = dataclasses.replace(c, tool_id=config['tool_id'])
    if config.get('capability'):
        c = dataclasses.replace(c, capability=ToolCapability(config['capability']), operation_kind=None, write_semantics=None)
    return c

def retention_input(config=None, **overrides):
    config = config or {}
    c = contract(config)
    e = plain(h['_provider_evidence'](c))
    e.update(overrides.pop('evidence', {}))
    return dict(contract=config, evidence=e, now=NOW.isoformat(), required_until=(NOW+timedelta(days=7)).isoformat(), **overrides)

def evidence(d):
    return ProviderIdempotencyEvidence(**{k: datetime.fromisoformat(v) if k in ('verified_at','guaranteed_until') else v for k,v in d.items()})

def validate(d):
    e = evidence(d['evidence'])
    return IdempotencyRetentionValidator().validate(intent=h['_intent'](), contract=contract(d['contract']), evidence=e, now=datetime.fromisoformat(d['now']), required_until=datetime.fromisoformat(d['required_until']))

def record():
    i = idempotency_identity(h['_intent']())
    return IdempotencyEvidenceRecord(**plain(i), tool_id='FINANCE.CREATE_DRAFT', contract_hash=h['_contract']().contract_hash, provider_guarantee_ref='provider-policy://finance/idempotency-v1', retained_until=NOW+timedelta(days=30))

def model(kind, d):
    if kind == 'identity': return IdempotencyIdentity(**d)
    if kind == 'provider': return evidence(d)
    if kind == 'record': return IdempotencyEvidenceRecord(**{**d, 'retained_until':datetime.fromisoformat(d['retained_until'])})
    return IdempotencyReservation(IdempotencyDisposition(d['disposition']), record(), d['business_result_ref'])

async def reservation_case(d):
    class Port:
        def __init__(self): self.records={}; self.results={}; self.calls=0
        async def reserve(self, incoming):
            self.calls += 1
            key = (incoming.tenant_id, incoming.tool_id, incoming.idempotency_key)
            previous = self.records.get(key)
            if d.get('port') == 'fail-before': raise OSError('synthetic protected error')
            disposition = IdempotencyDisposition.CREATED if previous is None else IdempotencyDisposition.CONFLICT if previous.payload_hash != incoming.payload_hash else IdempotencyDisposition.REUSED
            if previous is None: self.records[key] = incoming
            if d.get('port') == 'fail-after': raise OSError('synthetic protected error')
            returned = incoming
            if d.get('echo_old') and previous is not None: returned = previous
            if d.get('alter'):
                field, val = d['alter']
                if field == 'retained_until': val = datetime.fromisoformat(val)
                returned = dataclasses.replace(returned, **{field:val})
            if d.get('force_conflict'): disposition = IdempotencyDisposition.CONFLICT
            return IdempotencyReservation(disposition, returned, self.results.get(key) if disposition is not IdempotencyDisposition.CONFLICT else None)
    p = Port(); coordinator = IdempotencyCoordinator(p)
    contexts={}; outputs=[]
    for step in d['steps']:
        intent = h['_intent'](**step.get('intent', {}))
        c = contract(step.get('contract', {}))
        if c.tool_id != intent.tool_id:
            from nsl.write_intent import compute_write_intent_hash
            intent = dataclasses.replace(intent, tool_id=c.tool_id, write_intent_hash=compute_write_intent_hash(skill_semantic_hash=intent.skill_semantic_hash,write_node_id=intent.node_id,tool_id=c.tool_id,payload_hash=intent.payload_hash))
        ctx_id=step.get('context','default')
        if ctx_id not in contexts:
            ctx=contexts[ctx_id]=h['_context'](**step.get('context_values', {}))
            ctx.idempotency.keys_by_write_node.update(step.get('keys', {}))
            ctx.idempotency.payload_hashes_by_write_node.update(step.get('payloads', {}))
        ctx=contexts[ctx_id]
        if step.get('complete'):
            p.results[(intent.tenant_id,intent.tool_id,idempotency_identity(intent).idempotency_key)] = step['complete']
        e=h['_provider_evidence'](c, **{k:datetime.fromisoformat(v) if k in ('verified_at','guaranteed_until') else v for k,v in step.get('evidence',{}).items()})
        try:
            result=await coordinator.reserve(intent=intent,context=ctx,contract=c,provider_evidence=e,now=NOW,required_until=NOW+timedelta(days=7))
            result=['ok',plain(result)]
        except Exception as exc:
            result=['error',type(exc).__name__,str(exc),getattr(exc,'detail_code',None)]
        outputs.append(dict(result=result, keys=dict(ctx.idempotency.keys_by_write_node), payloads=dict(ctx.idempotency.payload_hashes_by_write_node), calls=p.calls, records=len(p.records)))
    return outputs

def main():
    print('# Python Oracle '+PIN)
    mode=sys.argv[2]
    if mode == 'keys':
        for field in ('tenant_id','business_operation_id','write_node_id'):
            for value in ('',' ','\x1c','tenant-b','가😀','quoted"\\\n\x00','Bearer synthetic-only'):
                d=dict(tenant_id='tenant-a',business_operation_id='business-0051',write_node_id='write0001');d[field]=value
                emit(field+'-'+repr(value),d,lambda d=d:IdempotencyKeyFactory().create(d['tenant_id'],d['business_operation_id'],d['write_node_id']))
        for name,changes in [('base',{}),('payload',dict(amount='84001')),('tenant',dict(tenant_id='tenant-b')),('business',dict(business_operation_id='other-business')),('node',dict(node_id='write0002'))]:
            emit('intent-'+name,changes,lambda changes=changes:idempotency_identity(h['_intent'](**changes)))
    elif mode == 'models':
        bases=dict(identity=plain(idempotency_identity(h['_intent']())),provider=plain(h['_provider_evidence'](h['_contract']())),record=plain(record()),reservation=dict(disposition='REUSED',business_result_ref=None))
        for kind,base in bases.items():
            emit(kind+'-base',dict(kind=kind,value=base),lambda k=kind,d=base:model(k,d))
            for field,value in base.items():
                if field in ('verified_at','guaranteed_until','retained_until','durable_duplicate_guarantee','disposition','business_result_ref'): continue
                for val in ('',' ','Bearer synthetic-only'):
                    d={**base,field:val};emit(kind+'-'+field+'-'+repr(val),dict(kind=kind,value=d),lambda k=kind,d=d:model(k,d))
            if kind in ('identity','record'):
                for n in (0,1,63,64,65):
                    for char in ('x','😀'):
                        d={**base,'idempotency_key':'idem:sha256:'+char*n};emit(f'{kind}-key-{char}-{n}',dict(kind=kind,value=d),lambda k=kind,d=d:model(k,d))
                for val in ('sha256:'+'A'*64,'sha256:'+'a'*63,'sha256:'+'a'*65):
                    d={**base,'payload_hash':val};emit(kind+'-payload-'+val,dict(kind=kind,value=d),lambda k=kind,d=d:model(k,d))
        for delta in (-1,0,1):
            d={**bases['provider'],'guaranteed_until':(NOW+delta*US).isoformat()};emit('provider-until-'+str(delta),dict(kind='provider',value=d),lambda d=d:model('provider',d))
        for val in (datetime.min.replace(tzinfo=UTC),NOW,datetime.max.replace(tzinfo=UTC)):
            d={**bases['record'],'retained_until':val.isoformat()};emit('record-time-'+val.isoformat(),dict(kind='record',value=d),lambda d=d:model('record',d))
        for disp in ('CREATED','REUSED','CONFLICT'):
            for ref in (None,'result://original','','Bearer synthetic-only'):
                d=dict(disposition=disp,business_result_ref=ref);emit('reservation-'+disp+'-'+repr(ref),dict(kind='reservation',value=d),lambda d=d:model('reservation',d))
    elif mode == 'retention':
        cases=[('base',retention_input())]
        for field in ('required_until','now'):
            pivot=NOW if field=='required_until' else NOW+timedelta(days=30)
            for n in (-1,0,1):
                d=retention_input();d[field]=(pivot+n*US).isoformat()
                if field=='now': d['required_until']=(pivot+timedelta(days=1)).isoformat()
                cases.append((field+'-'+str(n),d))
        for n in (-1,0,1):
            cases.append(('horizon-'+str(n),retention_input(evidence={'guaranteed_until':(NOW+timedelta(days=7)+n*US).isoformat()})))
            cases.append(('verified-'+str(n),retention_input(evidence={'verified_at':(NOW+n*US).isoformat()})))
        for field,val in [('tool_id','OTHER.TOOL'),('contract_hash','sha256:'+'e'*64),('retention_policy','OTHER_POLICY'),('durable_duplicate_guarantee',False)]:cases.append(('evidence-'+field,retention_input(evidence={field:val})))
        for field,val in [('scope','GLOBAL'),('duplicate_behavior','RAISE'),('payload_conflict_behavior','OVERWRITE'),('tool_id','OTHER.TOOL'),('capability','READ'),('capability','TRANSACTION')]:cases.append(('contract-'+field+'-'+val,retention_input({field:val})))
        cases.append(('opaque-policy',retention_input({'retention':'POLICY:v2'},evidence={'retention_policy':'POLICY:v2'})))
        cases.append(('minimum-instant',retention_input(evidence={'verified_at':datetime.min.replace(tzinfo=UTC).isoformat()})))
        cases.append(('maximum-instant',retention_input(evidence={'guaranteed_until':datetime.max.replace(tzinfo=UTC).isoformat()})))
        for name,d in cases:emit(name,d,lambda d=d:validate(d))
    elif mode == 'reservations':
        i=idempotency_identity(h['_intent']())
        scenarios=[('fresh',dict(steps=[{}])),('pending-reuse',dict(steps=[{},{}])),('original-result',dict(steps=[{},dict(complete='result://original')])),('cache-conflict',dict(steps=[{},dict(intent={'amount':'84001'})])),('durable-conflict',dict(steps=[{},dict(context='new',intent={'amount':'84001'})])),('fresh-context-reuse',dict(steps=[{},dict(context='new')])),('tenant-mismatch',dict(steps=[dict(context_values={'tenant_id':'tenant-b'})])),('business-mismatch',dict(steps=[dict(context_values={'business_operation_id':'other'})])),('business-missing',dict(steps=[dict(context_values={'business_operation_id':None})])),('stale-key',dict(steps=[dict(keys={'write0001':'idem:sha256:'+'e'*64})])),('stale-payload',dict(steps=[dict(payloads={'write0001':'sha256:'+'e'*64})])),('partial-key',dict(steps=[dict(keys={'write0001':i.idempotency_key})])),('partial-payload',dict(steps=[dict(payloads={'write0001':i.payload_hash})])),('other-node-preserved',dict(steps=[dict(keys={'other':'raw'},payloads={'other':'raw'})])),('fail-before',dict(port='fail-before',steps=[{}])),('fail-after',dict(port='fail-after',steps=[{}])),('conflict',dict(force_conflict=True,steps=[{}])),('policy-change-echo',dict(steps=[{},dict(evidence={'provider_guarantee_ref':'policy://v2'})])),('old-policy-rejected',dict(echo_old=True,steps=[{},dict(evidence={'provider_guarantee_ref':'policy://v2'})])),('ttl-change-echo',dict(steps=[{},dict(evidence={'guaranteed_until':(NOW+timedelta(days=31)).isoformat()})])),('old-ttl-rejected',dict(echo_old=True,steps=[{},dict(evidence={'guaranteed_until':(NOW+timedelta(days=31)).isoformat()})])),('two-tools',dict(steps=[{},dict(context='new',contract={'tool_id':'OTHER.TOOL'})])),('two-tenants',dict(steps=[{},dict(context='new',intent={'tenant_id':'tenant-b'},context_values={'tenant_id':'tenant-b'})])),('two-operations',dict(steps=[{},dict(context='new',intent={'business_operation_id':'business-b'},context_values={'business_operation_id':'business-b'})])),('two-nodes',dict(steps=[{},dict(intent={'node_id':'write0002'})]))]
        for field,val in plain(record()).items():
            altered=(NOW+timedelta(days=31)).isoformat() if field=='retained_until' else 'sha256:'+'e'*64 if field in ('contract_hash','payload_hash') else 'idem:sha256:'+'e'*64 if field=='idempotency_key' else 'different'
            scenarios.append(('foreign-'+field,dict(alter=[field,altered],steps=[{}])))
        scenarios.append(('foreign-conflict-priority',dict(force_conflict=True,alter=['tenant_id','other'],steps=[{}])))
        for name,d in scenarios:emit(name,d,lambda d=d:asyncio.run(reservation_case(d)))
    else: raise ValueError(mode)

main()
