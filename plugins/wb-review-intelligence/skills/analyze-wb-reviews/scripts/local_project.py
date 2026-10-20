"""Project-local persistence. No network, cloud credentials or automatic uploads."""
import argparse
from datetime import datetime, timezone
from html import escape
import hashlib
import json
from pathlib import Path
import re
import sys
import uuid
from urllib.parse import quote

from common import atomic_json, atomic_text, load_json
import pipeline
from render_report import render_local, render as render_inline

DIRECTORY = 'wb-review-intelligence'
FILES = ('corpus.jsonl', 'analysis.json', 'manifest.json')
RUN_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,119}$')


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def validate_run_id(value):
    if not isinstance(value, str) or not RUN_ID.fullmatch(value):
        raise ValueError('run_id must be 1..120 ASCII letters/digits/_/-; no paths')
    return value


def child(base, *parts):
    """Constrain every path component and refuse symlink-backed storage."""
    target = base
    for part in parts:
        target = target / part
        if target.is_symlink():
            raise ValueError('storage path must not be a symlink: '+str(target))
    if not target.resolve().is_relative_to(base.resolve()):
        raise ValueError('storage path escapes project')
    return target


def layout(project, create=False):
    project = Path(project).expanduser().resolve(strict=True)
    if not project.is_dir() or DIRECTORY in project.parts:
        raise ValueError('pass the project root, not a storage subdirectory')
    root = child(project, DIRECTORY)
    if create:
        root.mkdir(mode=0o700, exist_ok=True)
        child(root, 'runs').mkdir(mode=0o700, exist_ok=True)
    return project, root


def locations(root, run_id):
    validate_run_id(run_id)
    run = child(root, 'runs', run_id)
    data = child(root, 'runs', run_id, 'data')
    for name in FILES:
        child(data, name)
    child(run, 'report.html')
    return run, data


def registry(root):
    path = child(root, 'registry.jsonl')
    if not path.exists():
        return {}
    rows = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        key = validate_run_id(entry['run_id'])
        if key in rows:
            raise ValueError('duplicate registry run_id: '+key)
        rows[key] = entry
    return rows


def legacy_entries(root, rows):
    """References to earlier studies; never copy or rewrite their data."""
    catalog = child(root, 'legacy-index.json')
    if not catalog.exists():
        return []
    data = load_json(catalog)
    if data.get('version') != 1 or not isinstance(data.get('entries'), list):
        raise ValueError('invalid legacy index')
    project = root.parent
    output, seen, paths = [], set(), set()
    for raw in data['entries']:
        entry = dict(raw)
        key = validate_run_id(entry['run_id'])
        if key in seen or key in rows:
            raise ValueError('legacy run_id collision: '+key)
        seen.add(key)
        for field in ('source_path', 'report_path'):
            value = entry.get(field)
            if field == 'report_path' and value is None:
                continue
            if (not isinstance(value, str) or not value or '\\' in value
                    or any(part in ('', '.', '..') for part in value.split('/'))
                    or Path(value).is_absolute()):
                raise ValueError('invalid legacy '+field)
            target = child(project, *value.split('/'))
            if not (target.is_dir() if field == 'source_path' else target.is_file()):
                raise ValueError('legacy path missing: '+value)
        if entry['source_path'] in paths:
            raise ValueError('duplicate legacy source path')
        paths.add(entry['source_path'])
        alias = entry.get('alias_of')
        if alias is not None and (not isinstance(alias,str) or alias not in rows):
            raise ValueError('legacy alias must reference a current run')
        entry['legacy'] = True
        output.append(entry)
    return output


def history_rows(root, rows):
    legacy = legacy_entries(root, rows)
    output = []
    for key, raw in rows.items():
        entry = dict(raw)
        entry['legacy_aliases'] = [x for x in legacy if x.get('alias_of') == key]
        output.append(entry)
    return output + [x for x in legacy if not x.get('alias_of')]


def legacy_link(entry):
    label = escape(entry.get('title') or entry['run_id'])
    report = entry.get('report_path')
    if report:
        label = '<a href="../'+quote(report, safe='/')+'">'+label+'</a>'
    return label


def save_registry(root, rows):
    legacy = legacy_entries(root, rows)  # Validate before any registry write.
    # One project-level POSIX directory flock covers every read/merge/replace.
    atomic_text(child(root, 'registry.jsonl'), ''.join(
        json.dumps(rows[key], ensure_ascii=False, allow_nan=False)+'\n'
        for key in sorted(rows)))
    links = []
    for key, entry in sorted(rows.items(), key=lambda item: item[1].get('updated_at', ''), reverse=True):
        label = escape(entry.get('title') or key)
        status = escape(', '.join(k+'='+v for k, v in entry.get('stage_statuses', {}).items()))
        if entry.get('report_current'):
            label = '<a href="runs/'+key+'/report.html">'+label+'</a>'
        else:
            label += ' — HTML ещё не создан или устарел'
        aliases = [x for x in legacy if x.get('alias_of') == key]
        extras = ''.join('<br><small>Сохранённая совместимая копия: '+legacy_link(x)+'</small>' for x in aliases)
        links.append('<li>'+label+'<br><small>'+escape(key)+' · '+status+'</small>'+extras+'</li>')
    old = [x for x in legacy if not x.get('alias_of')]
    archive = ('<h2>Ранее сохранённые исследования</h2><p>Прежний формат, данные остаются по исходным путям. Статусы нового контролёра им не присваиваются.</p><ul>' + ''.join('<li>'+legacy_link(x)+'<br><small>'+escape(x['run_id'])+' · '+escape(x.get('format','legacy'))+'</small></li>' for x in old) + '</ul>') if old else ''
    html = ('<!doctype html><html lang="ru"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>История исследований WB</title><body><h1>История исследований WB</h1>'
            '<p>Локальные данные этого проекта. Сохранение не означает полноту источника или проверки UI.</p>'
            '<ul>'+''.join(links)+'</ul>'+archive+'</body></html>\n')
    atomic_text(child(root, 'index.html'), html)


def file_digest(path):
    data = path.read_bytes()
    return dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


def record_locked(root, run_id, title=None, render=False, allow_partial=False):
    run, data = locations(root, run_id)
    if not data.is_dir():
        raise ValueError('run data not found: '+str(data))
    rows = registry(root)  # Invalid/duplicate index must not be overwritten.
    legacy_entries(root, rows)  # Reject unsafe history before touching run files.
    previous = rows.get(run_id, {})
    manifest = load_json(data/'manifest.json')
    if manifest.get('run_id') != run_id:
        raise ValueError('manifest run_id differs from folder')
    state = pipeline.check(data)
    checks = {name: file_digest(data/name) for name in FILES[:2]}
    storage = dict(provider='local_project', root_directory=DIRECTORY,
                   run_directory='runs/'+run_id, data_directory='runs/'+run_id+'/data',
                   checkpoint_at=timestamp(), files=checks,
                   verification='Full persisted bytes read back and SHA-256 in registry')
    manifest = load_json(data/'manifest.json')
    manifest['storage'] = storage
    atomic_json(data/'manifest.json', manifest)
    state = pipeline.check(data)
    before = {name: file_digest(data/name) for name in FILES}
    rendered = None
    inline = None
    inline_error = None
    render_error = None
    if render:
        try:
            rendered = render_local(data, run/'report.html', allow_partial)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            render_error = str(exc)
        if rendered:
            try:
                inline = render_inline(data, child(run, 'message.html'), allow_partial)
            except (ValueError, OSError, KeyError, TypeError) as exc:
                # A chat transport limit must not block the standalone local file.
                inline_error = str(exc)
    # Refuse to publish a coherent index if another pipeline writer changed data.
    after = {name: file_digest(data/name) for name in FILES}
    final_state = pipeline.assess(data)
    if (any(before[name] != after[name] for name in FILES[:2])
            or final_state['input_sha256'] != state['input_sha256']):
        raise ValueError('data changed during checkpoint; retry on current data')
    state = final_state
    report_hash = rendered['input_sha256'] if rendered else previous.get('report_input_sha256')
    html_file = run/'report.html'
    expected_html_sha = (rendered['standalone_sha256'] if rendered
                         else previous.get('report_sha256'))
    actual_html_sha = file_digest(html_file)['sha256'] if html_file.is_file() else None
    current = bool(not render_error and report_hash == state['input_sha256']
                   and expected_html_sha and actual_html_sha == expected_html_sha)
    entry = dict(run_id=run_id, title=title or previous.get('title') or run_id,
                 updated_at=timestamp(), data_path='runs/'+run_id+'/data',
                 files=after, input_sha256=state['input_sha256'],
                 stage_statuses={k:v['status'] for k,v in state['stages'].items()},
                 research_success=state['success'], storage_verified=True,
                 report_path='runs/'+run_id+'/report.html' if actual_html_sha else None,
                 report_current=current, report_input_sha256=report_hash,
                 report_sha256=actual_html_sha, render_error=render_error)
    rows[run_id] = entry
    save_registry(root, rows)
    if registry(root)[run_id] != entry:
        raise ValueError('registry read-back mismatch')
    result = dict(project_storage=str(root), data_folder=str(data),
                  index_html=str(root/'index.html'), entry=entry)
    if rendered:
        result['report_html'] = str(html_file)
    if inline:
        result['message_html'] = inline['path']
        result['inline_reference'] = '\ue200visualize\ue202'+json.dumps(
            {'path':inline['path']}, ensure_ascii=False)+'\ue201'
        result['inline_ui_status'] = 'PREPARED_NOT_VERIFIED'
    if inline_error:
        result['inline_blocked'] = inline_error
    if render_error:
        result['render_blocked'] = render_error
    return result


def initialize(project, article=None, run_id=None, title=None):
    project, root = layout(project, create=True)
    if article is not None and not re.fullmatch(r'[0-9]+', article):
        raise ValueError('article must contain digits only')
    run_id = validate_run_id(run_id or ('wb-'+(article or 'research')+'-'
        +datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:6]))
    with pipeline.locked(root):
        config_file = child(root, 'config.json')
        config = dict(version=1, provider='local_project', reports_directory='runs',
                      registry='registry.jsonl', index='index.html',
                      cloud_uploads=False)
        if config_file.exists() and load_json(config_file) != config:
            raise ValueError('local config differs; inspect before using storage')
        registry(root)
        run, data = locations(root, run_id)
        data.mkdir(mode=0o700, parents=True, exist_ok=True)
        atomic_json(config_file, config)
        pipeline.initialize(data, run_id)
        return record_locked(root, run_id, title)


def checkpoint(project, run_id, title=None, render=False, allow_partial=False):
    _, root = layout(project)
    if not root.is_dir():
        raise ValueError('local project storage missing; run init')
    with pipeline.locked(root):
        return record_locked(root, run_id, title, render, allow_partial)


def list_runs(project):
    _, root = layout(project)
    if not root.exists():
        return []
    with pipeline.locked(root):
        return history_rows(root, registry(root))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', default='.', help='Actual project root; default cwd')
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init')
    init.add_argument('--article'); init.add_argument('--run-id'); init.add_argument('--title')
    command = commands.add_parser('checkpoint')
    command.add_argument('run_id'); command.add_argument('--title')
    command.add_argument('--render', action='store_true')
    command.add_argument('--allow-partial-source', action='store_true')
    commands.add_parser('list')
    args = parser.parse_args()
    try:
        if args.command == 'init':
            result = initialize(args.project, args.article, args.run_id, args.title)
        elif args.command == 'list':
            result = list_runs(args.project)
        else:
            result = checkpoint(args.project, args.run_id, args.title,
                                args.render, args.allow_partial_source)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return int(isinstance(result, dict) and bool(result.get('render_blocked')))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print('FAIL: '+str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
