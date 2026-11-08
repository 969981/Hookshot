"""A compute program prepared before the game finds its storage buffer table where the bound program would have it.

NVN2 compute keeps its storage buffer table in c0 after the push area and the uniform buffer table, as many slots as its
control section says (vk_bank.cpp cs_layout, read from the bound program's control section). The shader precompile
(seed_program) translates with no program bound, so cs_layout said nothing, the translator fell back to Maxwell's 0x310,
where NVN2's c0 holds nothing, and the program cache gave the game that translation from then on (compute programs have
no variants). OoT's foliage culling then left some of its draw lists empty: the plants near Link had no leaves, while a
run without the program cache, where every program is translated in play, drew them (issue 590).

parse_code now reads the same control bytes for every compute program it parses, the seed's included, and the compute
translation takes that table when no program is bound. This checks that both read the same bytes with the same arithmetic
(over random control sections), and that the translation uses the program's own table only when the state has none.
python -I -B tests/test_compute_table_precompile.py
"""
import random
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BANK = ROOT / "gpu" / "vk" / "vk_bank.cpp"
SHADER = ROOT / "gpu" / "vk" / "vk_shader.cpp"


def block(text, marker):
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def c_expr(expr):
    """A C integer expression as Python: unsigned suffixes and casts dropped"""
    expr = re.sub(r"\(\s*uint32_t\s*\)", "", expr)
    expr = re.sub(r"\b(0x[0-9a-fA-F]+|\d+)[uU]\b", r"\1", expr)
    return expr.strip()


def bank_layout(bank):
    """vk_bank.cpp's CsLayout: which control byte each count comes from, and its offsets"""
    layout = block(bank, "struct CsLayout")
    methods = dict(re.findall(r"uint32_t (\w+)\(\) const \{ return ([^;]+); \}", layout))
    for name in ("ubo_at", "ssbo_at", "tex_at", "img_at", "end"):
        assert name in methods, "CsLayout::%s() not found" % name
    reader = block(bank, "CsLayout cs_layout(const NvnState& s)")
    fields = {}
    for field, offset in re.findall(r"l\.(ubo|ssbo|tex|img) = c\[(0x[0-9a-f]+) - 0x58\]", reader):
        fields[field] = int(offset, 16)
    m = re.search(r"l\.push = (.+?);", reader)
    assert m and len(fields) == 4, "cs_layout no longer reads the counts as this test expects"
    push = c_expr(m.group(1))
    push_byte = re.search(r"c\[(0x[0-9a-f]+) - 0x58\]", push)
    assert push_byte, push
    fields["push_byte"] = int(push_byte.group(1), 16)
    push = push.replace(push_byte.group(0), "push_word")
    return fields, push, {k: c_expr(v) for k, v in methods.items()}


def evaluate_bank(fields, push_expr, methods, ctl):
    names = {"push_word": ctl[fields["push_byte"]]}
    names["push"] = eval(push_expr, {"__builtins__": {}}, names)  # noqa: S307 (arithmetic on names)
    for k in ("ubo", "ssbo", "tex", "img"):
        names[k] = ctl[fields[k]]
    out = {}
    for name in ("ubo_at", "ssbo_at", "tex_at", "img_at", "end"):
        expr = re.sub(r"(\w+)\(\)", lambda m: str(out[m.group(1)]), methods[name])
        out[name] = eval(expr, {"__builtins__": {}}, names)  # noqa: S307
    return out


def code_layout(shader):
    """vk_shader.cpp parse_code: the compute stage's table, as Code's cs_ssbo_at, cs_ssbo and cs_end"""
    parse = block(shader, "Code parse_code(const uint8_t* ctl, const std::vector<uint8_t>& area) {")
    m = re.search(r"const uint32_t push = (.+?);", parse)
    assert m, "parse_code does not read the push area"
    push = c_expr(m.group(1))
    reads = dict(re.findall(r"(ubo|ssbo|tex|img) = ctl\[(0x[0-9a-f]+)\]", parse))
    assert len(reads) == 4, "parse_code does not read the four counts: %r" % reads
    assignments = dict(re.findall(r"c\.(cs_ssbo_at|cs_ssbo|cs_end) = (.+?);", parse))
    assert len(assignments) == 3, "parse_code does not set cs_ssbo_at, cs_ssbo and cs_end"
    assert parse.index("cs_ssbo_at") > parse.index("if (!graphics)"), "the table is read for graphics stages too"
    return push, {k: int(v, 16) for k, v in reads.items()}, {k: c_expr(v) for k, v in assignments.items()}


def evaluate_code(push_expr, reads, assignments, ctl):
    names = {"ctl": ctl}
    names["push"] = eval(push_expr, {"__builtins__": {}}, names)  # noqa: S307
    for k, off in reads.items():
        names[k] = ctl[off]
    out = {}
    for name in ("cs_ssbo_at", "cs_ssbo", "cs_end"):
        expr = assignments[name].replace("c.cs_ssbo_at", "cs_ssbo_at")
        out[name] = eval(expr, {"__builtins__": {}}, {**names, **out})  # noqa: S307
    return out


def check_same_table(bank, shader):
    fields, push, methods = bank_layout(bank)
    code_push, reads, assignments = code_layout(shader)
    for k in ("ubo", "ssbo", "tex", "img"):
        assert reads[k] == fields[k], "parse_code reads %s at %#x, cs_layout at %#x" % (k, reads[k], fields[k])
    assert "ctl[%#x]" % fields["push_byte"] in code_push, "parse_code reads the push size elsewhere than cs_layout"
    rng = random.Random(590)
    for _ in range(2000):
        ctl = bytes(rng.randrange(256) if rng.random() < 0.5 else rng.randrange(8) for _ in range(0xd8))
        want = evaluate_bank(fields, push, methods, ctl)
        got = evaluate_code(code_push, reads, assignments, ctl)
        assert got["cs_ssbo_at"] == want["ssbo_at"], (got, want)
        assert got["cs_ssbo"] == ctl[fields["ssbo"]], (got, want)
        assert got["cs_end"] == want["end"], (got, want)


def check_translation_uses_it(shader):
    body = block(shader, "void translate_into(Program& p, const NvnState& s")
    at = body.index("compute_ssbo_table(s, &count, &end)")
    use = re.search(r"if \(([^)]*!count[^)]*)\)\s*base = csc->cs_ssbo_at, count = csc->cs_ssbo, end = csc->cs_end;",
                    body[at:])
    assert use, "the compute translation does not take the program's own table"
    cond = use.group(1)
    assert "!s.prog.nstage" in cond, "the program's own table must stand in only when no program is bound: %s" % cond
    assert "csc->cs_ssbo" in cond, "a program with no storage buffers keeps the old path: %s" % cond
    base_set = body.index("g_nvn2_compute_ssbo_base = count ? base : 0", at)
    assert at + use.start() < base_set, "the table is chosen after the translator was told"
    assert "RX_VK_CS_TABLE_SEED" in body[at:base_set], "no switch to turn it off"


def main():
    bank = BANK.read_text(encoding="utf-8")
    shader = SHADER.read_text(encoding="utf-8")
    check_same_table(bank, shader)
    check_translation_uses_it(shader)
    print("compute table for the precompile: OK")


if __name__ == "__main__":
    main()
