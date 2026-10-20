"""Validate corpus structure, unique IDs and origin-neutral field invariants."""
import sys
from pathlib import Path
from common import load_corpus, validate_schema

def validate(folder):
    folder=Path(folder); errors=[]
    try: records=load_corpus(folder/'corpus.jsonl')
    except (OSError,ValueError) as exc: return [str(exc)]
    seen=set()
    for idx,r in enumerate(records,1):
        if not isinstance(r,dict):errors.append(f'line {idx}: expected object');continue
        key=str(r.get('review_id',''))
        if not key:errors.append(f'line {idx}: missing review_id')
        if key in seen:errors.append(f'line {idx}: duplicate review_id {key}')
        seen.add(key)
        if not str(r.get('product_id','')):errors.append(f'line {idx}: missing product_id')
        for k in ('text','pros','cons'):
            if not isinstance(r.get(k),str):errors.append(f'line {idx}: {k} must be a string')
        if not any(str(r.get(k,'')).strip() for k in ('text','pros','cons')):continue # empty feedback allowed
    try:
        from common import load_json
        data=load_json(folder/'manifest.json')
        for e in validate_schema(data,Path(__file__).resolve().parent.parent/'schemas/manifest.schema.json'):
            errors.append(e)
        for idx, rec in enumerate(records, 1):
            for e in validate_schema(rec,Path(__file__).resolve().parent.parent/'schemas/corpus.schema.json'):
                errors.append(f'line {idx}: {e}')
    except (OSError,ValueError) as exc: errors.append(f'manifest: {exc}')
    return errors
if __name__=='__main__':
    issues=validate(sys.argv[1] if len(sys.argv)>1 else '.')
    print('Corpus: '+('PASS' if not issues else 'FAIL'))
    for x in issues:print('ERROR: '+x)
    sys.exit(bool(issues))
