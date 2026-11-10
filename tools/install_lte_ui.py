#!/usr/bin/env python3
"""Add 4G navigation to both web views without changing authentication."""
import argparse
import shutil
import tempfile
from pathlib import Path


def replace(content, old, new):
    if content.count(old) != 1:
        raise ValueError('Unexpected TeslaUSB layout; no files changed')
    return content.replace(old, new, 1)


def classic(content):
    if 'id="car-lte-frame"' in content:
        return content
    if 'id="tab10"' in content:
        raise ValueError('Tab 10 is already in use; no files changed')
    content = replace(content, '#tab9:checked ~ .tab.content9 {',
                      '#tab9:checked ~ .tab.content9,\n#tab10:checked ~ .tab.content10 {')
    content = replace(content, '#tab9:checked ~ .tabheaderholder .tablabel9 {',
                      '#tab9:checked ~ .tabheaderholder .tablabel9,\n  #tab10:checked ~ .tabheaderholder .tablabel10 {')
    radio = '<input class="tabradiobtn" type="radio" name="tabs" id="tab9">'
    content = replace(content, radio, radio + '\n  <input class="tabradiobtn" type="radio" name="tabs" id="tab10">')
    label = '<label for="tab9" class="tablabel tablabel9">Moonlight</label>'
    content = replace(content, label, label + '\n    <label for="tab10" class="tablabel tablabel10">4G</label>')
    anchor = '  <div id="settingsoverlay" class="modaloverlay">'
    return replace(content, anchor, '  <div class="tab content10"><iframe id="car-lte-frame" src="/4g/" '
                   'title="4G" style="width:100%;height:100%;border:0"></iframe></div>\n' + anchor)


def alternative(content):
    tag = '<script src="/4g/new-ui-link.js" defer></script>'
    return content if tag in content else replace(content, '</body>', tag + '\n</body>')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--web-root', type=Path, default=Path('/var/www/html'))
    parser.add_argument('--backup-dir', type=Path, default=Path('/root/car-link-web-backups'))
    args = parser.parse_args()
    plans = []
    for path, transform in [(args.web_root / 'index.html', classic),
                            (args.web_root / 'new/index.html', alternative)]:
        original = path.read_text(); updated = transform(original)
        plans.append((path, original, updated))
    changed = [plan for plan in plans if plan[1] != plan[2]]
    if changed:
        args.backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        backup_dir = Path(tempfile.mkdtemp(prefix='lte-ui.', dir=args.backup_dir))
    for path, original, updated in changed:
        if original == updated:
            continue
        backup = backup_dir / path.relative_to(args.web_root)
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
        temp = path.with_name(path.name + '.car-lte-new')
        temp.write_text(updated); temp.chmod(path.stat().st_mode & 0o777); temp.replace(path)
        print('Added 4G navigation')


if __name__ == '__main__':
    main()
