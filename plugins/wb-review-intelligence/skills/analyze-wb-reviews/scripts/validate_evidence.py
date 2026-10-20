"""Strict provenance and ID-set validation for REPORT_DATA v1.

Requires explicit observations and per-review analysis statuses. Structural
validation cannot prove the semantic correctness of labels or conclusions.
"""
import sys
from pathlib import Path
from collections import defaultdict
from common import load_json,load_corpus, validate_schema

def validate(folder, manifest_override=None):
    folder=Path(folder); errors=[]
    try:
        corpus=load_corpus(folder/'corpus.jsonl');analysis=load_json(folder/'analysis.json');manifest=load_json(folder/'manifest.json')
    except (ValueError,OSError) as exc:return [str(exc)]
    if manifest_override is not None: manifest=manifest_override
    from validate_corpus import validate as corpus_validate
    errors.extend(corpus_validate(folder))
    schema_root=Path(__file__).resolve().parent.parent/'schemas'
    for obj,name in [(analysis,'analysis'),(manifest,'manifest')]:
        for e in validate_schema(obj,schema_root/(name+'.schema.json')):
            errors.append(e)
    if errors: return errors
    if 'REPORT_DATA' not in analysis: errors.append('REPORT_DATA is required for evidence validation')
    if errors: return errors
    from pipeline import processing
    processing_state=processing(corpus,analysis,manifest)
    errors.extend(processing_state['reasons'])
    by_id={}
    for r in corpus:
        k=str(r.get('review_id',''))
        if not k or k in by_id:errors.append(f'bad or duplicate review_id: {k}')
        by_id[k]=r
    text_ids={k for k,r in by_id.items() if any(isinstance(r.get(f),str) and r[f].strip() for f in ['text','pros','cons'])}
    status=analysis.get('review_status',[]); status_by={}
    for x in status:
        k=str(x.get('review_id',''))
        if k in status_by:errors.append(f'duplicate review_status: {k}')
        status_by[k]=x.get('status')
    if set(status_by)!=text_ids:errors.append(f'review_status ID set mismatch: missing={len(text_ids-set(status_by))}, extra={len(set(status_by)-text_ids)}')
    pending=set(processing_state['pending_IDs'])
    if pending!=set(map(str,manifest.get('pending_IDs',[]))):errors.append('manifest pending_IDs differs from pending reviews')
    if manifest.get('analysis_status')=='complete' and pending:errors.append('complete analysis cannot have pending reviews')
    report=analysis.get('REPORT_DATA',{});areas=report.get('areas',{})
    if report.get('version')!=1:errors.append('REPORT_DATA version must be 1')
    if report.get('report_id')!=manifest.get('run_id'):errors.append('report_id != manifest.run_id')
    observations=analysis.get('observations',[])
    observed=defaultdict(set)
    observed_records=defaultdict(list)
    for o in observations:
        k=str(o.get('review_id','')); a=o.get('area');tid=o.get('theme_id');context=o.get('context')
        if k not in text_ids:errors.append(f'observation outside text corpus: {k}')
        if status_by.get(k)!='analyzed':errors.append(f'observation on pending review: {k}')
        if 'source_field' in o or 'excerpt' in o:
            rec=by_id.get(k,{});field=o.get('source_field');excerpt=o.get('excerpt')
            if field not in ('text','pros','cons') or not isinstance(excerpt,str) or not excerpt or excerpt not in rec.get(field,''):
                errors.append(f'observation excerpt/field mismatch: {k}')
        observed[(a,tid,context)].add(k)
        observed_records[(a,tid,context)].append(o)
    all_theme_keys=set()
    all_verified=True
    for a,data in areas.items():
        m=data.get('metrics',{}); pid=str(report.get('product',{}).get('id',''))
        article_id=str(data.get('product',{}).get('id',pid)) if a=='article' else pid
        scope=data.get('scope',{})
        group_ids=set(map(str,scope.get('product_ids',[]))) if a=='group' else set()
        if a=='group' and not group_ids:
            errors.append('group scope.product_ids is required for membership verification');group_ids=set()
        scoped={k for k,r in by_id.items() if str(r.get('product_id',''))==article_id} if a=='article' else {k for k,r in by_id.items() if str(r.get('product_id','')) in group_ids}
        scoped_text=scoped & text_ids
        for n,value in [('returned',len(scoped)),('text',len(scoped_text)),('empty',len(scoped-scoped_text)),('analyzed',len(scoped_text-pending))]:
            if m.get(n)!=value:errors.append(f'{a}.metrics.{n}: expected {value}, got {m.get(n)}')
        quote_by={}
        for q in data.get('quotes',[]):
            qid=q.get('id');rid=str(q.get('review_id',''));rec=by_id.get(rid);f=q.get('field')
            if qid in quote_by:errors.append(f'{a} duplicate quote id {qid}')
            quote_by[qid]=q
            if rid not in scoped_text:errors.append(f'{a} quote review not in scope: {rid}');continue
            if f not in ('text','pros','cons'):errors.append(f'{a} quote unknown field: {f}');continue
            origin=rec.get(f)
            if not isinstance(origin,str) or q.get('full_field_text')!=origin:errors.append(f'{a} full_field_text mismatch in {qid}')
            if not isinstance(q.get('text'),str) or not q.get('text') or q['text'] not in (origin or ''):errors.append(f'{a} excerpt not exact substring in {qid}')
            if str(q.get('product_id'))!=str(rec.get('product_id')):errors.append(f'{a} product_id mismatch in {qid}')
            if q.get('date')!=rec.get('date'):errors.append(f'{a} date mismatch in {qid}')
        theme_ids=set()
        for theme in data.get('themes',[]):
            tid=theme.get('id'); ctx=theme.get('context');key=(a,tid,ctx)
            if tid in theme_ids:errors.append(f'{a} duplicate theme id: {tid}')
            theme_ids.add(tid);all_theme_keys.add(key)
            mapped=observed.get(key,set()); refs=theme.get('quote_ids',[]);quotes=[]
            for ref in refs:
                if ref not in quote_by:errors.append(f'{a} dangling quote ref {ref}');continue
                quotes.append(quote_by[ref])
            evidenced={str(q.get('review_id')) for q in quotes}
            if theme.get('count')!=len(mapped):errors.append(f'{a}/{tid}: count {theme.get("count")} != observation unique IDs {len(mapped)}')
            denom=theme.get('denominator')
            if denom is not None and denom!=len(scoped_text):errors.append(f'{a}/{tid}: denominator {denom} != {len(scoped_text)}')
            if not mapped<=scoped_text:errors.append(f'{a}/{tid}: observation outside scope')
            if evidenced!=mapped:
                if theme.get('evidence_status')=='complete':errors.append(f'{a}/{tid}: marked complete but evidence ID set mismatch')
                if not theme.get('evidence_reason'):errors.append(f'{a}/{tid}: incomplete evidence requires evidence_reason')
                all_verified=False
            elif theme.get('evidence_status')=='partial':all_verified=False
            if theme.get('evidence_status')=='complete' and not mapped:errors.append(f'{a}/{tid}: no verified evidence')
            for o in observed_records[key]:
                if 'source_field' in o:
                    if not any(str(q['review_id'])==o['review_id'] and q['field']==o['source_field']
                               and o['excerpt'] in q['text'] for q in quotes):
                        errors.append(f'{a}/{tid}: observation excerpt missing from report quotes: {o["review_id"]}')
        for opp in data.get('opportunities',[]):
            for tid in opp.get('theme_ids',[]):
                if tid not in theme_ids:errors.append(f'{a} opportunity references missing theme {tid}')
    for key in observed:
        if key not in all_theme_keys:errors.append(f'observation references missing theme/context: {key}')
    if manifest.get('evidence_status')=='complete' and not all_verified:errors.append('manifest complete evidence conflicts with partial theme evidence')
    return errors

if __name__=='__main__':
    issues=validate(sys.argv[1] if len(sys.argv)>1 else '.')
    print('Evidence: '+('PASS' if not issues else 'FAIL'))
    for x in issues:print('ERROR: '+x)
    sys.exit(bool(issues))
