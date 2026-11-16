#!/usr/bin/env python3
"""Read-only public asset inventory. No deletion or capacity policy."""
import argparse
import json
import posixpath
from pathlib import Path
from urllib.parse import unquote, urlsplit

IMAGE_SUFFIXES = {'.webp', '.jpg', '.jpeg', '.png', '.gif', '.avif', '.svg', '.bmp', '.ico'}


def metric(sizes):
    sizes = list(sizes)
    total = sum(sizes)
    return {'files': len(sizes), 'bytes': total, 'mib': round(total / 1048576, 1)}


def catalog_files(path):
    if path.is_dir():
        files = sorted(path.glob('chunk-*.json'))
        if not files:
            raise ValueError('catalog directory contains no chunks')
        return files
    return [path]


def references(files):
    refs, nonlocal_refs, count = set(), [], 0
    for file in files:
        with file.open(encoding='utf-8') as stream:
            data = json.load(stream)
        rows = data.get('items') if isinstance(data, dict) else data
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError(f'invalid catalog items: {file}')
        for row in rows:
            count += 1
            raw = str(row.get('thumbnail') or '').strip()
            if not raw:
                continue
            try:
                url = urlsplit(raw)
                decoded = unquote(url.path).replace('\\', '/')
                normalized = posixpath.normpath(decoded)
                local = (not url.scheme and not url.netloc and not decoded.startswith('/')
                         and '..' not in decoded.split('/') and normalized.startswith('thumbs/'))
            except ValueError:
                local = False
            if local:
                refs.add(normalized)
            else:
                nonlocal_refs.append(raw)
        del rows, data
    return refs, nonlocal_refs, count


def measure(docs, tree_json=None, catalog=None):
    docs = Path(docs).resolve()
    truncated = False
    if tree_json:
        with Path(tree_json).open(encoding='utf-8') as stream:
            tree = json.load(stream)
        if not isinstance(tree, dict) or not isinstance(tree.get('tree'), list) or not isinstance(tree.get('truncated'), bool):
            raise ValueError('invalid recursive tree response')
        truncated = tree['truncated']
        files = {}
        for entry in tree['tree']:
            if entry.get('type') != 'blob':
                continue
            raw = entry['path']
            path = posixpath.normpath(raw)
            if raw.startswith('/') or '..' in raw.split('/'):
                raise ValueError('invalid tree path')
            if path.startswith('docs/'):
                size = entry.get('size')
                if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                    raise ValueError(f'invalid blob size: {path}')
                files[path[5:]] = size
        sha = tree.get('sha')
        del tree
    else:
        if not docs.is_dir():
            raise ValueError(f'docs directory missing: {docs}')
        files = {}
        for path in docs.rglob('*'):
            if path.is_symlink():
                raise ValueError(f'symlink cannot be safely inventoried: {path}')
            if path.is_file():
                files[path.relative_to(docs).as_posix()] = path.stat().st_size
        sha = None
    chosen = Path(catalog).resolve() if catalog else None
    if chosen is None and not tree_json:
        chosen = docs / 'data.json' if (docs / 'data.json').is_file() else docs / 'catalog'
    refs, external, count = references(catalog_files(chosen)) if chosen else (None, None, None)
    thumbs = {p: s for p, s in files.items() if p.startswith('thumbs/')}
    sources = sorted({p.split('/')[1] for p in thumbs})
    chunks = {p: s for p, s in files.items() if p.startswith('catalog/')}
    orphan = None if refs is None else sorted(p for p in thumbs if p not in refs and Path(p).suffix.lower() in IMAGE_SUFFIXES)
    missing = None if refs is None else sorted(refs - files.keys())
    result = {
        'mode': 'tree' if tree_json else 'local', 'sha': sha, 'truncated': truncated,
        'completeness': 'incomplete (truncated=true)' if truncated else 'complete inventory',
        'catalog_input': str(chosen) if chosen else None,
        'reference_caveat': ('ツリーとカタログのコミット一致は未検証。異なる時点の場合、未参照候補は上限の参考値。'
                             if tree_json and chosen else None),
        'data_json': {**metric([files['data.json']] if 'data.json' in files else []), 'items': count},
        'catalog': {**metric(chunks.values()), 'max_chunk_bytes': max((s for p, s in chunks.items() if Path(p).match('chunk-*.json')), default=0)},
        'thumbs': {s: metric(v for p, v in thumbs.items() if p.split('/')[1] == s) for s in sources},
        'docs': metric(files.values()),
        'other_root_files': {p: metric([s]) for p, s in sorted(files.items()) if '/' not in p and p != 'data.json'},
        'jp07': {'status': 'assessed' if refs is not None else 'カタログ未指定のため未判定',
                 'unreferenced': None if orphan is None else {s: metric(thumbs[p] for p in orphan if p.split('/')[1] == s) for s in sources},
                 'paths': orphan, 'missing_count': None if missing is None else len(missing), 'missing_paths': missing,
                 'nonlocal_count': None if external is None else len(external), 'nonlocal_references': external}}
    result['catalog']['max_chunk_mib'] = round(result['catalog']['max_chunk_bytes'] / 1048576, 1)
    return result


def summary(result):
    lines = [f"mode={result['mode']} sha={result['sha']} {result['completeness']}"]
    def add(label, value):
        lines.append(f"{label}: {value['files']} files, {value['bytes']} bytes ({value['mib']:.1f} MiB)")
    add('docs/data.json', result['data_json'])
    lines.append(f"catalog input items: {result['data_json']['items'] if result['data_json']['items'] is not None else '未判定'}")
    add('docs/catalog', result['catalog'])
    lines.append(f"max chunk: {result['catalog']['max_chunk_bytes']} bytes ({result['catalog']['max_chunk_mib']:.1f} MiB)")
    for source, value in result['thumbs'].items():
        add(f'docs/thumbs/{source}', value)
    add('docs total', result['docs'])
    for path, value in result['other_root_files'].items():
        add(f'docs/{path}', value)
    if result['reference_caveat']:
        lines.append(result['reference_caveat'])
    jp = result['jp07']
    lines.append(f"JP-07: {jp['status']}")
    if jp['paths'] is not None:
        for source, value in jp['unreferenced'].items():
            add(f'unreferenced/{source}', value)
        lines.append(f"missing references: {jp['missing_count']}; nonlocal references: {jp['nonlocal_count']}")
        lines.extend('docs/' + p for p in jp['paths'][:20])
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--docs', default='docs')
    parser.add_argument('--tree-json')
    parser.add_argument('--catalog')
    parser.add_argument('--json')
    parser.add_argument('--list')
    args = parser.parse_args(argv)
    try:
        outputs = [Path(p).resolve() for p in (args.json, args.list) if p]
        inputs = [Path(p).resolve() for p in (args.tree_json, args.catalog) if p]
        docs = Path(args.docs).resolve()
        for output in outputs:
            if output.exists() or output == docs or docs in output.parents or any(output == p or p in output.parents for p in inputs):
                raise ValueError(f'output must be a new file outside input directories: {output}')
        if len(set(outputs)) != len(outputs):
            raise ValueError('output paths must differ')
        result = measure(args.docs, args.tree_json, args.catalog)
        if args.list and result['jp07']['paths'] is None:
            raise ValueError('--list requires a catalog')
        if args.json:
            with Path(args.json).open('x', encoding='utf-8') as stream:
                json.dump(result, stream, ensure_ascii=False, indent=2)
                stream.write('\n')
        if args.list:
            with Path(args.list).open('x', encoding='utf-8') as stream:
                for path in result['jp07']['paths']:
                    stream.write('docs/' + path + '\n')
        print(summary(result))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    raise SystemExit(main())
