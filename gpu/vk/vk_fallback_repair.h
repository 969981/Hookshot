// Diagnostic compatibility repair for the validated fallback SPIR-V defects.
// Limited to modules explicitly named mesh_fallback_records by the translator.
#pragma once
#include <algorithm>
#include <cstdint>
#include <cstring>
#include <map>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace fallback_repair {
using Word = uint32_t;
using Instruction = std::vector<Word>;
struct Result {
    std::vector<Word> words;
    unsigned indices = 0, alignments = 0, boolean_stores = 0;
    bool fallback = false;
};

inline Result repair(const Word* words, size_t count) {
    Result out;
    if (count < 5 || words[0] != 0x07230203u) return out;
    std::vector<Instruction> code;
    for (size_t pos = 5; pos < count;) {
        const Word size = words[pos] >> 16;
        if (!size || size > count - pos) throw std::runtime_error("invalid SPIR-V instruction length");
        code.emplace_back(words + pos, words + pos + size);
        const auto& ins = code.back();
        if ((ins[0] & 65535) == 6 && ins.size() >= 4) {
            const char* text = reinterpret_cast<const char*>(ins.data() + 3);
            const size_t bytes = (ins.size() - 3) * sizeof(Word);
            const char* end = static_cast<const char*>(std::memchr(text, 0, bytes));
            if (end && std::string(text, end) == "mesh_fallback_records") out.fallback = true;
        }
        pos += size;
    }
    if (!out.fallback) return out;
    std::unordered_set<Word> types, bool_types;
    for (const auto& ins : code) {
        const Word op = ins[0] & 65535;
        if (op >= 19 && op <= 39) types.insert(ins[1]);
        if (op == 20) bool_types.insert(ins[1]);
    }
    struct Pointer { Word storage, pointee; };
    struct Constant { Word id, type, value; };
    std::unordered_map<Word, Pointer> pointers;
    std::unordered_map<Word, Word> structure_sizes, result_types;
    std::unordered_map<Word, Constant> constant_ids;
    std::vector<Constant> constants;
    std::map<std::pair<Word, Word>, Word> offsets;
    for (const auto& ins : code) {
        const Word op = ins[0] & 65535;
        if (op == 32) pointers[ins[1]] = {ins[2], ins[3]};
        else if (op == 30) structure_sizes[ins[1]] = static_cast<Word>(ins.size() - 2);
        else if (op == 43 && ins.size() == 4) {
            constants.push_back({ins[2], ins[1], ins[3]});
            constant_ids[ins[2]] = constants.back();
        } else if (op == 72 && ins.size() >= 5 && ins[3] == 35) offsets[{ins[1], ins[4]}] = ins[2];
        if (ins.size() >= 3 && types.count(ins[1])) result_types[ins[2]] = ins[1];
    }
    Word bound = words[3];
    std::vector<Instruction> new_constants, amended;
    const auto constant = [&](Word type, Word value) -> Word {
        for (const auto& item : constants) if (item.type == type && item.value == value) return item.id;
        const Word id = bound++;
        constants.push_back({id, type, value});
        constant_ids[id] = constants.back();
        new_constants.push_back({(4u << 16) | 43u, type, id, value});
        return id;
    };
    for (auto ins : code) {
        const Word op = ins[0] & 65535;
        if (op == 65 && ins.size() == 5) {
            const auto result = result_types.find(ins[3]);
            if (result != result_types.end()) {
                const auto ptr = pointers.find(result->second);
                const auto c = constant_ids.find(ins[4]);
                if (ptr != pointers.end() && ptr->second.storage == 9 && c != constant_ids.end()) {
                    const Word structure = ptr->second.pointee;
                    const auto size = structure_sizes.find(structure);
                    const auto member = offsets.find({structure, c->second.value});
                    if (size != structure_sizes.end() && c->second.value >= size->second && member != offsets.end()) {
                        ins[4] = constant(c->second.type, member->second);
                        out.indices++;
                    }
                }
            }
        }
        if (op == 61 || op == 62) {
            const Word pointer = ins[op == 61 ? 3 : 1];
            const auto result = result_types.find(pointer);
            if (result != result_types.end()) {
                const auto ptr = pointers.find(result->second);
                if (ptr != pointers.end() && ptr->second.storage == 5349) {
                    if (op == 62) {
                        const auto object_type = result_types.find(ins[2]);
                        if (object_type != result_types.end() && bool_types.count(object_type->second) &&
                            object_type->second != ptr->second.pointee) {
                            const Word one = constant(ptr->second.pointee, 1);
                            const Word zero = constant(ptr->second.pointee, 0);
                            const Word id = bound++;
                            amended.push_back({(6u << 16) | 169u, ptr->second.pointee, id, ins[2], one, zero});
                            ins[2] = id;
                            out.boolean_stores++;
                        }
                    }
                    const size_t mask = op == 61 ? 4 : 3;
                    if (ins.size() == mask) {
                        ins.push_back(2); ins.push_back(4);
                        out.alignments++;
                    } else if (!(ins[mask] & 2)) {
                        ins[mask] |= 2;
                        ins.insert(ins.begin() + mask + 1, 4);
                        out.alignments++;
                    }
                    ins[0] = (static_cast<Word>(ins.size()) << 16) | op;
                }
            }
        }
        amended.push_back(std::move(ins));
    }
    auto function = std::find_if(amended.begin(), amended.end(), [](const Instruction& ins) { return (ins[0] & 65535) == 54; });
    amended.insert(function, new_constants.begin(), new_constants.end());
    std::vector<Instruction> fixed;
    for (size_t i = 0; i < amended.size();) {
        if ((amended[i][0] & 65535) != 54) { fixed.push_back(std::move(amended[i++])); continue; }
        size_t end = i + 1;
        while (end < amended.size() && (amended[end][0] & 65535) != 56) end++;
        if (end == amended.size()) throw std::runtime_error("unterminated SPIR-V function");
        std::vector<Instruction> body, variables;
        for (size_t j = i; j <= end; j++) {
            auto& ins = amended[j];
            if ((ins[0] & 65535) == 59 && ins.size() >= 4 && ins[3] == 7) variables.push_back(std::move(ins));
            else body.push_back(std::move(ins));
        }
        const auto entry = std::find_if(body.begin(), body.end(), [](const Instruction& ins) { return (ins[0] & 65535) == 248; });
        if (entry == body.end()) throw std::runtime_error("SPIR-V function lacks an entry block");
        body.insert(entry + 1, variables.begin(), variables.end());
        for (auto& ins : body) fixed.push_back(std::move(ins));
        i = end + 1;
    }
    out.words.assign(words, words + 5);
    out.words[3] = bound;
    for (const auto& ins : fixed) out.words.insert(out.words.end(), ins.begin(), ins.end());
    return out;
}
} // namespace fallback_repair
