import ast
import os
import string
import sys
import tempfile
import unittest

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, SRC)

import i18n  # noqa: E402
import json_loader  # noqa: E402
import main  # noqa: E402
import ui  # noqa: E402


def literal_keys():
    """Every string literal passed straight to t() in the sources."""
    keys = set()
    for name in ("main.py", "ui.py"):
        with open(os.path.join(SRC, name), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "t"
                    and node.args and isinstance(node.args[0], ast.Constant)):
                keys.add(node.args[0].value)
    return keys


def dynamic_keys():
    """Texts that reach t() through a variable."""
    keys = set(ui.WEEKDAYS) | set(ui.TAGS.values()) | set(ui.COLOR_NAMES.values()) | set(ui.BACKGROUNDS)
    keys |= {label for _, label, _ in ui.COUNT_LABELS}
    keys |= {text for items in (main.MENU_ITEMS, main.EDITOR_ITEMS, main.DOMAIN_ITEMS)
             for _, label, short in items for text in (label, short)}
    keys.add("more")  # the editor's "Enter on a domain" hint
    return keys


def fields(text):
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


class TranslationTest(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("en")

    def test_every_text_has_a_polish_translation(self):
        self.assertEqual(sorted((literal_keys() | dynamic_keys()) - set(i18n.PL)), [])

    def test_no_stale_polish_entries(self):
        self.assertEqual(sorted(set(i18n.PL) - literal_keys() - dynamic_keys()), [])

    def test_polish_keeps_placeholders(self):
        for en, pl in i18n.PL.items():
            with self.subTest(en=en):
                self.assertEqual(fields(en), fields(pl))

    def test_console_tags_keep_their_width(self):
        for tag in list(ui.TAGS.values()) + ["  OK  ", " FAIL ", " WARN "]:
            self.assertEqual(len(i18n.PL[tag]), len(tag), tag)

    def test_menu_shortcuts_fit_function_key_bar(self):
        for items in (main.MENU_ITEMS, main.EDITOR_ITEMS):
            width = ui.WIDTH // len(items) - 3  # key, cursor marker and a gap before the next key
            for _, _, short in items:
                self.assertLessEqual(max(len(short), len(i18n.PL[short])), width, short)

    def test_unknown_language_falls_back_to_english(self):
        i18n.set_language("de")
        self.assertEqual(i18n.t("{n} days left", n=3), "3 days left")
        i18n.set_language("pl")
        self.assertEqual(i18n.t("{n} days left", n=3), "zostało dni: 3")
        self.assertEqual(i18n.t("not translated"), "not translated")


class LanguageConfigTest(unittest.TestCase):
    def test_language_comes_from_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            json_loader.save_config({"general": {"language": "pl"}}, config_path=path)
            self.assertEqual(main.configured_language(path), "pl")
            self.assertIsNone(main.configured_language(os.path.join(tmp, "missing.json")))

    def test_validation_rejects_unknown_language(self):
        config = {"general": {"language": "de"}, "providers": {}, "domains": []}
        logger = main.logging.getLogger("xcert-test")
        logger.disabled = True
        self.assertFalse(main.validate_config(config, logger))
        config["general"]["language"] = "pl"
        self.assertTrue(main.validate_config(config, logger))


if __name__ == "__main__":
    unittest.main()
