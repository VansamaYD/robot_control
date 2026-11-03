"""Fetch fixed upstream assets; never executes downloaded code during fetching."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import urlopen, Request
import hashlib
import json
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent / 'assets'


def get(url):
    return urlopen(Request(url, headers={'User-Agent': 'arm-chain-prototype'}), timeout=60).read()


def main():
    ROOT.mkdir(exist_ok=True)
    sha = json.loads(get('https://api.github.com/repos/TheRobotStudio/SO-ARM100/commits/main'))['sha']
    base = f'https://raw.githubusercontent.com/TheRobotStudio/SO-ARM100/{sha}/Simulation/SO101/'
    urdf = get(base + 'so101_new_calib.urdf')
    entries = [('so101_new_calib.urdf', base + 'so101_new_calib.urdf', urdf)]
    files = sorted({node.attrib['filename'] for node in ET.fromstring(urdf).findall('.//mesh')})
    urls = [(name, base + name) for name in files]
    three = 'https://raw.githubusercontent.com/mrdoob/three.js/r170/'
    urls += [('three.module.js', three + 'build/three.module.js'),
             ('STLLoader.js', three + 'examples/jsm/loaders/STLLoader.js'),
             ('OrbitControls.js', three + 'examples/jsm/controls/OrbitControls.js'),
             ('THREE-LICENSE.txt', three + 'LICENSE')]
    for name in ['LICENSE', 'LICENSE.txt', 'LICENSE.md']:
        try:
            entries.append(('SO-ARM100-LICENSE.txt', f'https://raw.githubusercontent.com/TheRobotStudio/SO-ARM100/{sha}/{name}', get(f'https://raw.githubusercontent.com/TheRobotStudio/SO-ARM100/{sha}/{name}')))
            break
        except Exception:
            pass
    def fetch(pair):
        name, url = pair
        return name, url, get(url)
    with ThreadPoolExecutor(max_workers=4) as pool:
        entries.extend(pool.map(fetch, urls))
    manifest = {'so_arm100_commit': sha, 'three_tag': 'r170', 'assets': []}
    for name, url, data in entries:
        target = ROOT / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        manifest['assets'].append({'path': name, 'url': url, 'bytes': len(data),
                                   'sha256': hashlib.sha256(data).hexdigest()})
    (ROOT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'assets': len(entries), 'bytes': sum(len(e[2]) for e in entries),
                      'commit': sha, 'license_found': any('SO-ARM100-LICENSE' in e[0] for e in entries)}))


if __name__ == '__main__':
    main()
