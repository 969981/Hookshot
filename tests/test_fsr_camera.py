"""Compile and execute game-free temporal reprojection cases."""
import argparse
import pathlib
import subprocess
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument('--cxx', default='clang++')
args = parser.parse_args()
root = pathlib.Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='hookshot-fsr-camera-') as folder:
    binary = pathlib.Path(folder) / 'fsr-camera.exe'
    subprocess.run([args.cxx, '-std=c++17', '-O2', str(root / 'tests/fsr_camera_test.cpp'), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
