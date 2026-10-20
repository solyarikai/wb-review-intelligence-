"""Build the pinned inline UI from validated research. No scraping or UI attestation."""
import argparse
from copy import deepcopy
import hashlib
from html import escape
import json
from pathlib import Path
import re
import sys

from build_report_data import build
from common import atomic_text, digest

RENDERER_VERSION = '1.0.0'
ASSETS = Path(__file__).resolve().parents[1] / 'assets' / 'report'
MAX_BYTES = 999_999


def renderer_sha256():
    files = ['shell.html', 'report.css', 'report.js']
    return digest({'version': RENDERER_VERSION,
                   'assets': {name: hashlib.sha256((ASSETS/name).read_bytes()).hexdigest() for name in files}})


def pack(data):
    """Intern strings and keys losslessly; preserve arbitrary signed numbers."""
    strings, indices = [], {}

    def intern(value):
        if value not in indices:
            indices[value] = len(strings)
            strings.append(value)
        return indices[value]

    def base36(value):
        result = ''
        while value:
            result = '0123456789abcdefghijklmnopqrstuvwxyz'[value % 36] + result
            value //= 36
        return result or '0'

    def encode(value):
        if isinstance(value, str): return -intern(value)-1
        if isinstance(value, list): return [encode(item) for item in value]
        if isinstance(value, dict): return {base36(intern(key)): encode(item) for key, item in value.items()}
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value < 0:
            return {'!': value}
        return value

    tree = encode(data)
    return dict(strings=strings, tree=tree)


def unpack(payload):
    def decode(value):
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value < 0:
            return payload['strings'][-value-1]
        if isinstance(value, list): return [decode(item) for item in value]
        if isinstance(value, dict):
            if '!' in value: return value['!']
            return {payload['strings'][int(key, 36)]: decode(item) for key, item in value.items()}
        return value
    return decode(payload['tree'])


def safe_json(value):
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    for old, new in [('<', '\\u003c'), ('>', '\\u003e'), ('&', '\\u0026'), ('\u2028', '\\u2028'), ('\u2029', '\\u2029')]:
        text = text.replace(old, new)
    return text


def text(value):
    return escape(str(value if value is not None else '—'), quote=True)


def date(value):
    if not isinstance(value, str) or not re.match(r'^\d{4}-\d{2}-\d{2}', value):
        return 'Дата неизвестна'
    return '.'.join(reversed(value[:10].split('-')))


def top_themes(data):
    result = []
    for context, title in [('positive', 'За что выбирают'), ('negative', 'Что настораживает')]:
        rows = []
        themes = sorted((theme for theme in data['themes'] if theme['context'] == context), key=lambda t: -t['count'])[:3]
        for theme in themes:
            n, total = theme['count'], data['metrics']['text']
            share = 100*n/total if total else 0
            percent = f'{share:.2f}'.replace('.', ',')+'%' if total else '—'
            rows.append('<div class="ri-bar-row"><span>'+text(theme['title'])+'</span><strong class="ri-bar-value">'+percent+
                        '</strong><div class="ri-track" aria-hidden="true"><span style="width:'+str(min(share, 100))+
                        '%"></span></div><span class="ri-muted ri-small ri-bar-value">'+text(n)+'/'+text(total)+'</span></div>')
        if not themes: rows.append('<p class="ri-muted">Тем этого контекста в разметке нет.</p>')
        result.append('<div class="ri-box" data-context="'+context+'"><h3 class="ri-'+context+'">'+title+'</h3>'+''.join(rows)+'</div>')
    return ''.join(result)


def fragment(data, *, enforce_inline_limit=True):
    """Internal renderer for already validated build() output; keep all evidence."""
    data = deepcopy(data)
    if data.get('readiness', {}).get('processing_complete') is not True or data['readiness'].get('evidence_complete') is not True:
        raise ValueError('render requires validated processing and evidence readiness')
    # A newly generated renderer has no host receipt. Never inherit an old UI PASS.
    data['readiness']['ui_status'] = 'NOT_VERIFIED'
    data['readiness']['research_success'] = False
    renderer_hash = renderer_sha256()
    report_hash = digest({key: value for key, value in data.items() if key != 'readiness'})
    meta = dict(renderer_version=RENDERER_VERSION, renderer_sha256=renderer_hash,
                report_sha256=report_hash, input_sha256=data['readiness']['input_sha256'],
                ui_verified=False)
    root_id = 'ri-'+digest(meta)[:16]
    payload = pack(data)
    if unpack(payload) != data: raise ValueError('lossless packing check failed')
    area = 'article' if 'article' in data['areas'] else next(iter(data['areas']))
    d = data['areas'][area]
    product, scope, dates = d.get('product') or data.get('product', {}), d.get('scope', {}), d.get('dates', {})
    label = ('АРТИКУЛ '+str(product.get('id', data.get('product', {}).get('id', 'не указан')))
             if area == 'article' else 'ГРУППА'+(' · '+str(scope['variant_count'])+' вариантов' if scope.get('variant_count') else ''))
    if product.get('brand'): label += ' · '+str(product['brand'])
    source = 'Охват источника подтверждён' if data['readiness']['source_complete'] else 'Полнота источника не подтверждена'
    values = dict(ROOT_ID=root_id, RENDERER_VERSION=RENDERER_VERSION,
                  PRODUCT_LABEL=text(label), PRODUCT_NAME=text(product.get('name') or ('Исходный артикул' if area == 'article' else 'Группа товаров')),
                  ANALYZED=text(d['metrics']['analyzed']), RETURNED=text(d['metrics']['returned']), EMPTY=text(d['metrics']['empty']),
                  PERIOD=text(date(dates.get('start'))+' — '+date(dates.get('end'))+' · Тем: '+str(len(d['themes']))),
                  SCOPE='<p>'+text(source)+'</p><p>'+text(scope.get('coverage', {}).get('basis', 'Основание охвата не указано.'))+'</p><p>'+text(scope.get('description', ''))+'</p>',
                  SUMMARY=text(d.get('summary') or 'Выводы для этой области не сформированы.'), TOP_THEMES=top_themes(d),
                  FOOTER=text('Review Intelligence · Wildberries · Снимок '+date(dates.get('generated_at'))+' · '+source),
                  AREA_BUTTONS=''.join('<button type="button" disabled class="ri-pill cursor-interaction" data-area="'+key+'" aria-pressed="'+str(key == area).lower()+'">'+('Исходный артикул' if key == 'article' else 'Вся группа')+'</button>' for key in data['areas']))
    shell = (ASSETS/'shell.html').read_text(encoding='utf-8')
    # Substitute once: untrusted data may itself contain placeholder-looking text.
    shell = re.sub(r'__([A-Z_]+)__', lambda match: values[match[1]], shell)
    css = (ASSETS/'report.css').read_text(encoding='utf-8').replace('__ROOT_ID__', root_id)
    script = (ASSETS/'report.js').read_text(encoding='utf-8')
    script_values = {'ROOT_ID':root_id, 'REPORT_PAYLOAD':safe_json(payload), 'REPORT_META':safe_json(meta)}
    script = re.sub(r'__([A-Z_]+)__', lambda match: script_values[match[1]], script)
    html = shell+'\n<style>\n'+css+'\n</style>\n<script>\n'+script+'\n</script>\n'
    encoded = html.encode('utf-8')
    if enforce_inline_limit and len(encoded) > MAX_BYTES:
        raise ValueError(f'inline report is {len(encoded)} bytes, limit {MAX_BYTES}; no evidence was dropped; UI is BLOCKED')
    inventory = {key: {name: [item['id'] for item in value[field]] for name, field in
                      [('theme_ids','themes'), ('quote_ids','quotes'), ('question_ids','questions'), ('opportunity_ids','opportunities')]}
                 for key, value in data['areas'].items()}
    return html, dict(**meta, root_id=root_id, bytes=len(encoded), fragment_sha256=hashlib.sha256(encoded).hexdigest(), inventory=inventory)


def render(folder, out, allow_partial_source=False):
    folder, out = Path(folder).resolve(), Path(out).resolve()
    if out.suffix != '.html' or out == folder or folder in out.parents:
        raise ValueError('inline fragment must be .html outside the 3-file research folder')
    data = build(folder, allow_partial_source)
    html, metadata = fragment(data)
    atomic_text(out, html)
    metadata['path'] = str(out)
    return metadata


def render_local(folder, out, allow_partial_source=False):
    """Standalone, offline HTML; the chat transport size cap does not apply."""
    folder, out = Path(folder).resolve(), Path(out).resolve()
    if out.suffix != '.html' or out == folder or folder in out.parents:
        raise ValueError('local HTML must be outside the three-file data folder')
    data = build(folder, allow_partial_source)
    body, metadata = fragment(data, enforce_inline_limit=False)
    title = text(data.get('product', {}).get('name') or data['report_id'])
    html = ('<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>'+title+'</title></head><body>'+body+'</body></html>\n')
    atomic_text(out, html)
    encoded = html.encode('utf-8')
    metadata.update(path=str(out), bytes=len(encoded),
                    standalone_sha256=hashlib.sha256(encoded).hexdigest())
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder'); parser.add_argument('--out', required=True)
    parser.add_argument('--allow-partial-source', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(render(args.folder, args.out, args.allow_partial_source), ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as error:
        print('FAIL: '+str(error), file=sys.stderr); return 1
    return 0


if __name__ == '__main__': sys.exit(main())
