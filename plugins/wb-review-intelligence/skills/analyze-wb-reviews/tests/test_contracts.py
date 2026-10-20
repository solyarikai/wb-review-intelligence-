import copy,json,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from validate_corpus import validate as corpus_check
from validate_evidence import validate as evidence_check
from build_report_data import build
from common import load_json
FIX=Path(__file__).parent/'fixtures/valid'

class ContractTests(unittest.TestCase):
    def test_fixture_valid(self):
        self.assertEqual([],corpus_check(FIX));self.assertEqual([],evidence_check(FIX))
        import tempfile,shutil
        with tempfile.TemporaryDirectory() as d:
            for p in FIX.iterdir():shutil.copy(p,Path(d)/p.name)
            self.assertEqual(['article','group'],list(build(d)['areas']))
    def test_bad_quote_detected(self):
        import tempfile,shutil
        with tempfile.TemporaryDirectory() as d:
            for p in FIX.iterdir():shutil.copy(p,Path(d)/p.name)
            p=Path(d)/'analysis.json';a=load_json(p)
            a['REPORT_DATA']['areas']['article']['quotes'][0]['text']='выдуманный текст'
            p.write_text(json.dumps(a,ensure_ascii=False))
            self.assertTrue(any('exact substring' in e for e in evidence_check(d)))
    def test_wrong_frequency_detected(self):
        import tempfile,shutil
        with tempfile.TemporaryDirectory() as d:
            for p in FIX.iterdir():shutil.copy(p,Path(d)/p.name)
            p=Path(d)/'analysis.json';a=load_json(p)
            a['REPORT_DATA']['areas']['article']['themes'][0]['count']=2
            p.write_text(json.dumps(a,ensure_ascii=False))
            self.assertTrue(any('observation unique IDs' in e for e in evidence_check(d)))
    def test_missing_evidence_detected(self):
        import tempfile,shutil
        with tempfile.TemporaryDirectory() as d:
            for p in FIX.iterdir():shutil.copy(p,Path(d)/p.name)
            p=Path(d)/'analysis.json';a=load_json(p)
            a['REPORT_DATA']['areas']['group']['themes'][1]['quote_ids']=[]
            p.write_text(json.dumps(a,ensure_ascii=False))
            self.assertTrue(any('marked complete' in e for e in evidence_check(d)))
    def test_pending_mismatch_detected(self):
        import tempfile,shutil
        with tempfile.TemporaryDirectory() as d:
            for p in FIX.iterdir():shutil.copy(p,Path(d)/p.name)
            p=Path(d)/'manifest.json';a=load_json(p);a['pending_IDs']=['R-SYN-2'];p.write_text(json.dumps(a))
            self.assertTrue(any('pending_IDs differs' in e for e in evidence_check(d)))

if __name__=='__main__':unittest.main()
