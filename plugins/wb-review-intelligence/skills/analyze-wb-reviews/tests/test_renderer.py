"""Renderer regressions. Synthetic inputs are never real buyer evidence."""
from copy import deepcopy
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from build_report_data import build
from render_report import fragment, pack, unpack, render, renderer_sha256
import pipeline


class RendererTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)/'research'
        shutil.copytree(Path(__file__).parent/'fixtures/valid', self.folder)

    def test_packing_preserves_unicode_signed_values_and_marker_keys(self):
        original = {'negative':-3.25, 'bool':True, '!':-5, 'list':['текст', None, 0, -2], '__proto__':{'n':-1}}
        self.assertEqual(original, unpack(pack(original)))

    def test_deterministic_all_evidence_and_static_fallback(self):
        data = build(self.folder)
        first, meta = fragment(data)
        second, other = fragment(data)
        self.assertEqual(first, second)
        self.assertEqual(meta, other)
        self.assertFalse(meta['ui_verified'])
        self.assertIn('Что важно для решения', first.split('<script>')[0])
        payload = json.loads(re.search(r'const payload = (.*);', first).group(1))
        decoded = unpack(payload)
        self.assertEqual(data['areas'], decoded['areas'])
        for area, item in data['areas'].items():
            for key, field in [('theme_ids','themes'),('quote_ids','quotes'),('question_ids','questions'),('opportunity_ids','opportunities')]:
                self.assertEqual([x['id'] for x in item[field]], meta['inventory'][area][key])

    def test_untrusted_text_does_not_break_script_or_substitute_placeholders(self):
        data = build(self.folder)
        marker = '</script><img src=x onerror=alert(1)>__REPORT_PAYLOAD__&\u2028'
        data['areas']['article']['summary'] = marker
        html, _ = fragment(data)
        self.assertEqual(1, html.count('<script>'))
        self.assertEqual(1, html.count('</script>'))
        self.assertNotIn('<img', html)
        self.assertIn('__REPORT_PAYLOAD__', html.split('<script>')[0])

    def test_incomplete_analysis_is_not_rendered(self):
        analysis = self.folder/'analysis.json'
        data = json.loads(analysis.read_text())
        data['review_status'][0]['status'] = 'pending'
        analysis.write_text(json.dumps(data))
        target = Path(self.temp.name)/'blocked.html'
        with self.assertRaises(ValueError): render(self.folder, target, True)
        self.assertFalse(target.exists())

    def test_size_limit_never_drops_quotes_or_overwrites_previous_output(self):
        target = Path(self.temp.name)/'kept.html'
        target.write_text('previous')
        with patch('render_report.MAX_BYTES', 50):
            with self.assertRaisesRegex(ValueError, 'no evidence was dropped'):
                render(self.folder, target)
        self.assertEqual('previous', target.read_text())

    def test_cannot_add_fourth_file_to_research(self):
        with self.assertRaises(ValueError): render(self.folder, self.folder/'report.html')

    def test_ui_receipt_for_another_renderer_fails(self):
        state = pipeline.check(self.folder)
        analysis = json.loads((self.folder/'analysis.json').read_text())
        manifest = json.loads((self.folder/'manifest.json').read_text())
        manifest['execution']['ui_receipt'] = {
            'input_sha256':state['input_sha256'], 'available':True,
            'mechanism':'synthetic validation adapter', 'checked_at':'2026-10-17',
            'renderer_sha256':'0'*64, 'checks':{}, 'inventory':{}
        }
        result = pipeline.ui_result(analysis, manifest, state['input_sha256'])
        self.assertEqual('FAIL', result['status'])
        self.assertTrue(any('pinned renderer' in text for text in result['reasons']))

    def test_unverified_or_old_ui_pass_is_never_inherited_by_new_fragment(self):
        data = build(self.folder)
        data['readiness']['ui_status'] = 'PASS'
        data['readiness']['research_success'] = True
        html, _ = fragment(data)
        payload = json.loads(re.search(r'const payload = (.*);', html).group(1))
        decoded = unpack(payload)
        self.assertEqual('NOT_VERIFIED', decoded['readiness']['ui_status'])
        self.assertFalse(decoded['readiness']['research_success'])


if __name__ == '__main__': unittest.main()
