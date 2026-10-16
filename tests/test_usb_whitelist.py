"""Tests de la logique pure de usb_whitelist.py.
Tests for the pure logic of usb_whitelist.py.

FR : Ces tests ne lancent JAMAIS usbguard et ne touchent pas au système : on
     peut les exécuter n'importe où (même sous Windows). Les parties Unix
     (groupes, utilisateurs) sont simulées avec des faux modules.
EN : These tests NEVER run usbguard and never touch the system: they can run
     anywhere (even on Windows). The Unix parts (groups, users) are simulated
     with fake modules.

    python -m unittest discover -s tests -v
"""
import io
import os
import re
import sys
import types
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import usb_whitelist as uw  # noqa: E402

# FR : Une vraie ligne de `usbguard list-devices` (modèle de nos tests).
# EN : A realistic `usbguard list-devices` line (the model for our tests).
DEVICE_LINE = (
    '7: block id 0781:5581 serial "4C530001" name "Ultra USB 3.0" '
    'hash "Xy/abc+1=" parent-hash "Pq9=" via-port "2-1" '
    'with-interface { 08:06:50 } with-connect-type "hotplug"'
)
# FR : Trois lignes : un hub racine, une clé, un clavier (2 interfaces).
# EN : Three lines: a root hub, a key, a keyboard (2 interfaces).
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
    """Lecture des lignes usbguard. / Reading usbguard lines."""

    def test_champs_de_base(self):
        """Tous les champs utiles sont extraits.
        All useful fields are extracted.
        """
        e = uw.parse_line(DEVICE_LINE)
        self.assertEqual((e.num, e.target), (7, "block"))
        self.assertEqual(e.vid_pid, "0781:5581")
        self.assertEqual(e.serial, "4C530001")
        self.assertEqual(e.name, "Ultra USB 3.0")
        self.assertEqual(e.hash, "Xy/abc+1=")
        self.assertEqual(e.interfaces, ["08:06:50"])
        self.assertTrue(e.is_storage)

    def test_interface_simple_sans_accolades(self):
        """`with-interface 09:00:00` (sans accolades) est compris.
        `with-interface 09:00:00` (no braces) is understood.
        """
        e = uw.parse_output(SAMPLE)[0]
        self.assertEqual(e.interfaces, ["09:00:00"])
        self.assertTrue(e.is_root_hub)

    def test_plusieurs_interfaces_clavier(self):
        """Un clavier déclare plusieurs interfaces ; ce n'est pas du stockage.
        A keyboard declares several interfaces; it is not storage.
        """
        e = uw.parse_output(SAMPLE)[2]
        self.assertEqual(e.interfaces, ["03:01:01", "03:00:00"])
        self.assertEqual(e.serial, "")
        self.assertFalse(e.is_storage)
        uw.set_language("fr")
        self.assertIn("HID", e.kind)

    def test_operateur_with_interface(self):
        """Un opérateur (all-of, one-of...) devant les accolades est ignoré.
        An operator (all-of, one-of...) before the braces is skipped.
        """
        e = uw.parse_line('3: allow id 1234:5678 with-interface all-of { 08:*:* }')
        self.assertEqual(e.interfaces, ["08:*:*"])

    def test_guillemets_echappes_dans_le_nom(self):
        """Un nom contenant des guillemets échappés est relu tel quel.
        A name containing escaped quotes is read back unchanged.
        """
        e = uw.parse_line(r'4: block id 1234:5678 name "Cle \"pro\"" hash "h"')
        self.assertEqual(e.name, 'Cle "pro"')

    def test_valeur_qui_ressemble_a_un_mot_cle(self):
        """Un nom valant littéralement "id" n'est pas pris pour le champ id.
        A name that is literally "id" is not mistaken for the id field.
        """
        e = uw.parse_line('5: block id 1234:5678 name "id" hash "h"')
        self.assertEqual((e.name, e.vid_pid), ("id", "1234:5678"))

    def test_ligne_invalide(self):
        """Une ligne qui n'est pas du usbguard est ignorée, sans erreur.
        A line that is not usbguard output is ignored, without error.
        """
        self.assertIsNone(uw.parse_line("pas une ligne usbguard"))
        self.assertEqual(len(uw.parse_output(SAMPLE)), 3)


class RuleTests(unittest.TestCase):
    """Construction des règles `allow`. / Building `allow` rules."""

    def setUp(self):
        uw.set_language("fr")

    def test_regle_sans_port(self):
        """La règle contient id/série/nom/empreinte, et PAS de via-port.
        The rule holds id/serial/name/hash, and NO via-port.
        """
        rule = uw.build_allow_rule(uw.parse_line(DEVICE_LINE))
        self.assertEqual(
            rule,
            'allow id 0781:5581 serial "4C530001" name "Ultra USB 3.0" '
            'hash "Xy/abc+1="',
        )
        self.assertNotIn("via-port", rule)

    def test_regle_relue_identique(self):
        """Écrire puis relire une règle redonne le même matériel.
        Writing then re-reading a rule gives back the same hardware.
        """
        dev = uw.parse_line(DEVICE_LINE)
        back = uw.parse_line("9: " + uw.build_allow_rule(dev))
        self.assertTrue(uw.same_device(dev, back))

    def test_injection_via_nom_impossible(self):
        """Un nom piégé ne peut pas fabriquer une 2e règle (ex. tout autoriser).
        A booby-trapped name cannot forge a second rule (e.g. allow all).
        """
        dev = uw.parse_line(DEVICE_LINE)
        dev.name = 'x" allow id *:* hash "'
        rule = uw.build_allow_rule(dev)
        back = uw.parse_line("9: " + rule)
        self.assertEqual(back.vid_pid, "0781:5581")   # pas écrasé / not overwritten
        self.assertEqual(back.name, dev.name)          # tout reste dans le nom / all stays in the name

    def test_refus_sans_hash(self):
        """Sans empreinte, on refuse : la règle serait trop permissive.
        Without a fingerprint we refuse: the rule would be too permissive.
        """
        dev = uw.parse_line(DEVICE_LINE)
        dev.hash = ""
        with self.assertRaises(uw.UsbGuardError):
            uw.build_allow_rule(dev)

    def test_refus_id_invalide(self):
        """Un id joker (*:*) est refusé. A wildcard id (*:*) is refused."""
        dev = uw.parse_line(DEVICE_LINE)
        dev.vid_pid = "*:*"
        with self.assertRaises(uw.UsbGuardError):
            uw.build_allow_rule(dev)

    def test_refus_caractere_de_controle(self):
        """Un retour à la ligne dans une valeur est refusé.
        A newline inside a value is refused.
        """
        dev = uw.parse_line(DEVICE_LINE)
        dev.serial = "ab\ncd"
        with self.assertRaises(uw.UsbGuardError):
            uw.build_allow_rule(dev)


class WheelTests(unittest.TestCase):
    """Contrôle d'appartenance à wheel (la fonction de sécurité clé).
    wheel membership check (the key security function).

    FR : On simule l'OS : faux modules grp/pwd et fausses fonctions os.*.
         Les nombres sont des identifiants de groupe (gid) : 10 = wheel.
    EN : We simulate the OS: fake grp/pwd modules and fake os.* functions.
         Numbers are group ids (gid): 10 = wheel.
    """

    def check(self, *, euid, wheel_gid=10, egid=1000, groups=(), env=None,
              user_groups=()):
        def getgrnam(name):
            if wheel_gid is None:
                raise KeyError(name)
            return types.SimpleNamespace(gr_gid=wheel_gid)

        fake_grp = types.SimpleNamespace(getgrnam=getgrnam)
        fake_pwd = types.SimpleNamespace(
            getpwnam=lambda n: types.SimpleNamespace(pw_gid=1000))
        with mock.patch.dict(sys.modules, {"grp": fake_grp, "pwd": fake_pwd}), \
                mock.patch.dict(os.environ, env or {}, clear=True), \
                mock.patch.object(os, "geteuid", lambda: euid, create=True), \
                mock.patch.object(os, "getegid", lambda: egid, create=True), \
                mock.patch.object(os, "getgroups", lambda: list(groups), create=True), \
                mock.patch.object(os, "getgrouplist",
                                  lambda u, g: list(user_groups), create=True):
            return uw.is_wheel_member()

    def test_utilisateur_dans_wheel(self):
        """Utilisateur normal, wheel parmi ses groupes : accepté.
        Normal user with wheel among its groups: accepted.
        """
        self.assertTrue(self.check(euid=1000, groups=[1000, 10]))

    def test_utilisateur_hors_wheel(self):
        """Utilisateur normal sans wheel : refusé. Normal user without wheel: refused."""
        self.assertFalse(self.check(euid=1000, groups=[1000, 5]))

    def test_groupe_principal_wheel(self):
        """wheel comme groupe principal compte aussi.
        wheel as the primary group counts too.
        """
        self.assertTrue(self.check(euid=1000, egid=10, groups=[]))

    def test_sudo_depuis_wheel(self):
        """root via sudo, l'appelant d'origine est dans wheel : accepté.
        root via sudo, the original caller is in wheel: accepted.
        """
        self.assertTrue(self.check(euid=0, env={"SUDO_USER": "alice"},
                                   user_groups=[1000, 10]))

    def test_sudo_hors_wheel(self):
        """root via sudo, mais l'appelant d'origine n'est PAS dans wheel : refusé.
        root via sudo, but the original caller is NOT in wheel: refused.
        """
        self.assertFalse(self.check(euid=0, env={"SUDO_USER": "bob"},
                                    user_groups=[1000]))

    def test_root_direct_autorise_par_defaut(self):
        """Vraie session root (pas de SUDO_USER) : autorisée si ALLOW_ROOT.
        Genuine root session (no SUDO_USER): allowed if ALLOW_ROOT.
        """
        self.assertTrue(self.check(euid=0))
        with mock.patch.object(uw, "ALLOW_ROOT", False):
            self.assertFalse(self.check(euid=0))

    def test_pas_de_groupe_wheel(self):
        """Si le groupe wheel n'existe pas : on refuse par prudence.
        If the wheel group does not exist: refuse, to be safe.
        """
        self.assertFalse(self.check(euid=1000, wheel_gid=None, groups=[10]))

    def test_sudo_user_ignore_si_pas_root(self):
        """Un utilisateur normal ne gagne rien en forgeant SUDO_USER.
        A normal user gains nothing by forging SUDO_USER.
        """
        self.assertFalse(self.check(euid=1000, groups=[1000],
                                    env={"SUDO_USER": "alice"},
                                    user_groups=[10]))


# FR : Les VRAIES lignes relevées sur la VM (VMware, OL 9.6) : la règle de base
#      est liée au port "2-1", la souris est maintenant sur "1-1".
# EN : The REAL lines captured on the VM (VMware, OL 9.6): the baseline rule is
#      bound to port "2-1", the mouse now sits on "1-1".
VM_RULE_PORT = (
    '3: allow id 0e0f:0003 serial "" name "VMware Virtual USB Mouse" '
    'hash "F4BFfAiFQzjQxG8oQHVYMFiDF2/zhhARDrRSIj6oij8=" '
    'parent-hash "3QuFY0nRVqo+mgnIkqiXmmirRvxPDeP7P3mintodpys=" '
    'via-port "2-1" with-interface 03:01:02 with-connect-type "unknown"'
)
VM_DEVICE_MOVED = (
    '7: block id 0e0f:0003 serial "" name "VMware Virtual USB Mouse" '
    'hash "F4BFfAiFQzjQxG8oQHVYMFiDF2/zhhARDrRSIj6oij8=" '
    'parent-hash "3QuFY0nRVqo+mgnIkqiXmmirRvxPDeP7P3mintodpys=" '
    'via-port "1-1" with-interface 03:01:02 with-connect-type "unknown"'
)
VM_RULE_PORTABLE = VM_RULE_PORT.replace('3: ', '5: ').replace(' via-port "2-1"', '')


class PortBoundRuleTests(unittest.TestCase):
    """Le cas réel : une règle liée au port ne reconnaît plus le matériel.
    The real case: a port-bound rule no longer recognizes the hardware.
    """

    def setUp(self):
        uw.set_language("fr")

    def test_champs_port_et_parent(self):
        """via-port et parent-hash sont bien lus. via-port and parent-hash are read."""
        e = uw.parse_line(VM_RULE_PORT)
        self.assertEqual(e.via_port, "2-1")
        self.assertTrue(e.parent_hash.startswith("3QuFY0n"))
        self.assertEqual(uw.parse_line(VM_RULE_PORTABLE).via_port, "")

    def test_meme_materiel_mais_regle_qui_ne_couvre_plus(self):
        """Même matériel (same_device) mais port différent : non couvert.
        Same hardware (same_device) but different port: not covered.
        """
        rule, dev = uw.parse_line(VM_RULE_PORT), uw.parse_line(VM_DEVICE_MOVED)
        self.assertTrue(uw.same_device(rule, dev))
        self.assertFalse(uw.rule_covers(rule, dev))

    def test_regle_sans_port_couvre(self):
        """Une règle sans via-port couvre le périphérique où qu'il soit.
        A rule without via-port covers the device wherever it is.
        """
        self.assertTrue(uw.rule_covers(uw.parse_line(VM_RULE_PORTABLE),
                                       uw.parse_line(VM_DEVICE_MOVED)))

    def _run_add(self, rules, persist=True):
        """Exécute cmd_add avec un faux usbguard ; renvoie (appels, erreur).
        Runs cmd_add with a fake usbguard; returns (calls, error).

        FR : `persist=True` simule un usbguard qui écrit VRAIMENT la règle
             (elle apparaît ensuite dans list_rules) ; `False` simule un
             append-rule sans effet, comme l'ancien bug.
        EN : `persist=True` simulates a usbguard that REALLY writes the rule
             (it then shows up in list_rules); `False` simulates an
             ineffective append-rule, like the old bug.
        """
        calls, state = [], list(rules)
        dev = uw.parse_line(VM_DEVICE_MOVED)

        def fake_usbguard(*args):
            calls.append(args)
            if args[0] == "append-rule" and persist:
                state.append(uw.parse_line("99: " + args[1]))
            return ""

        error = None
        with mock.patch.object(uw, "list_devices", return_value=[dev]),                 mock.patch.object(uw, "list_rules", side_effect=lambda: list(state)),                 mock.patch.object(uw, "run_usbguard", side_effect=fake_usbguard),                 mock.patch.object(uw, "audit"),                 mock.patch("builtins.input", side_effect=[uw.t("yes_word"), "o"]),                 redirect_stdout(io.StringIO()) as out:
            try:
                uw.cmd_add(types.SimpleNamespace(number=7))
            except uw.UsbGuardError as exc:
                error = exc
        self.last_output = out.getvalue()
        return calls, error

    def test_add_ecrit_une_regle_si_seule_une_regle_liee_au_port(self):
        """LE BUG CORRIGÉ : une règle liée au port n'empêche plus l'écriture
        de la règle portable.
        THE FIXED BUG: a port-bound rule no longer prevents writing the
        portable rule.
        """
        calls, error = self._run_add([uw.parse_line(VM_RULE_PORT)])
        self.assertIsNone(error)
        verbs = [c[0] for c in calls]
        self.assertIn("append-rule", verbs)
        self.assertIn("allow-device", verbs)
        rule_text = [c for c in calls if c[0] == "append-rule"][0][1]
        self.assertNotIn("via-port", rule_text)
        self.assertIn("OK", self.last_output)

    def test_add_n_ecrit_pas_deux_fois_une_regle_portable(self):
        """Une règle portable existe déjà : pas de doublon, mais on débloque.
        A portable rule already exists: no duplicate, but we unblock.
        """
        calls, error = self._run_add([uw.parse_line(VM_RULE_PORTABLE)])
        self.assertIsNone(error)
        verbs = [c[0] for c in calls]
        self.assertNotIn("append-rule", verbs)
        self.assertIn("allow-device", verbs)

    def test_add_n_annonce_rien_si_la_regle_n_est_pas_ecrite(self):
        """LA LEÇON DU « définitivement » FAUX : si la règle n'apparaît pas
        après l'ajout, erreur ; aucun déblocage, aucun « OK ».
        THE LESSON OF THE FALSE "permanently": if the rule does not appear
        after adding, error; no unblocking, no "OK".
        """
        calls, error = self._run_add([uw.parse_line(VM_RULE_PORT)], persist=False)
        self.assertIsNotNone(error)
        verbs = [c[0] for c in calls]
        self.assertIn("append-rule", verbs)
        self.assertNotIn("allow-device", verbs)
        self.assertNotIn("OK", self.last_output)

    def _run_remove(self, before, after):
        calls = []
        allowed_dev = uw.parse_line(VM_DEVICE_MOVED.replace(": block", ": allow"))
        with mock.patch.object(uw, "list_devices", return_value=[allowed_dev]),                 mock.patch.object(uw, "list_rules", side_effect=[before, after]),                 mock.patch.object(uw, "run_usbguard",
                                  side_effect=lambda *a: calls.append(a) or ""),                 mock.patch.object(uw, "audit"),                 mock.patch("builtins.input", return_value="o"),                 redirect_stdout(io.StringIO()):
            uw.cmd_remove(types.SimpleNamespace(number=5))
        return [c[0] for c in calls]

    def test_remove_ne_coupe_pas_si_une_autre_regle_autorise(self):
        """LE 2e BUG CORRIGÉ : si une règle restante couvre encore le
        périphérique, on ne le bloque pas.
        THE 2nd FIXED BUG: if a remaining rule still covers the device, we do
        not block it.
        """
        dup = uw.parse_line(VM_RULE_PORTABLE.replace("5: ", "9: "))
        verbs = self._run_remove(before=[uw.parse_line(VM_RULE_PORTABLE), dup],
                                 after=[dup])
        self.assertIn("remove-rule", verbs)
        self.assertNotIn("block-device", verbs)

    def test_remove_bloque_si_plus_aucune_regle(self):
        """Plus aucune règle ne couvre : le périphérique est bloqué.
        No rule covers it any more: the device is blocked.
        """
        verbs = self._run_remove(before=[uw.parse_line(VM_RULE_PORTABLE)], after=[])
        self.assertIn("block-device", verbs)


class TranslationTests(unittest.TestCase):
    """Le catalogue fr/en doit rester synchronisé.
    The fr/en catalog must stay in sync.
    """

    def tearDown(self):
        uw.set_language("fr")

    def test_memes_cles(self):
        """Toute clé existe dans les deux langues. Every key exists in both languages."""
        fr, en = set(uw.MESSAGES["fr"]), set(uw.MESSAGES["en"])
        self.assertEqual(fr - en, set(), "missing in en")
        self.assertEqual(en - fr, set(), "missing in fr")

    def test_memes_trous(self):
        """Les {trous} sont identiques, sinon format() planterait.
        The {placeholders} are identical, otherwise format() would fail.
        """
        hole = re.compile(r"\{(\w+)\}")
        for key in uw.MESSAGES["fr"]:
            self.assertEqual(
                set(hole.findall(uw.MESSAGES["fr"][key])),
                set(hole.findall(uw.MESSAGES["en"][key])),
                key,
            )

    def test_aucun_texte_vide(self):
        """Aucune traduction oubliée (chaîne vide). No forgotten (empty) translation."""
        for lang in uw.LANGUAGES:
            for key, text in uw.MESSAGES[lang].items():
                self.assertTrue(text.strip(), f"{lang}.{key}")

    def test_changement_de_langue(self):
        """set_language() change bien le texte rendu.
        set_language() really changes the rendered text.
        """
        uw.set_language("en")
        self.assertEqual(uw.t("cancelled"), "Cancelled.")
        uw.set_language("fr")
        self.assertEqual(uw.t("cancelled"), "Annulé.")

    def test_langue_inconnue_retombe_sur_francais(self):
        """Langue inconnue : repli sur le français. Unknown language: fall back to French."""
        uw.set_language("de")
        self.assertEqual(uw.t("cancelled"), "Annulé.")

    def test_type_de_peripherique_traduit(self):
        """Le type affiché suit la langue. The displayed type follows the language."""
        dev = uw.parse_line(DEVICE_LINE)
        uw.set_language("fr")
        self.assertIn("stockage", dev.kind)
        uw.set_language("en")
        self.assertIn("storage", dev.kind)

    def test_menu_de_langue(self):
        """Le menu de langue : 2 = anglais, 1 = français.
        The language menu: 2 = English, 1 = French.
        """
        with mock.patch("builtins.input", return_value="2"), \
                redirect_stdout(io.StringIO()):
            uw.choose_language()
        self.assertEqual(uw.t("cancelled"), "Cancelled.")
        with mock.patch("builtins.input", return_value="1"), \
                redirect_stdout(io.StringIO()):
            uw.choose_language()
        self.assertEqual(uw.t("cancelled"), "Annulé.")

    def test_option_lang_prioritaire_sur_environnement(self):
        """--lang l'emporte sur USB_WHITELIST_LANG ; sans les deux : None.
        --lang beats USB_WHITELIST_LANG; with neither: None.
        """
        with mock.patch.dict(os.environ, {"USB_WHITELIST_LANG": "fr"}):
            self.assertEqual(uw._initial_language(["--lang", "en", "list"]), "en")
            self.assertEqual(uw._initial_language(["list"]), "fr")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(uw._initial_language(["list"]))

    def test_lang_accepte_avant_ou_apres_la_commande(self):
        """`--lang en list` et `list --lang en` sont tous deux valides.
        Both `--lang en list` and `list --lang en` are valid.
        """
        parser = uw.build_parser()
        for argv in (["--lang", "en", "list"], ["list", "--lang", "en"],
                     ["add", "--lang", "en", "7"]):
            parser.parse_args(argv)  # ne doit pas lever / must not raise
        self.assertEqual(uw._initial_language(["list", "--lang", "en"]), "en")


if __name__ == "__main__":
    unittest.main()
