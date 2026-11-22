"""What OpenDLSS-NR reads from the game folder is what it should be (gpu/vk/vk_opennr.cpp materialize_kernels,
gpu/vk/opennr/opennr_net.cpp check_model and the network's model load). Audit 2026-11-14, PR-C F2.

- Its kernels (.spv, .ptx) are the renderer's own, written into the game folder's hookshot_opennr_kernels/<hash>/. A later
  start trusted that folder whenever its "complete" mark was there, so a kernel changed there ran as it was. Now each file
  is compared with the renderer's bytes at every start and written again where it differs.
- A stage of the model is a plain file name in model/: the manifest's "file" was joined to the folder as it was, so "../"
  or a drive named any file the game could read, taken as weights.
- The model is loaded with its hashes verified: check_model hashed the files it read, and the load read them again
  unverified, so a file changed in between was taken.

The code needs the network's headers and a GPU to run, so this reads the source.

python -I -B tests/test_opennr_inputs.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def body(text, head):
    i = text.index(head)
    return text[i:text.index("\n}\n", i)]


def main():
    fails = []
    vk = (ROOT / "gpu/vk/vk_opennr.cpp").read_text(encoding="utf-8")
    mat = body(vk, "std::string materialize_kernels(std::string* error) {")
    if 'exists(dir / "complete"' in mat:
        fails.append("materialize_kernels trusts the kernels folder's \"complete\" mark again: the files are not compared")
    write = mat[mat.index("auto write = [&]"):mat.index("};", mat.index("auto write = [&]"))]
    if '"rb"' not in write or "memcmp(have.data(), data, n)" not in write or write.find('"rb"') > write.find('"wb"'):
        fails.append("materialize_kernels must compare each kernel file with the renderer's bytes before keeping it")
    net = (ROOT / "gpu/vk/opennr/opennr_net.cpp").read_text(encoding="utf-8")
    check = body(net, "ModelCheck check_model(const std::string& dir) {")
    rule = "if (file.empty() || file[0] == '.' || file.find_first_of(\"/\\\\:\") != std::string::npos) {"
    if rule not in check or check.find(rule) > check.find('dir + "/model/" + file'):
        fails.append("check_model must refuse a stage \"file\" that is not a plain name in model/ before it reads it")
    if "std::make_unique<nr::Model>(*n->context, model_dir, true)" not in net:
        fails.append("the network's model must be loaded with its hashes verified (nr::Model's verifyHashes)")
    if fails:
        raise SystemExit("\n".join(fails))
    print("PASS: OpenDLSS-NR's kernels compared at each start, its stage files plain names, its model verified as loaded")


if __name__ == "__main__":
    main()
