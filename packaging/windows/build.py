"""Stage a Windows x64 release from the four configured project environments."""
from __future__ import annotations

import argparse
import importlib.metadata as metadata
import json
from pathlib import Path
import shutil
import subprocess
import os
import tempfile
import tomllib

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PROJECTS = ('doc2md', 'DocBase', 'DocAgent', 'ResearchDesk/service')


def copy_file(source: Path, destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def copy_dependencies(project: Path, original: Path, target: Path):
    source_packages = original / 'Lib/site-packages'
    packages = {canonicalize_name(d.metadata['Name']): d
                for d in metadata.distributions(path=[str(source_packages)])}
    config = tomllib.loads((project / 'pyproject.toml').read_text(encoding='utf-8'))
    requirements = list(map(Requirement, config['project']['dependencies']))
    copied = set()
    while requirements:
        requirement = requirements.pop()
        if requirement.marker and not requirement.marker.evaluate({'extra': ''}):
            continue
        name = canonicalize_name(requirement.name)
        distribution = packages[name]
        if distribution.version not in requirement.specifier:
            raise RuntimeError(f'{name} {distribution.version} does not satisfy {requirement}')
        if name in copied:
            continue
        copied.add(name)
        requirements.extend(map(Requirement, distribution.requires or ()))
        if not distribution.files:
            raise RuntimeError(f'{name} has no package file manifest')
        for entry in distribution.files:
            if '..' in entry.parts or '__pycache__' in entry.parts:
                continue
            source = source_packages / entry
            if source.is_file():
                copy_file(source, target / 'Lib/site-packages' / entry)


def stage_runtime(project: Path, target: Path):
    original = project / '.conda'
    subprocess.run([str(original / 'python.exe'), '-I', '-c',
                    'import struct, sys; assert sys.version_info[:2] == (3, 11) and struct.calcsize("P") == 8'],
                   check=True)
    target.mkdir(parents=True)
    for source in [original / 'python.exe', original / 'LICENSE_PYTHON.txt', *original.glob('*.dll')]:
        copy_file(source, target / source.name)
    for source in (original / 'Library/bin').glob('*.dll'):
        if not (target / source.name).exists():
            copy_file(source, target / source.name)
    shutil.copytree(original / 'DLLs', target / 'DLLs', ignore=shutil.ignore_patterns('*.pdb'))
    shutil.copytree(original / 'Lib', target / 'Lib',
                    ignore=shutil.ignore_patterns('site-packages', '__pycache__', 'test', 'tests', 'ensurepip'))
    for record in (original / 'conda-meta').glob('*.json'):
        package = json.loads(record.read_text(encoding='utf-8'))
        source = Path(package.get('link', {}).get('source', '')) / 'info/licenses'
        if source.is_dir():
            shutil.copytree(source, target / 'licenses' / package['name'])
    copy_dependencies(project, original, target)
    (target / 'python311._pth').write_text('.\nLib\nDLLs\nLib/site-packages\n../src\n', encoding='ascii')


def compile_exe(source: Path, target: Path, *options):
    compiler = Path(os.environ['SystemRoot']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    subprocess.run([str(compiler), '/nologo', '/target:winexe', '/platform:x64', '/optimize+',
                    '/reference:System.Windows.Forms.dll', f'/out:{target}', *options, str(source)], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inno', type=Path, help='Path to ISCC.exe')
    parser.add_argument('--bundle', type=Path, help='Reuse a staged portable directory')
    parser.add_argument('--stage-only', action='store_true', help='Build a portable directory for testing')
    args = parser.parse_args()
    if not args.stage_only and args.inno is None:
        parser.error('--inno is required to build the installer')
    output = ROOT / 'dist/windows'
    output.mkdir(parents=True, exist_ok=True)
    if args.bundle:
        bundle = args.bundle.resolve()
        if not (bundle / 'ResearchDesk.exe').is_file():
            parser.error('--bundle must point to a staged release directory')
    else:
        bundle = Path(tempfile.mkdtemp(prefix='researchdesk-', dir=output))
        print(f'Staging {bundle}', flush=True)
        for name in PROJECTS:
            project, target = ROOT / name, bundle / name
            shutil.copytree(project / 'src', target / 'src',
                            ignore=shutil.ignore_patterns('__pycache__', '*.egg-info'))
            stage_runtime(project, target / '.conda')
            print(f'Packaged {name}', flush=True)
        for name in ('DocAgent/AGENTS.md', 'DocAgent/configs/docbase-context.env',
                     'doc2md/examples/sample.pdf', 'README.md'):
            copy_file(ROOT / name, bundle / name)
        shutil.copytree(ROOT / 'ResearchDesk/web/dist', bundle / 'ResearchDesk/web/dist')
        (bundle / 'ResearchDesk/bundled').touch()
        compile_exe(HERE / 'Launcher.cs', bundle / 'ResearchDesk.exe')
    if not args.stage_only:
        service = ROOT / 'ResearchDesk/service/pyproject.toml'
        version = tomllib.loads(service.read_text(encoding='utf-8'))['project']['version']
        subprocess.run([str(args.inno.resolve()), '/Qp', f'/DBundleDir={bundle}', f'/DAppVersion={version}',
                        f'/O{output}', str(HERE / 'setup.iss')], check=True)
    print(f'Portable directory: {bundle}', flush=True)


if __name__ == '__main__':
    main()
