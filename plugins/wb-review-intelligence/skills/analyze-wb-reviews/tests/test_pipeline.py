"""Synthetic failure/recovery scenarios; never use these as buyer feedback."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import pipeline as p
from build_report_data import build
from common import atomic_json, digest, load_corpus, load_json
from render_report import renderer_sha256
from validate_evidence import validate as evidence_check

FIX = Path(__file__).parent/'fixtures/valid'


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        for f in FIX.iterdir():
            shutil.copy(f, self.folder/f.name)

    def get(self, name):
        return load_json(self.folder/(name+'.json'))

    def put(self, name, obj):
        atomic_json(self.folder/(name+'.json'), obj)

    def partial(self):
        a = self.get('analysis')
        a['review_status'][1]['status'] = 'pending'
        a['observations'] = a['observations'][:2]
        g = a['REPORT_DATA']['areas']['group']
        g['themes'] = g['themes'][:1]; g['quotes'] = g['quotes'][:1]
        g['metrics']['analyzed'] = 1
        self.put('analysis', a)
        return p.check(self.folder)

    def receipt(self):
        s = p.check(self.folder)
        report = self.get('analysis')['REPORT_DATA']
        return dict(input_sha256=s['input_sha256'], available=True,
                    renderer_sha256=renderer_sha256(),
                    mechanism='synthetic host test adapter; not actual ChatGPT UI',
                    checked_at='2026-10-17T00:00:00Z',
                    checks={k:dict(status='PASS', evidence='synthetic scenario '+k) for k in p.UI_CHECKS},
                    inventory={area:{key:[x['id'] for x in data[field]] for key,field in
                                     [('theme_ids','themes'),('quote_ids','quotes'),
                                      ('question_ids','questions'),('opportunity_ids','opportunities')]}
                               for area,data in report['areas'].items()})

    def payload(self, batch):
        return dict(batch_id=batch['batch_id'], reviews=[
            dict(review_id=rec['review_id'], record_sha256=digest(rec),
                 reviewed_fields=[f for f in ['text','pros','cons'] if rec[f].strip()],
                 no_theme_reason='Synthetic: no additional thematic observation', observations=[])
            for rec in batch['records']])

    def test_ready_data_is_not_ready_ui(self):
        s=p.check(self.folder)
        for k in ('collection','processing','evidence','report'):
            self.assertEqual('PASS',s['stages'][k]['status'])
        self.assertFalse(s['success'])
        with self.assertRaises(ValueError):p.finish(self.folder)
        self.assertFalse(self.get('manifest')['execution']['success'])

    def test_all_checks_allow_success(self):
        p.attest_ui(self.folder,self.receipt())
        self.assertTrue(p.finish(self.folder)['success'])
        self.assertEqual('complete',self.get('manifest')['research_status'])

    def test_early_finish_with_consistent_partial_analysis(self):
        s=self.partial()
        self.assertEqual(['R-SYN-2'],s['pending_IDs'])
        self.assertEqual('PASS',s['stages']['collection']['status'])
        with self.assertRaises(ValueError):p.finish(self.folder)
        with self.assertRaises(ValueError):build(self.folder)
        with self.assertRaises(ValueError):build(self.folder,allow_partial_source=True)

    def test_forged_complete_flag_is_recomputed(self):
        self.partial();m=self.get('manifest')
        m.update(analysis_status='complete',pending_IDs=[],research_status='complete')
        m['execution']['success']=True
        self.put('manifest',m)
        s=p.check(self.folder)
        self.assertFalse(s['success'])
        self.assertEqual(['R-SYN-2'],self.get('manifest')['pending_IDs'])

    def test_partial_collection_can_be_fully_processed(self):
        m=self.get('manifest');coverage=m['source_coverage']
        coverage['group']['expected_returned']=5
        coverage['group']['expected_text']=4
        s=p.set_coverage(self.folder,coverage)
        self.assertEqual('BLOCKED',s['stages']['collection']['status'])
        self.assertEqual('PASS',s['stages']['processing']['status'])
        self.assertEqual('PASS',s['stages']['evidence']['status'])
        with self.assertRaises(ValueError):build(self.folder)
        data=build(self.folder,True)
        self.assertFalse(data['readiness']['source_complete'])
        self.assertFalse(data['readiness']['research_success'])
        with self.assertRaises(ValueError):p.finish(self.folder)

    def test_unknown_source_counts_cannot_prove_coverage(self):
        coverage=self.get('manifest')['source_coverage']
        coverage['group']['expected_returned']=None
        coverage['group']['expected_text']=None
        self.assertEqual('BLOCKED',p.set_coverage(self.folder,coverage)['stages']['collection']['status'])

    def test_matching_counts_without_exhaustion_not_complete(self):
        coverage=self.get('manifest')['source_coverage'];coverage['group']['exhausted']=False
        self.assertEqual('BLOCKED',p.set_coverage(self.folder,coverage)['stages']['collection']['status'])

    def test_missing_scope_cannot_drop_group(self):
        a=self.get('analysis');del a['REPORT_DATA']['areas']['group'];self.put('analysis',a)
        self.assertNotEqual('PASS',p.check(self.folder)['stages']['report']['status'])

    def test_mismatched_group_identity_blocks_report(self):
        m=self.get('manifest');m['source_coverage']['group']['product_ids']=['9001'];self.put('manifest',m)
        self.assertNotEqual('PASS',p.check(self.folder)['stages']['report']['status'])

    def test_empty_source_receipt_not_complete(self):
        p.set_coverage(self.folder,{})
        with self.assertRaises(ValueError):build(self.folder)
        with self.assertRaises(ValueError):p.finish(self.folder)

    def test_partial_theme_is_not_verified(self):
        a=self.get('analysis');t=a['REPORT_DATA']['areas']['group']['themes'][1]
        t.update(quote_ids=[],evidence_status='partial',evidence_reason='synthetic missing proof')
        self.put('analysis',a)
        self.assertNotEqual('PASS',p.check(self.folder)['stages']['evidence']['status'])
        with self.assertRaises(ValueError):build(self.folder)

    def test_equal_count_wrong_id_set_is_rejected(self):
        a=self.get('analysis');a['REPORT_DATA']['areas']['group']['themes'][0]['quote_ids']=['Q-SYN-2']
        self.put('analysis',a)
        self.assertEqual('FAIL',p.check(self.folder)['stages']['evidence']['status'])

    def test_missing_quote_full_field_and_provenance_rejected(self):
        for field,value in [('full_field_text','changed'),('date','wrong'),('product_id','999')]:
            with self.subTest(field=field):
                a=load_json(FIX/'analysis.json');a['REPORT_DATA']['areas']['group']['quotes'][0][field]=value
                self.put('analysis',a)
                self.assertEqual('FAIL',p.check(self.folder)['stages']['evidence']['status'])

    def test_resume_only_unprocessed_review_ids(self):
        self.partial()
        batch=p.next_batch(self.folder,1)
        self.assertEqual(['R-SYN-2'],batch['review_ids'])
        # A new invocation issues the same unfinished IDs, not a new download.
        self.assertEqual(batch,p.next_batch(self.folder,1))
        before=(self.folder/'corpus.jsonl').read_bytes()
        p.commit_batch(self.folder,self.payload(batch))
        self.assertEqual([],p.next_batch(self.folder,1)['review_ids'])
        self.assertEqual(before,(self.folder/'corpus.jsonl').read_bytes())

    def test_batch_commit_is_all_or_nothing(self):
        self.partial();batch=p.next_batch(self.folder)
        before=(self.folder/'analysis.json').read_bytes()
        payload=self.payload(batch);payload['reviews']=[]
        with self.assertRaises(ValueError):p.commit_batch(self.folder,payload)
        self.assertEqual(before,(self.folder/'analysis.json').read_bytes())
        self.assertEqual(batch['review_ids'],p.check(self.folder)['pending_IDs'])

    def test_batch_rejects_stale_token(self):
        self.partial();batch=p.next_batch(self.folder);payload=self.payload(batch);payload['batch_id']='wrong'
        with self.assertRaises(ValueError):p.commit_batch(self.folder,payload)

    def test_crash_after_analysis_write_recovers_without_reprocessing(self):
        self.partial();batch=p.next_batch(self.folder)
        real_atomic=p.atomic_json
        def crash_manifest(path,obj):
            if Path(path).name=='manifest.json' and 'updated_at' in obj.get('execution',{}) and obj['execution'].get('stages',{}).get('processing',{}).get('status')=='PASS':
                raise OSError('synthetic crash after analysis commit')
            return real_atomic(path,obj)
        with patch.object(p,'atomic_json',side_effect=crash_manifest):
            with self.assertRaises(OSError):p.commit_batch(self.folder,self.payload(batch))
        self.assertFalse(self.get('manifest')['execution']['success'])
        self.assertEqual([],p.check(self.folder)['pending_IDs'])
        self.assertEqual([],p.next_batch(self.folder)['review_ids'])

    def test_crash_before_analysis_write_keeps_pending(self):
        self.partial();batch=p.next_batch(self.folder)
        real_atomic=p.atomic_json
        def fail_analysis(path,obj):
            if Path(path).name=='analysis.json':raise OSError('synthetic disk failure')
            return real_atomic(path,obj)
        with patch.object(p,'atomic_json',side_effect=fail_analysis):
            with self.assertRaises(OSError):p.commit_batch(self.folder,self.payload(batch))
        self.assertEqual(['R-SYN-2'],p.check(self.folder)['pending_IDs'])

    def test_no_theme_review_requires_explicit_reason(self):
        self.partial();batch=p.next_batch(self.folder);payload=self.payload(batch)
        del payload['reviews'][0]['no_theme_reason']
        with self.assertRaises(ValueError):p.commit_batch(self.folder,payload)

    def test_all_nonempty_fields_must_be_read(self):
        rec=dict(review_id='R-SYN-4',product_id='9001',text='x',pros='y',cons='z')
        p.ingest(self.folder,[rec]);batch=p.next_batch(self.folder);payload=self.payload(batch)
        payload['reviews'][0]['reviewed_fields']=['text']
        with self.assertRaises(ValueError):p.commit_batch(self.folder,payload)
        self.assertIn('R-SYN-4',p.check(self.folder)['pending_IDs'])

    def test_changed_source_invalidates_analyzed_stamp(self):
        cp=self.folder/'corpus.jsonl';c=load_corpus(cp);c[0]['pros']='new unreviewed field'
        cp.write_text(''.join(json.dumps(r)+'\n' for r in c))
        s=p.check(self.folder)
        self.assertEqual('FAIL',s['stages']['processing']['status'])
        self.assertIn('R-SYN-1',s['pending_IDs'])

    def test_ingest_deduplicates_and_rejects_conflicts(self):
        c=load_corpus(self.folder/'corpus.jsonl');before=(self.folder/'corpus.jsonl').read_bytes()
        p.ingest(self.folder,c)
        self.assertEqual(before,(self.folder/'corpus.jsonl').read_bytes())
        c[0]['text']='conflicting snapshot'
        with self.assertRaises(ValueError):p.ingest(self.folder,c)
        self.assertEqual(before,(self.folder/'corpus.jsonl').read_bytes())

    def test_ingestion_invalidation_precedes_corpus_write(self):
        p.attest_ui(self.folder,self.receipt());self.assertTrue(p.finish(self.folder)['success'])
        real_write=p.atomic_text
        def inspected_write(path,data):
            self.assertFalse(self.get('manifest')['execution']['success'])
            return real_write(path,data)
        with patch.object(p,'atomic_text',side_effect=inspected_write):
            p.ingest(self.folder,[dict(review_id='NEW',product_id='9001',text='new',pros='',cons='')])
        self.assertFalse(p.check(self.folder)['success'])

    def test_ui_edit_does_not_recollect_or_reanalyze(self):
        before={f:(self.folder/f).read_bytes() for f in ['corpus.jsonl','analysis.json']}
        p.attest_ui(self.folder,self.receipt())
        r=self.receipt();r['mechanism']='changed renderer';p.attest_ui(self.folder,r)
        self.assertTrue(p.finish(self.folder)['success'])
        for f,value in before.items():self.assertEqual(value,(self.folder/f).read_bytes())

    def test_ui_receipt_invalidated_by_data_change(self):
        p.attest_ui(self.folder,self.receipt());self.assertTrue(p.finish(self.folder)['success'])
        a=self.get('analysis');a['REPORT_DATA']['areas']['article']['summary']='new summary';self.put('analysis',a)
        self.assertEqual('FAIL',p.check(self.folder)['stages']['ui']['status'])
        with self.assertRaises(ValueError):p.finish(self.folder)

    def test_ui_cannot_drop_themes_or_quotes(self):
        for field in ['theme_ids','quote_ids']:
            with self.subTest(field=field):
                r=self.receipt();r['inventory']['group'][field]=[]
                self.assertEqual('FAIL',p.attest_ui(self.folder,r)['stages']['ui']['status'])

    def test_ui_requires_every_interaction_check(self):
        r=self.receipt();del r['checks']['hidden_quote_search']
        self.assertEqual('FAIL',p.attest_ui(self.folder,r)['stages']['ui']['status'])

    def test_unavailable_ui_does_not_block_semantic_processing(self):
        s=p.check(self.folder)
        r=dict(input_sha256=s['input_sha256'],available=False,reason='synthetic host without native UI')
        s=p.attest_ui(self.folder,r)
        self.assertEqual('BLOCKED',s['stages']['ui']['status'])
        self.assertEqual('PASS',s['stages']['processing']['status'])
        self.assertEqual('PASS',s['stages']['evidence']['status'])
        with self.assertRaises(ValueError):p.finish(self.folder)

    def test_missing_jsonschema_fails_closed(self):
        import builtins
        orig=builtins.__import__
        def missing(name,*args,**kwargs):
            if name=='jsonschema':raise ImportError('synthetic unavailable dependency')
            return orig(name,*args,**kwargs)
        from common import schema_validator
        schema_validator.cache_clear()
        try:
            with patch('builtins.__import__',side_effect=missing):
                with self.assertRaises(ValueError):p.check(self.folder)
        finally:schema_validator.cache_clear()
        self.assertFalse(self.get('manifest')['execution']['success'])

    def test_malformed_data_cannot_leave_saved_success(self):
        p.attest_ui(self.folder,self.receipt());p.finish(self.folder)
        self.put('analysis',{'review_status':'invalid','observations':[]})
        with self.assertRaises(ValueError):p.check(self.folder)
        self.assertFalse(self.get('manifest')['execution']['success'])

    def test_explicit_failure_requires_retry(self):
        p.attest_ui(self.folder,self.receipt())
        self.assertFalse(p.set_error(self.folder,'evidence','synthetic interrupted verification',True)['success'])
        self.assertTrue(p.set_error(self.folder,'evidence')['success'])

    def test_legacy_import_is_not_auto_certified(self):
        a=self.get('analysis')
        for s in a['review_status']:s.pop('record_sha256');s.pop('reviewed_fields')
        self.put('analysis',a);m=self.get('manifest');m.pop('execution');m['source_coverage']={'status':'old'};self.put('manifest',m)
        s=p.initialize(self.folder)
        self.assertEqual(['R-SYN-1','R-SYN-2'],s['pending_IDs'])
        self.assertFalse(s['success'])
        self.assertTrue(self.get('analysis')['legacy_observations'])

    def test_cli_check_and_finish_exit_nonzero_when_incomplete(self):
        self.partial()
        for command in ('check','finish'):
            r=subprocess.run([sys.executable,str(ROOT/'scripts/pipeline.py'),str(self.folder),command],capture_output=True,text=True)
            self.assertEqual(1,r.returncode)
        r=subprocess.run([sys.executable,str(ROOT/'scripts/pipeline.py'),str(self.folder),'status'],capture_output=True,text=True)
        self.assertEqual(0,r.returncode);self.assertFalse(json.loads(r.stdout)['success'])

    def test_new_run_can_start_with_only_three_files(self):
        with tempfile.TemporaryDirectory() as d:
            s=p.initialize(d,'SYNTHETIC-NEW')
            self.assertFalse(s['success'])
            self.assertEqual({'analysis.json','manifest.json','corpus.jsonl'},{f.name for f in Path(d).iterdir()})
            p.ingest(d,[dict(review_id='A',product_id='9001',text='test',pros='',cons='')])
            coverage=load_json(FIX/'manifest.json')['source_coverage']
            p.set_coverage(d,coverage)
            self.assertEqual(['A'],p.next_batch(d)['review_ids'])

    def test_complete_new_run_with_interruption_and_full_report(self):
        original=load_json(FIX/'analysis.json');m=load_json(FIX/'manifest.json')
        for f in self.folder.iterdir():f.unlink()
        p.initialize(self.folder,'RUN-SYN')
        records=load_corpus(FIX/'corpus.jsonl')
        p.ingest(self.folder,records[:2])
        p.set_coverage(self.folder,m['source_coverage'])
        self.assertEqual('BLOCKED',p.check(self.folder)['stages']['collection']['status'])
        batch=p.next_batch(self.folder,1)
        payload=self.payload(batch)
        for r in payload['reviews']:
            r['observations']=[o for o in original['observations'] if o['review_id']==r['review_id']]
        p.commit_batch(self.folder,payload)
        # Resume in a separate process, with exactly the remaining review.
        completed=subprocess.run([sys.executable,str(ROOT/'scripts/pipeline.py'),str(self.folder),'next-batch','--size','1'],capture_output=True,text=True)
        self.assertEqual(0,completed.returncode)
        batch=json.loads(completed.stdout)
        self.assertEqual(['R-SYN-2'],batch['review_ids'])
        payload=self.payload(batch)
        for r in payload['reviews']:
            r['observations']=[o for o in original['observations'] if o['review_id']==r['review_id']]
        p.commit_batch(self.folder,payload)
        p.ingest(self.folder,records[2:])
        self.assertEqual([],p.check(self.folder)['pending_IDs'])
        p.set_report(self.folder,original['REPORT_DATA'])
        self.assertEqual('PASS',p.check(self.folder)['stages']['evidence']['status'])
        self.assertEqual(original['REPORT_DATA']['areas'],build(self.folder)['areas'])
        p.attest_ui(self.folder,self.receipt())
        self.assertTrue(p.finish(self.folder)['success'])
        self.assertEqual({'analysis.json','manifest.json','corpus.jsonl'},{f.name for f in self.folder.iterdir()})

    def test_batch_scope_change_rejects_commit(self):
        self.partial();batch=p.next_batch(self.folder)
        m=self.get('manifest');m['source_coverage']['group']['product_ids'].append('9003')
        p.set_coverage(self.folder,m['source_coverage'])
        with self.assertRaises(ValueError):p.commit_batch(self.folder,self.payload(batch))
        self.assertFalse(p.check(self.folder)['success'])

    def test_source_scope_change_invalidates_review_mapping(self):
        m=self.get('manifest');m['source_coverage']['group']['product_ids'].append('9003')
        s=p.set_coverage(self.folder,m['source_coverage'])
        self.assertEqual('FAIL',s['stages']['processing']['status'])
        self.assertEqual(['R-SYN-1','R-SYN-2'],s['pending_IDs'])
        batch=p.next_batch(self.folder)
        self.assertEqual(s['pending_IDs'],batch['review_ids'])
        self.assertEqual('PASS',p.commit_batch(self.folder,self.payload(batch))['stages']['processing']['status'])

    def test_counts_change_preserves_mapping_but_invalidates_ui(self):
        p.attest_ui(self.folder,self.receipt());p.finish(self.folder)
        c=self.get('manifest')['source_coverage'];c['group']['expected_text']=3
        s=p.set_coverage(self.folder,c)
        self.assertEqual('PASS',s['stages']['processing']['status'])
        self.assertEqual('FAIL',s['stages']['ui']['status'])
        self.assertFalse(s['success'])

    def test_observation_excerpt_cannot_be_lost_in_export(self):
        a=self.get('analysis');r=load_corpus(self.folder/'corpus.jsonl')[0]
        a['observations'][0]['source_field']='pros';a['observations'][0]['excerpt']='not in field'
        self.put('analysis',a)
        self.assertEqual('FAIL',p.check(self.folder)['stages']['evidence']['status'])

    def test_batch_observation_needs_exact_excerpt(self):
        self.partial();batch=p.next_batch(self.folder);payload=self.payload(batch)
        payload['reviews'][0]['observations']=[dict(area='group',theme_id='test',context='neutral',source_field='text',excerpt='invented quote')]
        with self.assertRaises(ValueError):p.commit_batch(self.folder,payload)
        self.assertEqual(['R-SYN-2'],p.check(self.folder)['pending_IDs'])

    def test_report_cannot_be_saved_before_all_reviews_processed(self):
        self.partial()
        with self.assertRaises(ValueError):p.set_report(self.folder,load_json(FIX/'analysis.json')['REPORT_DATA'])

    def test_ui_not_applicable_checks_only_when_data_absent(self):
        r=self.receipt()
        for k in ['pagination','question_search']:
            r['checks'][k]=dict(status='N/A',evidence='synthetic fixture contains no applicable records')
        self.assertEqual('PASS',p.attest_ui(self.folder,r)['stages']['ui']['status'])
        r=self.receipt();r['checks']['hidden_quote_search']=dict(status='N/A',evidence='cannot check')
        self.assertEqual('FAIL',p.attest_ui(self.folder,r)['stages']['ui']['status'])

    def test_invalid_ui_receipt_does_not_poison_manifest(self):
        r=self.receipt();r['checks']='malformed'
        with self.assertRaises(ValueError):p.attest_ui(self.folder,r)
        self.assertNotIn('ui_receipt',self.get('manifest')['execution'])
        self.assertFalse(p.check(self.folder)['success'])

    def test_export_cannot_write_inside_research_subfolder(self):
        target=self.folder/'nested';target.mkdir()
        command=[sys.executable,str(ROOT/'scripts/build_report_data.py'),str(self.folder),'--out',str(target/'output.json')]
        r=subprocess.run(command,capture_output=True,text=True)
        self.assertEqual(1,r.returncode);self.assertFalse((target/'output.json').exists())

    def test_requested_areas_cannot_be_silently_reduced(self):
        with self.assertRaises(ValueError):p.initialize(self.folder,requested_areas=['article'])

    def test_large_batch_resume_keeps_all_pending_ids(self):
        new=[dict(review_id='SYN-'+str(i),product_id='9001',text='Synthetic '+str(i),pros='',cons='') for i in range(123)]
        p.ingest(self.folder,new)
        seen=set()
        while True:
            batch=p.next_batch(self.folder,50)
            if not batch['review_ids']:break
            self.assertFalse(seen.intersection(batch['review_ids']))
            seen.update(batch['review_ids'])
            p.commit_batch(self.folder,self.payload(batch))
        self.assertEqual({r['review_id'] for r in new},seen)
        self.assertEqual([],p.check(self.folder)['pending_IDs'])
        self.assertFalse(p.check(self.folder)['success'])

    def test_two_process_ingest_has_no_lost_updates(self):
        writers=[]
        for group in range(2):
            records=[dict(review_id=f'WRITER-{group}-{i}',product_id='9001',text='synthetic concurrent write',pros='',cons='') for i in range(10)]
            proc=subprocess.Popen([sys.executable,str(ROOT/'scripts/pipeline.py'),str(self.folder),'ingest','--input','-'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            proc.stdin.write(json.dumps(records));proc.stdin.close();writers.append(proc)
        for proc in writers:
            proc.wait(timeout=20)
            self.assertEqual(0,proc.returncode,proc.stderr.read())
            proc.stdout.close();proc.stderr.close()
        c=load_corpus(self.folder/'corpus.jsonl')
        self.assertEqual(23,len(c));self.assertEqual(23,len({r['review_id'] for r in c}))
        self.assertEqual(20,len(p.check(self.folder)['pending_IDs']))


if __name__=='__main__':unittest.main()

