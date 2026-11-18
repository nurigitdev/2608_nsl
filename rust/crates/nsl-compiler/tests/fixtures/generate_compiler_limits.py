"""Measure canonical NSO JSON units from the frozen compiler fixture."""
import json
import sys
from pathlib import Path

rows=Path(sys.argv[1]).read_text().splitlines()
print('# Oracle 28a949e canonical UTF-8 bytes; root depth 0; values count, keys do not')
header=None
for row in rows:
    columns=row.split('\t')
    if columns[0]=='case':header=columns
    elif columns[0]=='ok':
        raw=bytes.fromhex(columns[3]);data=json.loads(raw)
        pending=[(data,0)];nodes=0;depth=0
        while pending:
            value,current=pending.pop();nodes+=1;depth=max(depth,current)
            if isinstance(value,dict):pending.extend((item,current+1) for item in value.values())
            elif isinstance(value,list):pending.extend((item,current+1) for item in value)
        print(header[1],header[2],len(raw),depth,nodes,sep='\t')
