"""Builder regression with authored files and a fake lifter/compiler; no game or SDK required."""
import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


make = load('make_game_under_test', ROOT / 'nx2/make_game.py')
splitter = load('split_hot_under_test', ROOT / 'nx2/split_hot.py')


def main():
    work_root = ROOT / 'work'
    work_root.mkdir(exist_ok=True)
    for profile in (False, True):
        with tempfile.TemporaryDirectory(prefix='builder-profile-', dir=work_root) as tmp:
            directory = Path(tmp).resolve()
            directory.relative_to(work_root.resolve())
            project, exefs, romfs = (directory / n for n in ('project', 'exefs', 'romfs'))
            for folder in (project / 'work', exefs, romfs):
                folder.mkdir(parents=True)
            # Container signature/build-id metadata only, not an executable payload.
            header = bytearray(96)
            header[:4] = b'NSO0'
            header[64:68] = bytes.fromhex(make.BUILD_ID)
            (exefs / 'main').write_bytes(header)
            if profile:
                (project / 'work/hot_funcs.txt').write_text('0x10000000 10\n')
            build, game = directory / 'build', directory / 'game'
            for target in (build / 'exefs', game / 'romfs'):
                target.resolve().relative_to(directory)
            commands = []

            def run(command, env=None, on_line=None):
                script = Path(command[2]).name
                commands.append(script)
                if script == 'lift.py':
                    assert bool(env.get('A64_HOT_FUNCS')) == profile
                    gen = build / 'sa/gen'
                    gen.mkdir(parents=True)
                    image = build / 'sa/image'
                    image.mkdir()
                    (image / 'image.bin').write_bytes(b'authored fixture')
                    if profile:
                        (gen / 'code_hot.c').write_text('void f_10000000(Ctx *__restrict A64_NOESC c) {\nreturn;\n}\n')
                elif script == 'split_hot.py':
                    splitter.split(str(build / 'sa/gen'), 8)

            argv = ['make_game.py', '--exefs', str(exefs), '--romfs', str(romfs), '--work', str(build), '--out', str(game)]
            with patch.object(make, 'ROOT', str(project)), patch.object(make, 'check_windows_sdk'), \
                    patch.object(make, 'run', run), patch.object(make.subprocess, 'call', return_value=0), \
                    patch.object(sys, 'argv', argv), contextlib.redirect_stdout(io.StringIO()):
                make.main()
            assert commands.count('split_hot.py') == int(profile), commands
            assert commands.count('build.py') == 1, commands
            assert (game / 'image.bin').read_bytes() == b'authored fixture'
            if profile:
                assert commands.index('split_hot.py') < commands.index('build.py')
                assert len(list((build / 'sa/gen').glob('code_hot_*.c'))) == 8
            print('PASS:', 'profiled build still splits' if profile else 'fresh build needs no profile')


if __name__ == '__main__':
    main()
