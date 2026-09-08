"""Generate mobile-sized SQL sections; --check reports drift without writing.

Explicit top-level boundaries keep DO/function bodies intact. The canonical
source is supabase_schema.sql; edit that file, then regenerate these sections.
"""
import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BANNER = re.compile(r"^-- SECTION: ([a-z][a-z0-9_]*)\r?$", re.MULTILINE)


def sections(source):
    """Partition at schema-owned top-level banners, including the preamble."""
    banners = list(BANNER.finditer(source))
    if not banners:
        raise ValueError('Schema has no SECTION banners')
    names = [match.group(1) for match in banners]
    if len(names) != len(set(names)):
        raise ValueError('Duplicate schema section names')
    starts = [0] + [match.start() for match in banners[1:]]
    ends = starts[1:] + [len(source)]
    return {f'{i:02d}_{name}.sql': source[start:end]
            for i, (name, start, end) in enumerate(zip(names, starts, ends), 1)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    expected = sections((ROOT / 'supabase_schema.sql').read_bytes().decode('utf-8'))
    destination = ROOT / 'schema_sections'
    stale = sorted(p.name for p in destination.glob('*.sql') if p.name not in expected)
    changed = [name for name, text in expected.items()
               if not (destination / name).exists() or (destination / name).read_bytes().decode('utf-8') != text]
    if args.check:
        if stale or changed:
            print('Schema section drift: ' + ', '.join(stale + changed))
            return 1
        print(f'{len(expected)} schema sections match supabase_schema.sql')
        return 0
    if stale:
        parser.error('Unexpected SQL files; review manually: ' + ', '.join(stale))
    destination.mkdir(exist_ok=True)
    for name, text in expected.items():
        (destination / name).write_bytes(text.encode('utf-8'))
    print(f'Generated {len(expected)} schema sections')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
