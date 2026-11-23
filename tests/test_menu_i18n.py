"""gpu/vk/menu_i18n.h, the Insert menu's translations: every table's texts are the menu's own (vk_menu.cpp writes each
English text), every translation keeps its text's printf conversions in order and its leading and trailing spaces (the menu
joins pieces), and the language list holds what vk_menu_i18n.cpp expects. The controls and Automatic prompt hints
must remain present in every translated table. No game, no compiler.

A game's own texts (its menu table's, docs/game-packs.md a4) are translated in its pack: packs/<key>/menu/menu_i18n.h, a table
a menu language by its code, each English text one of the pack's menu/ C, held to the same rules; and no text of the game's
is left in the renderer's tables (Ocarina's tools, places and words moved there whole).

Run with: python -I tests/test_menu_i18n.py
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
VK = ROOT / "gpu" / "vk"


def c_strings(text):
    """The C string literals of a source, adjacent ones joined, escapes as written"""
    text = re.sub(r'//[^\n]*', '', text)
    text = re.sub(r'"\s*\n\s*"', '', text)
    return re.findall(r'"((?:[^"\\\n]|\\.)*)"', text)


def conversions(s):
    """The printf conversions of s in order (as vk_menu_i18n.cpp conversion() reads them; not ' ': "80% suits" is a sign)"""
    return re.findall(r'%[-+#0]*\d*(?:\.\d+)?[lhz]*[sducxgf%]', s)


def c_source_strings(text):
    """A C file's string literals, adjacent ones joined: /* */ comments out too"""
    return c_strings(re.sub(r'/\*.*?\*/', '', text, flags=re.S))


def pack_tables(pack):
    """A pack's menu translations (packs/<key>/menu/menu_i18n.h): {table: pairs}, and its languages [(code, table)]"""
    src = (pack / "menu" / "menu_i18n.h").read_text(encoding="utf-8")
    out = {}
    for name, body in re.findall(r'static const NX2MenuEntry (\w+)\[\] = \{\n(.*?)\n\};', src, re.S):
        out[name] = re.findall(r'^    \{"((?:[^"\\]|\\.)*)", "((?:[^"\\]|\\.)*)"\},$', body, re.M)
        assert len(out[name]) == body.count("\n") + 1, "%s: a line that is not one entry" % name
    langs = re.findall(r'^    \{"([\w-]+)", (\w+), \(int\)\(sizeof \2 / sizeof \2\[0\]\)\},$', src, re.M)
    return out, langs


def pack_texts(pack):
    texts = set()
    for f in sorted((pack / "menu").glob("*.[ch]")):
        if f.name != "menu_i18n.h":
            texts |= set(c_source_strings(f.read_text(encoding="utf-8")))
    return texts


def tables():
    src = (VK / "menu_i18n.h").read_text(encoding="utf-8")
    out = {}
    for name, body in re.findall(r'inline constexpr Entry (k\w+)\[\] = \{\n(.*?)\n\};', src, re.S):
        pairs = re.findall(r'^    \{"((?:[^"\\]|\\.)*)", "((?:[^"\\]|\\.)*)"\},$', body, re.M)
        out[name] = pairs
    langs = re.findall(r'^    \{"([\w-]+)", "((?:[^"\\]|\\.)*)", (\w+), (\d+)\},$', src, re.M)
    return out, langs


class MenuI18nTests(unittest.TestCase):
    def setUp(self):
        self.tables, self.langs = tables()
        menu = (VK / "vk_menu.cpp").read_text(encoding="utf-8")
        self.menu = set(c_strings(menu))

    def test_languages(self):
        self.assertEqual(self.langs[0][:3], ("en", "English", "nullptr"))
        codes = [code for code, _, _, _ in self.langs]
        self.assertEqual(len(codes), len(set(codes)), "a language listed twice")
        for code, _, table, count in self.langs[1:]:
            self.assertIn(table, self.tables, code)
            self.assertEqual(len(self.tables[table]), int(count), "%s: its count is not its table's" % code)

    def test_texts_are_the_menus(self):
        for table, pairs in self.tables.items():
            for en, _ in pairs:
                self.assertTrue(en in self.menu, "%s: %r is no text of vk_menu.cpp (renamed? then translate it again)" % (table, en))

    def test_current_hints_are_translated(self):
        # Whole-string lookup needs the complete hint. Dropping a stale entry and lowering its count is not a fix.
        hints = {
            "controls": "Gamepad: the D-pad moves, A binds, B goes back.",   # (the renderer's; Ocarina's own is its pack's)
            "automatic button prompts": (
                "Those of the first controller, from its type and name (SDL's). With none, the keyboard's (the Switch's with XInput "
                "alone)."
            ),
            # (game packs a4: the general wording of the rows that named Ocarina, which a game without its own words shows)
            "Game frame rate cap": (
                "Limits frames rendered by the game. Generated frames are additional. Changes at once. 30 reduces rendering work "
                "and keeps real time even with Game speed set to Per frame. Past 60 is experimental: some cutscenes and bosses can "
                "misbehave. 60 is the safe choice. Uncapped: as fast as the PC can, still at real time; with V-Sync on, the "
                "display's refresh rate is the limit."
            ),
            "Microphone": "What the game hears when it listens to the microphone.",
            "Text size": "How large the text of dialogue and signs is drawn.",
            "Skip opening": "Skips the new-game opening and leaves later cutscenes and the title screen alone.",
            "Auto run": (
                "Sprint without holding A (or its key). Hold it until the sprint starts, then let go: it keeps going. Let go of the "
                "stick or the movement keys, or tap A again, to stop."
            ),
            "Debug tools": "The game's testing tools, below while this is on.",
            "Damage taken": (
                "How much health a hit takes. Normal is unchanged. Changes apply at once; healing is unchanged. Debug tools Off "
                "makes it Normal until they are on again."
            ),
        }
        for name, hint in hints.items():
            self.assertTrue(hint in self.menu, "%s: expected complete hint is no text of vk_menu.cpp" % name)
        for table, pairs in self.tables.items():
            keys = {en for en, _ in pairs}
            for name, hint in hints.items():
                self.assertTrue(hint in keys, "%s: missing current %s hint" % (table, name))

    def test_pack_tables(self):
        """Ocarina's pack: a table for each translated menu language, sorted, each text one of its menu/ C's, its controls
        hint whole in every one, and none of its texts (its own words) left in the renderer's tables"""
        pack = ROOT / "packs" / "oot"
        tables, langs = pack_tables(pack)
        texts = pack_texts(pack)
        codes = [code for code, _, _, _ in self.langs[1:]]
        self.assertEqual([code for code, _ in langs], codes, "the pack's languages are not the menu's, in its order")
        self.assertEqual(sorted(t for _, t in langs), sorted(tables), "a pack table listed twice or not at all")
        hint = ("Gamepad: the D-pad moves, A binds, B goes back. Ocarina note keys are only notes while the ocarina waits for one; "
                "away from it they do their other jobs.")
        self.assertIn(hint, texts)
        self.assertNotIn(hint, self.menu)
        own = {"Heart protection", "No-clip flight", "Switch age", "Teleport", "Inventory", "Overworld", "adult only",
               "Camera & world", "Choose map", "Edit items"}
        for table, pairs in tables.items():
            keys = [en for en, _ in pairs]
            self.assertEqual(keys, sorted(keys), "%s: not sorted" % table)
            self.assertEqual(len(keys), len(set(keys)), "%s: a text twice" % table)
            self.assertIn(hint, keys, "%s: missing Ocarina's controls hint" % table)
            for en, text in pairs:
                self.assertTrue(en in texts, "%s: %r is no text of packs/oot/menu/" % (table, en))
                self.assertTrue(text, "%s: %r has an empty translation" % (table, en))
                self.assertEqual(conversions(en), conversions(text), "%s: %r and %r" % (table, en, text))
        for table, pairs in self.tables.items():
            for en, _ in pairs:
                self.assertFalse(en in own or "Ocarina" in en or "Link" in en, "%s: %r is Ocarina's, in the renderer's table" % (table, en))

    def test_plural_forms(self):
        # (i18n::plural: Russian and Polish ask for a third form, " settings (many)", for 5 and up; the menu shows it
        # nowhere else, so only their tables have it)
        cpp = (VK / "vk_menu_i18n.cpp").read_text(encoding="utf-8")
        for table, code in (("kru", "ru"), ("kpl", "pl")):
            self.assertIn('code == "%s"' % code, cpp)
            self.assertIn(" settings (many)", dict(self.tables[table]), table)
            self.assertIn(" settings", dict(self.tables[table]), table)

    def test_conversions_and_spaces(self):
        for table, pairs in self.tables.items():
            keys = [en for en, _ in pairs]
            self.assertEqual(len(keys), len(set(keys)), "%s: a text twice" % table)
            self.assertEqual(keys, sorted(keys), "%s: not sorted (menu_i18n.h is written sorted)" % table)
            for en, text in pairs:
                self.assertTrue(text, "%s: %r has an empty translation" % (table, en))
                self.assertEqual(conversions(en), conversions(text), "%s: %r and %r" % (table, en, text))
                for side in (lambda s: s[:len(s) - len(s.lstrip(" "))], lambda s: s[len(s.rstrip(" ")):]):
                    self.assertEqual(side(en), side(text), "%s: the spaces around %r" % (table, en))


if __name__ == "__main__":
    unittest.main()
