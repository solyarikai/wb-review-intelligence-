"""Synthetic project persistence tests; these are not marketplace reviews."""
from copy import deepcopy
import json
import multiprocessing
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import local_project as local
import pipeline
from render_report import fragment, render_local
from build_report_data import build


def init_worker(project, run):
    local.initialize(project, run_id=run)


class LocalProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)

    def fixture(self):
        local.initialize(self.project, run_id='RUN-SYN')
        folder = self.project/local.DIRECTORY/'runs/RUN-SYN/data'
        for name in local.FILES:
            shutil.copyfile(ROOT/'tests/fixtures/valid'/name, folder/name)
        return folder

    def test_init_resume_and_same_project_registry(self):
        first = local.initialize(self.project, article='123', run_id='same')
        before = (Path(first['data_folder'])/'corpus.jsonl')
        before.write_text('')  # Initial empty corpus is legitimate.
        second = local.initialize(self.project, article='123', run_id='same')
        self.assertEqual(first['data_folder'], second['data_folder'])
        self.assertEqual(1, len(local.list_runs(self.project)))
        self.assertFalse(local.list_runs(self.project)[0]['research_success'])
        self.assertEqual(set(local.FILES), {p.name for p in before.parent.iterdir()})
        self.assertFalse(json.loads((self.project/local.DIRECTORY/'config.json').read_text())['cloud_uploads'])

    def test_offline_render_and_full_integrity(self):
        folder = self.fixture()
        out = local.checkpoint(self.project, 'RUN-SYN', render=True)
        self.assertTrue(out['entry']['report_current'])
        self.assertFalse(out['entry']['research_success'])  # Native UI not attested.
        self.assertTrue(Path(out['message_html']).is_file())
        self.assertEqual('PREPARED_NOT_VERIFIED', out['inline_ui_status'])
        self.assertEqual({'path': out['message_html']}, json.loads(out['inline_reference'].split('\ue202')[1][:-1]))
        html = Path(out['report_html']).read_text()
        self.assertTrue(html.startswith('<!doctype html>'))
        self.assertIn('charset="utf-8"', html)
        for name, expected in out['entry']['files'].items():
            self.assertEqual(local.file_digest(folder/name), expected)
        self.assertIn('runs/RUN-SYN/report.html', (self.project/local.DIRECTORY/'index.html').read_text())

    def test_stale_report_is_not_offered_as_current(self):
        folder = self.fixture()
        local.checkpoint(self.project, 'RUN-SYN', render=True)
        a = json.loads((folder/'analysis.json').read_text())
        a['REPORT_DATA']['areas']['article']['summary'] += ' updated'
        (folder/'analysis.json').write_text(json.dumps(a))
        result = local.checkpoint(self.project, 'RUN-SYN')
        self.assertFalse(result['entry']['report_current'])
        self.assertNotIn('<a href=', (self.project/local.DIRECTORY/'index.html').read_text())

    def test_incomplete_render_keeps_data_and_previous_html(self):
        folder = self.fixture()
        result = local.checkpoint(self.project, 'RUN-SYN', render=True)
        html = Path(result['report_html']);before = html.read_bytes()
        a = json.loads((folder/'analysis.json').read_text())
        a['review_status'][0]['status'] = 'pending'
        (folder/'analysis.json').write_text(json.dumps(a))
        result = local.checkpoint(self.project, 'RUN-SYN', render=True)
        self.assertIn('render_blocked', result)
        self.assertEqual(before, html.read_bytes())
        self.assertFalse(result['entry']['report_current'])
        self.assertTrue(result['entry']['storage_verified'])

    def test_large_standalone_does_not_use_chat_size_limit(self):
        folder = self.fixture()
        with patch('render_report.MAX_BYTES', 10):
            with self.assertRaises(ValueError):
                fragment(build(folder))
            out = local.checkpoint(self.project, 'RUN-SYN', render=True)
        self.assertTrue(out['entry']['report_current'])
        self.assertTrue(Path(out['report_html']).is_file())
        self.assertIn('inline_blocked', out)
        self.assertNotIn('inline_reference', out)

    def test_symlink_and_path_traversal_rejected(self):
        with self.assertRaises(ValueError):
            local.initialize(self.project, run_id='../escape')
        outside = self.project/'outside';outside.mkdir()
        root = self.project/local.DIRECTORY
        if root.exists():shutil.rmtree(root)
        root.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            local.initialize(self.project, run_id='escape')
        self.assertEqual([], list(outside.iterdir()))

    def test_parallel_runs_do_not_lose_registry_entries(self):
        workers = [multiprocessing.Process(target=init_worker, args=(self.project, 'r'+str(i))) for i in range(4)]
        for worker in workers:worker.start()
        for worker in workers:
            worker.join(15)
            if worker.is_alive():worker.terminate();worker.join();self.fail('worker deadlocked')
            self.assertEqual(0, worker.exitcode)
        self.assertEqual({'r0','r1','r2','r3'}, {e['run_id'] for e in local.list_runs(self.project)})

    def test_registry_corruption_is_not_silently_overwritten(self):
        local.initialize(self.project, run_id='a')
        registry = self.project/local.DIRECTORY/'registry.jsonl'
        registry.write_text('{corrupt')
        with self.assertRaises(ValueError):
            local.checkpoint(self.project, 'a')
        self.assertEqual('{corrupt', registry.read_text())


if __name__ == '__main__':
    unittest.main()

class LegacyHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        local.initialize(self.project, run_id='current')
        self.root = self.project/local.DIRECTORY
        self.old = self.project/'old/research'
        self.old.mkdir(parents=True)
        (self.old/'corpus.jsonl').write_text('unchanged source')
        (self.old/'report.html').write_text('<p>previous report</p>')

    def catalog(self, alias=None):
        item = dict(run_id='previous',title='Earlier study',format='legacy-skill',
                    source_path='old/research',report_path='old/research/report.html')
        if alias is not None:item['alias_of']=alias
        (self.root/'legacy-index.json').write_text(json.dumps(dict(version=1,entries=[item])))

    def test_old_study_is_linked_without_copy_or_mutation(self):
        self.catalog()
        before = {p.name:p.read_bytes() for p in self.old.iterdir()}
        local.checkpoint(self.project,'current')
        rows = local.list_runs(self.project)
        self.assertEqual({'current','previous'}, {x['run_id'] for x in rows})
        self.assertTrue(next(x for x in rows if x['run_id']=='previous')['legacy'])
        self.assertIn('../old/research/report.html',(self.root/'index.html').read_text())
        self.assertEqual(before, {p.name:p.read_bytes() for p in self.old.iterdir()})
        self.assertFalse((self.root/'runs/previous').exists())
        self.assertFalse((self.project/'outputs/skill-runs').exists())
        self.assertEqual({'current'},set(local.registry(self.root)))

    def test_alias_is_one_study_and_survives_a_new_run(self):
        self.catalog('current')
        local.initialize(self.project, run_id='new')
        rows=local.list_runs(self.project)
        self.assertEqual({'current','new'},{x['run_id'] for x in rows})
        self.assertEqual('previous',next(x for x in rows if x['run_id']=='current')['legacy_aliases'][0]['run_id'])
        self.assertIn('Сохранённая совместимая копия',(self.root/'index.html').read_text())
        self.assertNotIn('Ранее сохранённые исследования',(self.root/'index.html').read_text())

    def test_invalid_reference_does_not_overwrite_index(self):
        self.catalog()
        path=self.root/'legacy-index.json'
        obj=json.loads(path.read_text())
        for value in ['../outside','/etc','old/../research','old//research','old\\research']:
            obj['entries'][0]['source_path']=value
            path.write_text(json.dumps(obj))
            before=(self.root/'registry.jsonl').read_bytes()
            study_before={p.name:p.read_bytes() for p in (self.root/'runs/current/data').iterdir()}
            html=(self.root/'index.html').read_bytes()
            with self.assertRaises(ValueError):local.checkpoint(self.project,'current')
            self.assertEqual(before,(self.root/'registry.jsonl').read_bytes())
            self.assertEqual(html,(self.root/'index.html').read_bytes())
            self.assertEqual(study_before,{p.name:p.read_bytes() for p in (self.root/'runs/current/data').iterdir()})

    def test_missing_source_alias_target_and_symlink_fail_closed(self):
        self.catalog('absent')
        with self.assertRaises(ValueError):local.list_runs(self.project)
        self.catalog()
        (self.old/'report.html').unlink()
        with self.assertRaises(ValueError):local.list_runs(self.project)
        (self.old/'report.html').symlink_to(self.project/'missing')
        with self.assertRaises(ValueError):local.list_runs(self.project)
