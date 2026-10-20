"""Offline helpers. No network, no arbitrary code execution from input."""
import json
from functools import lru_cache
from pathlib import Path

def load_json(path):
    with Path(path).open(encoding='utf-8') as f: return json.load(f)

def load_corpus(path):
    records=[]
    with Path(path).open(encoding='utf-8') as f:
        for line_no,line in enumerate(f,1):
            if not line.strip(): continue
            try: records.append(json.loads(line))
            except json.JSONDecodeError as e: raise ValueError(f'corpus.jsonl:{line_no}: {e}') from e
    return records

def issue(issues,message): issues.append(message)

@lru_cache(maxsize=8)
def schema_validator(schema_path):
    import jsonschema
    from referencing import Registry, Resource
    root = Path(schema_path).parent
    registry = Registry()
    for p in root.glob('*.schema.json'):
        registry = registry.with_resource(p.name, Resource.from_contents(load_json(p)))
    return jsonschema.Draft202012Validator(load_json(schema_path), registry=registry)


def validate_schema(data, schema_path):
    try:
        schema_validator(str(schema_path)).validate(data)
        return []
    except ImportError:
        return ['jsonschema/referencing is not installed: validation BLOCKED; install jsonschema>=4.18']
    except Exception as exc:
        return [f'{Path(schema_path).name}: {exc.message if hasattr(exc, "message") else exc}']


def areas_for(record, article_id, group_ids):
    product=str(record.get('product_id',''))
    groups=[]
    if article_id and product==str(article_id):groups.append('article')
    if product in group_ids:groups.append('group')
    return groups


def digest(value):
    """Canonical SHA-256; independent of formatting, sensitive to all data fields."""
    import hashlib
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def atomic_json(path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def atomic_text(path, value):
    """Same-directory replace, fsync data and directory; no persistent fourth file."""
    import os, tempfile
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix='.'+path.name+'-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(value); f.flush(); os.fsync(f.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(name): os.unlink(name)
