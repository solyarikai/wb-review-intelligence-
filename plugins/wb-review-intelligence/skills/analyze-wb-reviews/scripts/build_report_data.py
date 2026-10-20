"""Export only fully processed, evidenced data; never equate export with success."""
import argparse
from copy import deepcopy
import sys
from pathlib import Path
from common import atomic_json, load_json
from pipeline import check


def build(folder, allow_partial_source=False):
    state = check(folder)
    mandatory = ['processing', 'evidence', 'report']
    if not allow_partial_source:
        mandatory.append('collection')
    failures = [k+'='+state['stages'][k]['status']+': '+'; '.join(state['stages'][k]['reasons'])
                for k in mandatory if state['stages'][k]['status'] != 'PASS']
    if failures:
        raise ValueError('\n'.join(failures))
    data = deepcopy(load_json(Path(folder)/'analysis.json')['REPORT_DATA'])
    data['readiness'] = dict(input_sha256=state['input_sha256'],
                            source_complete=state['stages']['collection']['status']=='PASS',
                            processing_complete=True, evidence_complete=True,
                            ui_status=state['stages']['ui']['status'],
                            research_success=state['success'],
                            limitations=state['stages']['collection']['reasons'])
    return data


if __name__ == '__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('folder'); parser.add_argument('--out',required=True)
    parser.add_argument('--allow-partial-source',action='store_true')
    args=parser.parse_args()
    try:
        out=Path(args.out).resolve(); folder=Path(args.folder).resolve()
        if out == folder or folder in out.parents:
            raise ValueError('output must be temporary outside 3-file research folder')
        data=build(folder,args.allow_partial_source)
        atomic_json(out,data)
        print('REPORT_DATA exported; run pipeline.py finish to check overall completion')
    except (ValueError,OSError,KeyError,TypeError) as exc:
        print('FAIL: '+str(exc),file=sys.stderr);sys.exit(1)
