"""Keep native task/mesh stages in graphics pipelines, including depth-only draws."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def selection_loop(source):
    definition = re.search(r'VkPipeline build_graphics\([^;{]*\)\s*\{', source)
    if definition is None:
        raise AssertionError('build_graphics definition not found')
    start = source.index('for (const Stage& st : p.stages)', definition.end())
    end = source.index('{', start) + 1
    depth = 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


HARNESS = r'''
#include <cstdint>
#include <vector>
#include <cassert>
#include <cstdio>
constexpr uint32_t VK_SHADER_STAGE_VERTEX_BIT = 1;
constexpr uint32_t VK_SHADER_STAGE_FRAGMENT_BIT = 0x10;
constexpr uint32_t VK_SHADER_STAGE_COMPUTE_BIT = 0x20;
constexpr uint32_t VK_SHADER_STAGE_TASK_BIT_EXT = 0x40;
constexpr uint32_t VK_SHADER_STAGE_MESH_BIT_EXT = 0x80;
constexpr int VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO = 18;
struct Stage { uint32_t vk_stage; uint64_t module; };
struct Program { std::vector<Stage> stages; };
struct VkPipelineShaderStageCreateInfo {
    int sType; uint32_t stage; uint64_t module; const char* pName;
};
void pin_subgroup(VkPipelineShaderStageCreateInfo&) {}
std::vector<uint32_t> select(const Program& p) {
    std::vector<VkPipelineShaderStageCreateInfo> stages;
    SELECTION
    std::vector<uint32_t> result;
    for (const auto& stage : stages) result.push_back(stage.stage);
    return result;
}
int main() {
    // Native mesh pipelines need both mesh stages, including depth-only pipelines.
    assert((select({{{0x40, 1}, {0x80, 2}, {0x10, 3}}}) == std::vector<uint32_t>{0x40, 0x80, 0x10}));
    assert((select({{{0x80, 1}}}) == std::vector<uint32_t>{0x80}));
    assert((select({{{0x40, 1}, {0x80, 2}}}) == std::vector<uint32_t>{0x40, 0x80}));
    assert((select({{{1, 1}, {0x10, 2}}}) == std::vector<uint32_t>{1, 0x10}));
    std::puts("graphics stage selection: native task/mesh, depth-only and vertex passed");
}
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cxx', default='clang++')
    args = parser.parse_args()
    source = (ROOT / 'gpu/vk/vk_draw.cpp').read_text(encoding='utf-8')
    with tempfile.TemporaryDirectory() as folder:
        folder = Path(folder)
        cpp = folder / 'stages.cpp'
        exe = folder / 'stages.exe'
        cpp.write_text(HARNESS.replace('SELECTION', selection_loop(source)), encoding='utf-8')
        subprocess.run([args.cxx, '-std=c++17', str(cpp), '-o', str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == '__main__':
    main()
