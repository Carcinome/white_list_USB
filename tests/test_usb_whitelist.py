"""Tests de la logique pure (parsing, construction de règle).

Ils ne lancent jamais usbguard : on peut les exécuter n'importe où.
    python -m unittest discover -s tests -v
"""
import io
import os
import re
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import usb_whitelist as uw  # noqa: E402

DEVICE_LINE = (
    '7: block id 0781:5581 serial "4C530001" name "Ultra USB 3.0" '
    'hash "Xy/abc+1=" parent-hash "Pq9=" via-port "2-1" '
    'with-interface { 08:06:50 } with-connect-type "hotplug"'
)
SAMPLE = (
    '1: allow id 1d6b:0002 serial "0000:00:14.0" name "xHCI Host Controller" '
    'hash "h1" parent-hash "p1" via-port "usb1" with-interface 09:00:00 '
    'with-connect-type ""\n'
    + DEVICE_LINE + "\n"
    '8: allow id 046d:c31c serial "" name "USB Keyboard" hash "h3" '
    'parent-hash "p3" via-port "1-2" with-interface { 03:01:01 03:00:00 } '
    'with-connect-type "hotplug"\n'
)


class ParseTests(unittest.TestCase):
    def test_champs_de_base(self):
        e = uw.parse_line(DEVICE_LINE)
        self.assertEqual((e.num, e.target), (7, "block"))
        self.assertEqual(e.vid_pid, "0781:5581")
        self.assertEqual(e.serial, "4C530001")
        self.assertEqual(e.name, "Ultra USB 3.0")
        self.assertEqual(e.hash, "Xy/abc+1=")
        self.assertEqual(e.interfaces, ["08:06:50"])
        self.assertTrue(e.is_storage)

    def test_interface_simple_sans_accolades(self):
        e = uw.parse_output(SAMPLE)[0]
        self.assertEqual(e.interfaces, ["09:00:00"])
        self.assertTrue(e.is_root_hub)

    def test_plusieurs_interfaces_clavier(self):
        e = uw.parse_output(SAMPLE)[2]
        self.assertEqual(e.interfaces, ["03:01:01", "03:00:00"])
        self.assertEqual(e.serial, "")
        self.assertFalse(e.is_storage)
        self.assertIn("HID", e.kind)

    def test_operateur_with_interface(self):
        e = uw.parse_line('3: allow id 1234:5678 with-interface all-of { 08:*:* }')
        self.assertEqual(e.interfaces, ["08:*:*"])

    def test_guillemets_echappes_dans_le_nom(self):
        e = uw.parse_line(r'4: block id 1234:5678 name "Cle \"pro\"" hash "h"')
        self.assertEqual(e.name, 'Cle "pro"')

    def test_valeur_qui_ressemble_a_un_mot_cle(self):
        # Le nom vaut littéralement "id" : il ne doit pas être pris pour un champ.
        e = uw.parse_line('5: block id 1234:5678 name "id" hash "h"')
        self.assertEqual((e.name, e.vid_pid), ("id", "1234:5678"))

    def test_ligne_invalide(self):
        self.assertIsNone(uw.parse_line("pas une ligne usbguard"))
        self.assertEqual(len(uw.parse_output(SAMPLE)), 3)


class RuleTests(unittest.TestCase):
    def test_regle_sans_port(self):
        rule = uw.build_allow_rule(uw.parse_line(DEVICE_LINE))
        self.assertEqual(
            rule,
            'allow id 0781:5581 serial "4C530001" name "Ultra USB 3.0" '
            'hash "Xy/abc+1="',
        )
        self.assertNotIn("via-port", rule)

    def test_regle_relue_identique(self):
        dev = uw.parse_line(DEVICE_LINE)
        back = uw.parse_line("9: " + uw.build_allow_rule(dev))
        self.assertTrue(uw.same_device(dev, back))

    def test_injection_via_nom_impossible(self):
        dev = uw.parse_line(DEVICE_LINE)
        dev.name = 'x" allow id *:* hash "'
        rule = uw.build_allow_rule(dev)
        back = uw.parse_line("9: " + rule)
        self.assertEqual(back.vid_pid, "0781:5581")   # pas écrasé
        self.assertEqual(back.name, dev.name)          # tout est resté dans le nom

    def test_refus_sans_hash(self):
        dev = uw.parse_line(DEVICE_LINE)
        dev.hash = ""
        with self.assertRaises(uw.UsbGuardError):
            uw.build_allow_rule(dev)

    def test_refus_id_invalide(self):
        dev = uw.parse_line(DEVICE_LINE)
        dev.vid_pid = "*:*"
        with self.assertRaises(uw.UsbGuardError):
            uw.build_allow_rule(dev)

    def test_refus_caractere_de_controle(self):
        dev = uw.parse_line(DEVICE_LINE)
        dev.serial = "ab\ncd"
        with self.assertRaises(uw.UsbGuardError):
            uw.build_allow_rule(dev)


class TranslationTests(unittest.TestCase):
    """FR : le catalogue fr/en doit rester synchronisé.
    EN : the fr/en catalog must stay in sync.
    """

    def tearDown(self):
        uw.set_language("fr")

    def test_memes_cles(self):
        fr, en = set(uw.MESSAGES["fr"]), set(uw.MESSAGES["en"])
        self.assertEqual(fr - en, set(), "missing in en")
        self.assertEqual(en - fr, set(), "missing in fr")

    def test_memes_trous(self):
        # FR : {nom} doit exister dans les deux langues, sinon format() plante.
        # EN : {name} must exist in both languages, otherwise format() fails.
        hole = re.compile(r"\{(\w+)\}")
        for key in uw.MESSAGES["fr"]:
            self.assertEqual(
                set(hole.findall(uw.MESSAGES["fr"][key])),
                set(hole.findall(uw.MESSAGES["en"][key])),
                key,
            )

    def test_aucun_texte_vide(self):
        for lang in uw.LANGUAGES:
            for key, text in uw.MESSAGES[lang].items():
                self.assertTrue(text.strip(), f"{lang}.{key}")

    def test_changement_de_langue(self):
        uw.set_language("en")
        self.assertEqual(uw.t("cancelled"), "Cancelled.")
        uw.set_language("fr")
        self.assertEqual(uw.t("cancelled"), "Annulé.")

    def test_langue_inconnue_retombe_sur_francais(self):
        uw.set_language("de")
        self.assertEqual(uw.t("cancelled"), "Annulé.")

    def test_type_de_peripherique_traduit(self):
        dev = uw.parse_line(DEVICE_LINE)
        uw.set_language("fr")
        self.assertIn("stockage", dev.kind)
        uw.set_language("en")
        self.assertIn("storage", dev.kind)

    def test_menu_de_langue(self):
        with mock.patch("builtins.input", return_value="2"),                 redirect_stdout(io.StringIO()):
            uw.choose_language()
        self.assertEqual(uw.t("cancelled"), "Cancelled.")
        with mock.patch("builtins.input", return_value="1"),                 redirect_stdout(io.StringIO()):
            uw.choose_language()
        self.assertEqual(uw.t("cancelled"), "Annulé.")

    def test_option_lang_prioritaire_sur_environnement(self):
        with mock.patch.dict(os.environ, {"USB_WHITELIST_LANG": "fr"}):
            self.assertEqual(uw._initial_language(["--lang", "en", "list"]), "en")
            self.assertEqual(uw._initial_language(["list"]), "fr")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(uw._initial_language(["list"]))


if __name__ == "__main__":
    unittest.main()
