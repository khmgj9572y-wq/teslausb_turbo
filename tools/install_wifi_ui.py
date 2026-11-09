#!/usr/bin/env python3
"""Patch both TeslaUSB web views and nginx for the protected Wi-Fi tab.

Run after copying the Wi-Fi assets. This is idempotent and deliberately refuses
to expose a network-changing CGI if TeslaUSB's global Basic auth is disabled.
"""

import argparse
from pathlib import Path


CLASSIC_CSS = "#tab7:checked ~ .tab.content7 {"
CLASSIC_SELECTED = "#tab7:checked ~ .tabheaderholder .tablabel7 {"
CLASSIC_RADIO = '<input class="tabradiobtn" type="radio" name="tabs" id="tab7" checked>'
CLASSIC_LABEL = '<label for="tab7" class="tablabel tablabel7">Viewer</label>'
CLASSIC_CONTENT = '  <div id="settingsoverlay" class="modaloverlay">'
NEW_SCRIPT = '<script src="/wifi/new-ui-link.js" defer></script>'
PORTAL_LOCATION = """    location /wifi/portal/ {
        auth_basic "Restricted Content";
        auth_basic_user_file /etc/nginx/.htpasswd;
        proxy_pass http://127.0.0.1:6080/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }

"""


def replace_once(content, old, new):
    if content.count(old) != 1:
        raise ValueError("TeslaUSB web layout changed; expected one anchor: " + old[:60])
    return content.replace(old, new, 1)


def classic(content):
    if 'id="tab8"' in content:
        return content
    content = replace_once(content, CLASSIC_CSS,
        "#tab7:checked ~ .tab.content7,\n#tab8:checked ~ .tab.content8 {")
    content = replace_once(content, CLASSIC_SELECTED,
        "#tab7:checked ~ .tabheaderholder .tablabel7,\n"
        "  #tab8:checked ~ .tabheaderholder .tablabel8 {")
    content = replace_once(content, CLASSIC_RADIO,
        CLASSIC_RADIO + '\n  <input class="tabradiobtn" type="radio" name="tabs" id="tab8">')
    content = replace_once(content, CLASSIC_LABEL,
        CLASSIC_LABEL + '\n    <label for="tab8" class="tablabel tablabel8">Wi-Fi</label>')
    content = replace_once(content, CLASSIC_CONTENT,
        '  <div class="tab content8"><iframe src="/wifi/" title="Wi-Fi" '
        'style="width:100%;height:100%;border:0"></iframe></div>\n' + CLASSIC_CONTENT)
    return content


def alternative(content):
    if NEW_SCRIPT in content:
        return content
    return replace_once(content, "</body>", NEW_SCRIPT + "\n</body>")


def moonlight(content):
    content = classic(content)
    if 'id="car-moonlight-frame"' in content:
        return content
    content = replace_once(content, '#tab8:checked ~ .tab.content8 {',
        '#tab8:checked ~ .tab.content8,\n#tab9:checked ~ .tab.content9 {')
    content = replace_once(content, '#tab8:checked ~ .tabheaderholder .tablabel8 {',
        '#tab8:checked ~ .tabheaderholder .tablabel8,\n'
        '  #tab9:checked ~ .tabheaderholder .tablabel9 {')
    radio = '<input class="tabradiobtn" type="radio" name="tabs" id="tab8">'
    content = replace_once(content, radio, radio +
        '\n  <input class="tabradiobtn" type="radio" name="tabs" id="tab9">')
    label = '<label for="tab8" class="tablabel tablabel8">Wi-Fi</label>'
    content = replace_once(content, label, label +
        '\n    <label for="tab9" class="tablabel tablabel9">Moonlight</label>')
    return replace_once(content, CLASSIC_CONTENT,
        '  <div class="tab content9"><iframe id="car-moonlight-frame" '
        'src="/moonlight/" title="Moonlight" '
        'style="width:100%;height:100%;border:0"></iframe></div>\n' + CLASSIC_CONTENT)


def nginx(content):
    if 'auth_basic off;' in content or content.count('auth_basic "Restricted Content";') < 2:
        raise ValueError("TeslaUSB web authentication is not enabled")
    if "location /wifi/portal/" in content:
        return content
    return replace_once(content, "    location /cgi-bin/ {",
                        PORTAL_LOCATION + "    location /cgi-bin/ {")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web-root", type=Path, default=Path("/var/www/html"))
    parser.add_argument("--nginx", type=Path,
                        default=Path("/etc/nginx/sites-available/teslausb.nginx"))
    args = parser.parse_args()
    paths = [args.web_root / "index.html", args.web_root / "new" / "index.html",
             args.nginx]
    transforms = [moonlight, alternative, nginx]
    updated = []
    for path, transform in zip(paths, transforms):
        original = path.read_text()
        changed = transform(original)
        updated.append((path, original, changed))
    for path, original, changed in updated:
        if original != changed:
            backup = path.with_name(path.name + ".before-car-wifi")
            if not backup.exists():
                backup.write_text(original)
            path.write_text(changed)
            print("patched", path)
        else:
            print("already patched", path)


if __name__ == "__main__":
    main()
