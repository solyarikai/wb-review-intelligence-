"""Check the portable marketplace package without network or personal credentials."""
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT/'plugins/wb-review-intelligence'

def verify():
    manifest=json.loads((PLUGIN/'plugin.json').read_text())
    legacy=json.loads((PLUGIN/'.codex-plugin/plugin.json').read_text())
    market=json.loads((ROOT/'.agents/plugins/marketplace.json').read_text())
    assert manifest['$schema']=='https://agent-plugins.org/schemas/1.0.0/plugin.schema.json'
    assert manifest['name']=='wb-review-intelligence'
    assert manifest['version']==legacy['version']=='1.3.0'
    assert 'apps' not in manifest.get('extensions',{}).get('com.openai',{})
    assert 'apps' not in legacy and not (PLUGIN/'.app.json').exists()
    entries=market['plugins'];assert len(entries)==1
    target=(ROOT/entries[0]['source']['path']).resolve()
    assert target==PLUGIN.resolve() and target.is_relative_to(ROOT.resolve())
    assert entries[0]['name']==manifest['name']
    assert entries[0]['policy']['installation']=='AVAILABLE'
    skills=sorted((PLUGIN/'skills').glob('*/SKILL.md'))
    assert len(skills)==2
    for skill in skills:
        txt=skill.read_text();assert txt.startswith('---\n')
        head=txt.split('---',2)[1]
        assert re.search(r'^name: \S+',head,re.M) and re.search(r'^description: \S+',head,re.M)
    forbidden=[r'/home/denis',r'(?i)iovenko',r'781ee041',r'(?i)siwc_bypass',r'(?i)account_email',
               r'-----BEGIN (?:RSA |OPENSSH )?PRIVATE KEY-----',r'gh[pousr]_[A-Za-z0-9]{30,}',r'github_pat_[A-Za-z0-9_]{30,}']
    digests={}
    for file in PLUGIN.rglob('*'):
        assert not file.is_symlink(),str(file)
        if not file.is_file() or '__pycache__' in file.parts:continue
        assert file.name not in ('.env','storage-target.json','publish_report.py','.app.json')
        text=file.read_text()
        assert not any(re.search(pattern,text) for pattern in forbidden),str(file)+' contains private configuration'
        assert not any(x in file.parts for x in ['runs','outputs','backups'])
        digests[str(file.relative_to(ROOT))]=hashlib.sha256(file.read_bytes()).hexdigest()
    for name in ['pipeline.py','local_project.py','validate_corpus.py','validate_evidence.py','build_report_data.py','render_report.py']:
        assert (PLUGIN/'skills/analyze-wb-reviews/scripts'/name).is_file()
    print(json.dumps({'status':'PASS','version':manifest['version'],'skills':len(skills),'package_files':len(digests)},ensure_ascii=False))
    return digests

if __name__=='__main__':verify()
