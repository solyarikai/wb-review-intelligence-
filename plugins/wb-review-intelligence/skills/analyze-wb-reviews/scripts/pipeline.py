"""Offline research controller. State lives in the three research files.

No networking, no model calls, no semantic certification. All PASS decisions
are recomputed from artifacts. One writer per folder (directory flock), atomic
file replacement, analysis-first checkpoints, derived manifest recovery.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

from common import atomic_json, atomic_text, digest, load_corpus, load_json, validate_schema
from validate_corpus import validate as corpus_validate
from validate_evidence import validate as evidence_validate

SCHEMAS = Path(__file__).resolve().parent.parent / 'schemas'
STAGES = ('collection', 'processing', 'evidence', 'report', 'ui', 'completion')
UI_CHECKS = ('areas', 'tabs', 'filters', 'all_themes', 'hidden_quote_search',
             'question_search', 'singleton', 'pagination', 'full_field',
             'local_expansion', 'links', 'mobile', 'quote_metadata')


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def locked(folder):
    """Lock stable directory inode, never the atomically replaced manifest inode."""
    import fcntl
    fd = os.open(folder, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def result(status, *reasons, **values):
    return dict(status=status, reasons=list(reasons), **values)


def read(folder):
    folder = Path(folder)
    c = load_corpus(folder/'corpus.jsonl')
    a = load_json(folder/'analysis.json')
    m = load_json(folder/'manifest.json')
    if not isinstance(a, dict) or not isinstance(m, dict):
        raise ValueError('analysis and manifest must be objects')
    errors = corpus_validate(folder)
    if errors:
        raise ValueError('\n'.join(errors))
    # REPORT_DATA may be absent during processing; validate it only at report stage.
    mapping = {k: v for k, v in a.items() if k != 'REPORT_DATA'}
    errors = validate_schema(mapping, SCHEMAS/'analysis.schema.json')
    if errors:
        raise ValueError('\n'.join(errors))
    return c, a, m


def text_records(corpus):
    return {r['review_id']: r for r in corpus
            if any(r[f].strip() for f in ('text', 'pros', 'cons'))}


def scope_digest(manifest):
    requested = manifest['execution']['requested_areas']
    coverage = manifest.get('source_coverage', {})
    if any(area not in coverage for area in requested):
        return None
    identities = {area: (coverage[area]['product_id'] if area=='article'
                         else sorted(coverage[area]['product_ids'])) for area in requested}
    return digest(identities)


def processing(corpus, analysis, manifest):
    records = text_records(corpus)
    done, errors, seen = set(), [], set()
    observations = analysis.get('observations', [])
    observed_ids = {o['review_id'] for o in observations}
    for s in analysis.get('review_status', []):
        rid = s['review_id']
        if rid in seen:
            errors.append('duplicate review_status: '+rid)
        seen.add(rid)
        if rid not in records:
            errors.append('status outside text corpus: '+rid)
            continue
        if s['status'] != 'analyzed':
            continue
        rec = records[rid]
        fields = {f for f in ('text', 'pros', 'cons') if rec[f].strip()}
        if s.get('record_sha256') != digest(rec):
            errors.append('missing/stale record hash: '+rid)
        elif not scope_digest(manifest) or s.get('scope_sha256') != scope_digest(manifest):
            errors.append('missing/stale scope hash: '+rid)
        elif set(s.get('reviewed_fields', [])) != fields:
            errors.append('not all nonempty fields reviewed: '+rid)
        elif rid not in observed_ids and not s.get('no_theme_reason', '').strip():
            errors.append('no-theme review requires reason: '+rid)
        else:
            done.add(rid)
    for o in observations:
        if o['review_id'] not in done:
            errors.append('observation on unprocessed review: '+o['review_id'])
    pending = sorted(set(records)-done)
    state = 'FAIL' if errors else ('PASS' if not pending else ('RUNNING' if done else 'PENDING'))
    return result(state, *errors, text=len(records), analyzed=len(done), pending_IDs=pending)


def scope_ids(corpus, area, scope):
    if area == 'article':
        return {r['review_id'] for r in corpus if r['product_id'] == scope['product_id']}
    return {r['review_id'] for r in corpus if r['product_id'] in scope['product_ids']}


def collection(corpus, manifest):
    requested = manifest['execution']['requested_areas']
    coverage = manifest.get('source_coverage', {})
    areas, reasons = {}, []
    for area in requested:
        s = coverage.get(area)
        if not s:
            areas[area] = result('BLOCKED', 'coverage receipt missing')
            reasons.append(area+': coverage receipt missing')
            continue
        ids = scope_ids(corpus, area, s)
        texts = ids & set(text_records(corpus))
        counts = dict(returned=len(ids), text=len(texts), empty=len(ids-texts))
        comparable = all(isinstance(s.get(k), int) and not isinstance(s.get(k), bool)
                         for k in ('expected_returned', 'expected_text'))
        complete = (comparable and s['expected_returned'] == len(ids)
                    and s['expected_text'] == len(texts) and s.get('exhausted') is True
                    and bool(s.get('basis', '').strip()) and bool(s.get('observed_at', '').strip()))
        reason = 'counts, exhaustion or provenance do not prove full source coverage'
        areas[area] = result('PASS' if complete else 'BLOCKED', *([] if complete else [reason]), **counts)
        if not complete:
            reasons.append(area+': '+reason)
    if 'article' in requested and 'group' in requested and all(x in coverage for x in requested):
        if coverage['article']['product_id'] not in coverage['group']['product_ids']:
            reasons.append('article is not in confirmed group')
    if 'group' in coverage:
        outside = [r['review_id'] for r in corpus if r['product_id'] not in coverage['group']['product_ids']]
        if outside:
            reasons.append('corpus contains reviews outside confirmed group: '+str(len(outside)))
    return result('PASS' if not reasons else 'BLOCKED', *reasons, areas=areas)


def input_digest(corpus, analysis, manifest):
    return digest(dict(corpus=corpus, analysis=analysis,
                       source_coverage=manifest['source_coverage'],
                       requested_areas=manifest['execution']['requested_areas']))


def report_errors(corpus, analysis, manifest):
    report = analysis.get('REPORT_DATA')
    errors = validate_schema(report, SCHEMAS/'report.schema.json')
    if errors:
        return errors
    areas = report['areas']
    if set(areas) != set(manifest['execution']['requested_areas']):
        errors.append('report areas != requested_areas')
    coverage = manifest.get('source_coverage', {})
    for area, data in areas.items():
        s = coverage.get(area)
        if not s:
            errors.append(area+': scope identity not confirmed')
        elif area == 'article':
            if str(data.get('product', {}).get('id', report.get('product', {}).get('id', ''))) != s['product_id']:
                errors.append('article identity mismatch')
        elif set(data.get('scope', {}).get('product_ids', [])) != set(s['product_ids']):
            errors.append('group identity mismatch')
    return errors


def ui_result(analysis, manifest, snapshot):
    receipt = manifest['execution'].get('ui_receipt')
    if not receipt:
        return result('PENDING', 'UI not verified in host')
    if receipt.get('input_sha256') != snapshot:
        return result('FAIL', 'UI receipt is stale')
    if receipt.get('available') is False:
        return result('BLOCKED', receipt.get('reason', 'native UI unavailable'))
    errors = []
    from render_report import renderer_sha256
    if receipt.get('renderer_sha256') != renderer_sha256():
        errors.append('UI receipt does not match pinned renderer; verify current renderer in host')
    if not receipt.get('mechanism', '').strip() or not receipt.get('checked_at', '').strip():
        errors.append('UI mechanism and checked_at required')
    checks = receipt.get('checks', {})
    areas = analysis.get('REPORT_DATA', {}).get('areas', {})
    themes = [t for d in areas.values() for t in d['themes']]
    applicable = dict(singleton=any(t['count']==1 for t in themes),
                      pagination=any(len(t['quote_ids'])>5 for t in themes),
                      question_search=any(d['questions'] for d in areas.values()),
                      links=bool(themes))
    for key in UI_CHECKS:
        check = checks.get(key, {})
        allowed = ('PASS', 'N/A') if applicable.get(key) is False else ('PASS',)
        if check.get('status') not in allowed or not check.get('evidence', '').strip():
            errors.append('UI check missing/failed: '+key)
    areas = analysis.get('REPORT_DATA', {}).get('areas', {})
    inventories = receipt.get('inventory', {})
    if set(inventories) != set(areas):
        errors.append('UI inventory area set mismatch')
    for area, data in areas.items():
        inv = inventories.get(area, {})
        for key, field in [('theme_ids', 'themes'), ('quote_ids', 'quotes'),
                           ('question_ids', 'questions'), ('opportunity_ids', 'opportunities')]:
            ids = inv.get(key, [])
            if len(ids) != len(set(ids)) or set(ids) != {x['id'] for x in data[field]}:
                errors.append('UI inventory mismatch: '+area+'/'+key)
    return result('FAIL' if errors else 'PASS', *errors)


def assess(folder):
    corpus, analysis, manifest = read(folder)
    execution = manifest.get('execution', {})
    if execution.get('version') != 1:
        raise ValueError('controller not initialized; run init before continuing')
    p = processing(corpus, analysis, manifest)
    c = collection(corpus, manifest)
    re = report_errors(corpus, analysis, manifest)
    # Reconcile derived statuses for the strict existing evidence validator.
    mirror = dict(manifest, pending_IDs=p['pending_IDs'],
                  analysis_status='complete' if p['status']=='PASS' else 'partial',
                  evidence_status='partial')
    if re:
        e = result('BLOCKED', 'REPORT_DATA required for full evidence graph', *re)
    else:
        errors = evidence_validate(folder, manifest_override=mirror)
        partial = [area+'/'+t['id'] for area, data in analysis['REPORT_DATA']['areas'].items()
                   for t in data['themes'] if t['evidence_status'] != 'complete']
        e = result('FAIL' if errors else ('BLOCKED' if partial else 'PASS'), *errors,
                   *(['unconfirmed themes: '+', '.join(partial)] if partial else []))
    r = result('FAIL', *re) if re else result('PASS')
    if p['status'] != 'PASS' or e['status'] != 'PASS':
        r = result('BLOCKED', 'processing and evidence must PASS before report')
    snapshot = input_digest(corpus, analysis, manifest)
    u = ui_result(analysis, manifest, snapshot)
    if r['status'] != 'PASS' and u['status'] == 'PASS':
        u = result('BLOCKED', 'report validation must PASS before UI')
    stages = dict(collection=c, processing=p, evidence=e, report=r, ui=u)
    # Explicit unresolved errors cannot be erased by a computed ready result.
    for key, error in execution.get('errors', {}).items():
        stages[key] = result(error['status'], error['reason'])
    success = all(s['status']=='PASS' for s in stages.values())
    stages['completion'] = result('PASS' if success else 'BLOCKED',
                                   *([] if success else ['mandatory stages have not all passed']))
    return dict(version=1, input_sha256=snapshot, stages=stages,
                pending_IDs=p['pending_IDs'], success=success)


def sync(folder):
    state = assess(folder)
    m = load_json(Path(folder)/'manifest.json')
    m['execution'].update(state)
    m['execution']['updated_at'] = now()
    m['pending_IDs'] = state['pending_IDs']
    m['analysis_status'] = 'complete' if state['stages']['processing']['status']=='PASS' else 'partial'
    m['evidence_status'] = 'complete' if state['stages']['evidence']['status']=='PASS' else 'partial'
    u = state['stages']['ui']['status']
    unavailable = m['execution'].get('ui_receipt', {}).get('available') is False
    m['ui_status'] = 'verified' if u=='PASS' else ('not_available' if unavailable else 'not_verified')
    m['research_status'] = 'complete' if state['success'] else 'incomplete'
    atomic_json(Path(folder)/'manifest.json', m)
    return state


def initialize(folder, run_id=None, requested_areas=None):
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    with locked(folder):
        mp, ap, cp = (folder/x for x in ('manifest.json', 'analysis.json', 'corpus.jsonl'))
        m = load_json(mp) if mp.exists() else {}
        if m.get('execution', {}).get('version') == 1:
            if run_id and run_id != m['run_id']:
                raise ValueError('existing run_id cannot change')
            if requested_areas and requested_areas != m['execution']['requested_areas']:
                raise ValueError('existing requested_areas cannot change')
            return sync(folder)
        if not m.get('run_id') and not run_id:
            raise ValueError('run_id required for a new research')
        m.setdefault('run_id', run_id)
        if run_id and m['run_id'] != run_id:
            raise ValueError('existing run_id cannot change')
        m.update(analysis_status='partial', evidence_status='not_verified',
                 ui_status='not_verified', pending_IDs=[])
        coverage = m.setdefault('source_coverage', {})
        if any(k not in ('article','group') for k in coverage):
            m['legacy_source_coverage'] = coverage
            m['source_coverage'] = {}
        m['execution'] = dict(version=1, requested_areas=requested_areas or ['article', 'group'])
        a = load_json(ap) if ap.exists() else dict(review_status=[], observations=[])
        if not cp.exists(): atomic_text(cp, '')
        # Legacy analyzed flags are not proof of field-by-field processing.
        for s in a.get('review_status', []):
            if s.get('status') == 'analyzed' and (not s.get('record_sha256') or not s.get('scope_sha256')):
                s.update(status='pending', reason='legacy status needs field/hash verification')
        if any(s['status']=='pending' for s in a.get('review_status', [])):
            # Preserve legacy graph for traceable re-analysis, never certify it.
            a['legacy_observations'] = a.get('observations', [])
            a['observations'] = []
        atomic_json(ap, a); atomic_json(mp, m)
        return sync(folder)


def invalidate(manifest, status, reason):
    manifest['execution']['success'] = False
    manifest['execution'].setdefault('stages', {})['completion'] = result(status, reason)
    manifest['research_status'] = 'incomplete'


def mutate(folder, action):
    folder = Path(folder)
    with locked(folder):
        c, a, m = read(folder)
        if m.get('execution', {}).get('version') != 1:
            raise ValueError('run init first')
        # Write-ahead invalidation: interrupted mutation never leaves saved success.
        invalidate(m, 'RUNNING', 'mutation in progress')
        atomic_json(folder/'manifest.json', m)
        try:
            action(c, a, m)
            return sync(folder)
        except Exception as exc:
            m = load_json(folder/'manifest.json')
            invalidate(m, 'FAIL', str(exc))
            atomic_json(folder/'manifest.json', m)
            raise


def ingest(folder, records):
    def action(c, a, m):
        by_id = {r['review_id']: r for r in c}
        for rec in records:
            errors = validate_schema(rec, SCHEMAS/'corpus.schema.json')
            if errors: raise ValueError('\n'.join(errors))
            rid = rec['review_id']
            if rid in by_id and by_id[rid] != rec:
                raise ValueError('conflicting existing review-ID: '+rid)
            by_id[rid] = rec
        atomic_text(Path(folder)/'corpus.jsonl', ''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in by_id.values()))
    return mutate(folder, action)


def set_coverage(folder, coverage):
    def action(c, a, m):
        candidate = dict(m, source_coverage=coverage)
        errors = validate_schema(candidate, SCHEMAS/'manifest.schema.json')
        if errors: raise ValueError('\n'.join(errors))
        m['source_coverage'] = coverage
        atomic_json(Path(folder)/'manifest.json', m)
    return mutate(folder, action)


def next_batch(folder, size=50):
    if not 1 <= size <= 200:
        raise ValueError('batch size must be 1..200')
    folder = Path(folder)
    with locked(folder):
        c, a, m = read(folder)
        state = sync(folder)
        fatal = [e for e in state['stages']['processing']['reasons']
                 if e.startswith(('duplicate review_status:', 'status outside text corpus:'))]
        if fatal:
            raise ValueError('; '.join(fatal))
        scope_hash = scope_digest(m)
        if not scope_hash:
            raise ValueError('confirm requested scope identities with coverage before next-batch')
        ids = state['pending_IDs'][:size]
        by_id = text_records(c)
        batch = dict(scope_sha256=scope_hash, review_ids=ids, records=[by_id[i] for i in ids],
                     record_sha256={i:digest(by_id[i]) for i in ids})
        batch['batch_id'] = digest(dict(run_id=m['run_id'], scope_sha256=scope_hash, records=batch['record_sha256']))
        m = load_json(folder/'manifest.json')
        m['execution']['active_batch'] = {k:v for k,v in batch.items() if k != 'records'}
        if ids:
            m['execution']['stages']['processing'] = result('RUNNING', 'batch issued', batch_id=batch['batch_id'])
        atomic_json(folder/'manifest.json', m)
        return batch


def commit_batch(folder, payload):
    def action(c, a, m):
        batch = m['execution'].get('active_batch', {})
        if not batch.get('review_ids') or payload.get('batch_id') != batch.get('batch_id'):
            raise ValueError('no matching active batch; request next-batch')
        if batch.get('scope_sha256') != scope_digest(m):
            raise ValueError('batch scope changed; request next-batch')
        reviews = payload.get('reviews', [])
        ids = [s['review_id'] for s in reviews]
        if len(ids) != len(set(ids)) or set(ids) != set(batch['review_ids']):
            raise ValueError('batch must commit exactly its issued review-ID set')
        records = text_records(c)
        new_obs, statuses = [], []
        for review in reviews:
            rid = review['review_id']; rec = records[rid]
            if digest(rec) != batch['record_sha256'][rid] or review.get('record_sha256') != digest(rec):
                raise ValueError('batch record changed: '+rid)
            fields = {f for f in ('text', 'pros', 'cons') if rec[f].strip()}
            if set(review.get('reviewed_fields', [])) != fields:
                raise ValueError('must review every nonempty field: '+rid)
            obs = review.get('observations', [])
            if not obs and not review.get('no_theme_reason', '').strip():
                raise ValueError('no_theme_reason required: '+rid)
            for o in obs:
                o = dict(o, review_id=rid)
                if o.get('area') not in m['execution']['requested_areas'] or not o.get('theme_id') or o.get('context') not in ('positive','negative','neutral'):
                    raise ValueError('invalid observation: '+rid)
                scope = m.get('source_coverage', {}).get(o['area'])
                if not scope or rid not in scope_ids(c, o['area'], scope):
                    raise ValueError('observation outside confirmed scope: '+rid)
                field = o.get('source_field'); excerpt = o.get('excerpt')
                if field not in fields or not isinstance(excerpt,str) or not excerpt or excerpt not in rec[field]:
                    raise ValueError('observation requires exact excerpt in nonempty source_field: '+rid)
                new_obs.append(o)
            statuses.append(dict(review_id=rid, status='analyzed', reviewed_fields=sorted(fields),
                                 record_sha256=digest(rec), scope_sha256=scope_digest(m), no_theme_reason=review.get('no_theme_reason', '')))
        # Single authoritative write: a crash before manifest write is recoverable.
        a['review_status'] = [s for s in a.get('review_status', []) if s['review_id'] not in ids]+statuses
        a['observations'] = [o for o in a.get('observations', []) if o['review_id'] not in ids]+new_obs
        atomic_json(Path(folder)/'analysis.json', a)
    return mutate(folder, action)


def set_report(folder, report):
    def action(c, a, m):
        if processing(c, a, m)['status'] != 'PASS':
            raise ValueError('finish processing all reviews before report assembly')
        errors = validate_schema(report, SCHEMAS/'report.schema.json')
        if errors: raise ValueError('\n'.join(errors))
        candidate = dict(a, REPORT_DATA=report)
        errors = report_errors(c, candidate, m)
        if errors: raise ValueError('\n'.join(errors))
        a['REPORT_DATA'] = report
        atomic_json(Path(folder)/'analysis.json', a)
    return mutate(folder, action)


def attest_ui(folder, receipt):
    def action(c, a, m):
        state = assess(folder)
        if state['stages']['report']['status'] != 'PASS':
            raise ValueError('report must PASS before UI attestation')
        if receipt.get('input_sha256') != state['input_sha256']:
            raise ValueError('UI receipt does not match current input_sha256')
        candidate = json.loads(json.dumps(m))
        candidate['execution']['ui_receipt'] = receipt
        errors = validate_schema(candidate, SCHEMAS/'manifest.schema.json')
        if errors: raise ValueError('\n'.join(errors))
        m['execution']['ui_receipt'] = receipt
        atomic_json(Path(folder)/'manifest.json', m)
    return mutate(folder, action)


def set_error(folder, stage, reason=None, failed=False):
    if stage not in STAGES[:-1]: raise ValueError('unknown stage')
    def action(c, a, m):
        errors = m['execution'].setdefault('errors', {})
        if reason:
            errors[stage] = dict(status='FAIL' if failed else 'BLOCKED', reason=reason, at=now())
        else:
            errors.pop(stage, None)
        atomic_json(Path(folder)/'manifest.json', m)
    return mutate(folder, action)


def check(folder):
    with locked(folder):
        try:
            return sync(folder)
        except Exception as exc:
            try:
                m = load_json(Path(folder)/'manifest.json')
                if 'execution' in m:
                    invalidate(m, 'FAIL', str(exc))
                    atomic_json(Path(folder)/'manifest.json', m)
            except (ValueError, OSError, TypeError):
                pass
            raise


def finish(folder):
    state = check(folder)
    if not state['success']:
        raise ValueError('research incomplete: '+', '.join(k+'='+s['status'] for k,s in state['stages'].items() if s['status']!='PASS'))
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder'); sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('init'); p.add_argument('--run-id'); p.add_argument('--areas', nargs='+', choices=['article','group'])
    for cmd in ('ingest','coverage','commit-batch','report','ui'):
        p = sub.add_parser(cmd); p.add_argument('--input', required=True, help='JSON file, or - for stdin')
    p = sub.add_parser('next-batch'); p.add_argument('--size', type=int, default=50)
    p = sub.add_parser('render'); p.add_argument('--out', required=True)
    p.add_argument('--allow-partial-source', action='store_true')
    for cmd in ('status','check','finish'): sub.add_parser(cmd)
    for cmd in ('block','fail','retry'):
        p = sub.add_parser(cmd); p.add_argument('stage', choices=STAGES[:-1])
        if cmd != 'retry': p.add_argument('--reason', required=True)
    args = parser.parse_args()
    try:
        payload = (json.load(sys.stdin) if args.input=='-' else load_json(args.input)) if hasattr(args,'input') else None
        cmd = args.command
        if cmd=='init': state=initialize(args.folder,args.run_id,args.areas)
        elif cmd=='ingest': state=ingest(args.folder,payload)
        elif cmd=='coverage': state=set_coverage(args.folder,payload)
        elif cmd=='next-batch': state=next_batch(args.folder,args.size)
        elif cmd=='commit-batch': state=commit_batch(args.folder,payload)
        elif cmd=='report': state=set_report(args.folder,payload)
        elif cmd=='ui': state=attest_ui(args.folder,payload)
        elif cmd=='render':
            from render_report import render
            state=render(args.folder,args.out,args.allow_partial_source)
        elif cmd in ('block','fail','retry'): state=set_error(args.folder,args.stage,getattr(args,'reason',None),cmd=='fail')
        elif cmd=='finish': state=finish(args.folder)
        else: state=check(args.folder)
        print(json.dumps(state, ensure_ascii=False, indent=2))
        # status is inspectable even when incomplete; check/finish enforce readiness.
        if cmd in ('check','finish'): return int(not state['success'])
        if cmd=='report': return int(state['stages']['report']['status'] != 'PASS')
        if cmd=='ui': return int(state['stages']['ui']['status']=='FAIL')
        return 0
    except Exception as exc:
        # A failed command must invalidate stale success, including malformed input.
        try:
            with locked(args.folder):
                mp=Path(args.folder)/'manifest.json'; m=load_json(mp)
                if 'execution' in m:
                    m['execution']['success']=False
                    m['execution'].setdefault('stages', {})['completion']=result('FAIL', str(exc))
                    m['execution']['last_error']=dict(command=args.command, reason=str(exc), at=now())
                    m['research_status']='incomplete'; atomic_json(mp,m)
        except (ValueError,OSError,TypeError): pass
        print('FAIL: '+str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())

