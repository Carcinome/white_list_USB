#!/usr/bin/python3
"""usb-whitelist : gestion simple de la liste blanche USB (Oracle Linux 9).
usb-whitelist: simple USB whitelist management (Oracle Linux 9).

Principe / Principle
--------------------
FR : Ce script ne bloque rien lui-même : le travail est fait par le démon
     USBGuard (noyau Linux + service `usbguard`). Notre rôle est d'offrir une
     interface simple par-dessus la commande `usbguard`, et de vérifier que
     l'utilisateur fait bien partie du groupe `wheel`.
EN : This script blocks nothing by itself: the USBGuard daemon (Linux kernel +
     `usbguard` service) does the work. Our job is to provide a simple front
     end on top of the `usbguard` command, and to check that the user belongs
     to the `wheel` group.

FR : La liste blanche vit dans /etc/usbguard/rules.conf : elle appartient à la
     MACHINE, pas à un utilisateur. Elle vaut donc pour tous les comptes,
     passés, présents et futurs.
EN : The whitelist lives in /etc/usbguard/rules.conf: it belongs to the
     MACHINE, not to a user. It therefore applies to every account, past,
     present and future.

Langue / Language
-----------------
FR : Tous les textes affichés sont dans le catalogue MESSAGES (fr / en). Le
     code n'utilise que des clés : t("added_ok"). La langue se choisit :
       1. avec l'option --lang fr|en,
       2. sinon avec la variable d'environnement USB_WHITELIST_LANG,
       3. sinon, au lancement du menu interactif, via un petit menu.
EN : Every displayed text lives in the MESSAGES catalog (fr / en). The code
     only uses keys: t("added_ok"). The language is chosen:
       1. with the --lang fr|en option,
       2. otherwise with the USB_WHITELIST_LANG environment variable,
       3. otherwise, when the interactive menu starts, via a small menu.

Dépendances / Dependencies : standard library only (Python 3.9 on OL9).
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# --------------------------------------------------------------------------
# Constantes de configuration / Configuration constants
# --------------------------------------------------------------------------
# FR : Chemin absolu : on n'utilise pas $PATH, pour qu'on ne puisse pas
#      remplacer la commande par un faux "usbguard" placé dans un dossier de
#      l'utilisateur.
# EN : Absolute path: we do not rely on $PATH, so nobody can swap the command
#      for a fake "usbguard" dropped in a user-writable directory.
USBGUARD = "/usr/bin/usbguard"
SYSTEMCTL = "/usr/bin/systemctl"

WHEEL_GROUP = "wheel"
# FR : root n'est pas membre de wheel, mais il a de toute façon tous les
#      droits (il peut lancer `usbguard` directement). Mettre False pour
#      l'exclure aussi.
# EN : root is not a member of wheel, but it has every right anyway (it can
#      run `usbguard` directly). Set to False to exclude root as well.
ALLOW_ROOT = True

# FR : Identifiant "Linux Foundation" : ce sont les contrôleurs/hubs racines
#      virtuels du noyau. Ce ne sont pas des clés, on ne les affiche pas.
# EN : "Linux Foundation" vendor ID: these are the kernel's virtual root
#      hubs/controllers. They are not USB keys, so we hide them.
ROOT_HUB_VENDOR = "1d6b"

STORAGE_CLASS = "08"

# --------------------------------------------------------------------------
# Traductions / Translations
# --------------------------------------------------------------------------
# FR : {nom} est un trou rempli par t("clé", nom=valeur). Les deux langues
#      doivent avoir exactement les mêmes clés ET les mêmes trous : un test
#      (tests/test_usb_whitelist.py) le vérifie.
# EN : {name} is a placeholder filled by t("key", name=value). Both languages
#      must have exactly the same keys AND the same placeholders: a test
#      (tests/test_usb_whitelist.py) checks that.
LANGUAGES = ("fr", "en")
DEFAULT_LANG = "fr"

MESSAGES: Dict[str, Dict[str, str]] = {
    "fr": {
        # --- erreurs / errors
        "error_prefix": "Erreur : {msg}",
        "err_not_installed": "usbguard n'est pas installé (lancer deploy/install.sh).",
        "err_timeout": "usbguard ne répond pas (démon bloqué ?).",
        "err_denied": ("accès refusé par le démon USBGuard. Vous êtes-vous "
                       "reconnecté depuis votre ajout au groupe wheel ?"),
        "err_no_service": "le service usbguard ne tourne pas (`systemctl status usbguard`).",
        "err_failed": "usbguard a échoué (code {code}).",
        "err_control_char": "caractère de contrôle dans une valeur : refusé.",
        "err_bad_id": "identifiant USB invalide : {value}",
        "err_no_hash": "le périphérique n'a pas d'empreinte (hash) : refusé.",
        "err_not_persisted": ("la règle n'apparaît pas dans la liste après son ajout : "
                              "elle ne survivrait pas à un redémarrage. Rien n'a été débloqué."),
        "err_need_number": "il faut saisir un numéro.",
        "err_no_item": "aucun élément n°{num} dans la liste ci-dessus.",
        # --- types de périphériques / device kinds
        "unknown": "inconnu",
        "class_01": "audio",
        "class_03": "clavier/souris (HID)",
        "class_07": "imprimante",
        "class_08": "stockage (clé/disque)",
        "class_09": "hub",
        "class_0e": "caméra/vidéo",
        "class_e0": "sans fil (Bluetooth...)",
        "class_other": "classe {code}",
        # --- états / states
        "target_allow": "AUTORISÉ",
        "target_block": "BLOQUÉ",
        "target_reject": "REJETÉ",
        # --- tableaux / tables
        "col_num": "N°",
        "col_state": "ÉTAT",
        "col_id": "ID",
        "col_type": "TYPE",
        "col_name": "NOM",
        "col_serial": "SÉRIE",
        "no_serial": "(aucun)",
        # --- saisie / prompts
        "confirm_hint": "[o/N]",
        "yes_word": "OUI",
        "prompt_pick_allow": "Numéro à autoriser (Entrée pour annuler) : ",
        "prompt_pick_remove": "Numéro à retirer (Entrée pour annuler) : ",
        "cancelled": "Annulé.",
        "interrupted": "Interrompu.",
        # --- commandes / commands
        "no_devices": "Aucun périphérique USB branché.",
        "no_blocked": "Aucun périphérique bloqué à autoriser.",
        "plug_and_retry": "Branchez la clé, puis relancez cette commande.",
        "whitelist_empty": "La liste blanche est vide.",
        "warn_not_storage": "ATTENTION : ce n'est pas une clé de stockage mais : {kind}.",
        "warn_hid_risk": "Un clavier autorisé à tort peut taper des commandes à votre place.",
        "type_yes_prompt": "Tapez {word} en majuscules pour continuer : ",
        "note_no_serial": ("Note : cet appareil n'a pas de numéro de série. La règle "
                           "vaudra pour tous les appareils du même modèle ayant la "
                           "même empreinte."),
        "rule_to_add": "Règle à ajouter :",
        "confirm_add": "Confirmer l'ajout à la liste blanche ?",
        "added_ok": "OK : périphérique autorisé (définitivement, sur tous les ports).",
        "warn_remove_hid": ("ATTENTION : c'est un clavier/souris. Le retirer peut vous "
                            "empêcher de piloter la machine."),
        "confirm_remove": "Retirer la règle n°{num} ({label}) ?",
        "removed_ok": "OK : règle retirée.",
        # --- état du service / service status
        "status_service": "Service usbguard      : {state}",
        "status_start_hint": "  -> `sudo systemctl enable --now usbguard` pour le démarrer.",
        "status_rules": "Règles d'autorisation : {count}",
        "status_devices": "Périphériques branchés: {count} (dont {blocked} bloqué(s))",
        "status_user": "Utilisateur           : {user} (groupe {group} : oui)",
        # --- menu / menus
        "menu_title": "=== Liste blanche USB ===",
        "menu_1": "Voir les périphériques branchés",
        "menu_2": "Autoriser un périphérique bloqué",
        "menu_3": "Voir la liste blanche",
        "menu_4": "Retirer un périphérique de la liste blanche",
        "menu_5": "État du service",
        "menu_quit": "Quitter",
        "menu_prompt": "Votre choix : ",
        "menu_invalid": "Choix invalide.",
        # --- accès / access
        "denied": "Accès refusé : cet outil est réservé au groupe « {group} ».",
        # --- aide en ligne / command-line help
        "help_description": ("Gestion de la liste blanche USB (réservé au groupe "
                             "wheel). Sans argument : menu interactif."),
        "help_lang": "langue des messages : fr ou en",
        "help_devices": "périphériques branchés et leur état",
        "help_list": "afficher la liste blanche",
        "help_status": "état du service",
        "help_add": "autoriser un périphérique bloqué",
        "help_remove": "retirer une règle de la liste blanche",
        "help_number": "numéro affiché dans la liste (sinon on le demande)",
    },
    "en": {
        # --- errors
        "error_prefix": "Error: {msg}",
        "err_not_installed": "usbguard is not installed (run deploy/install.sh).",
        "err_timeout": "usbguard is not responding (daemon stuck?).",
        "err_denied": ("access denied by the USBGuard daemon. Did you log in again "
                       "after being added to the wheel group?"),
        "err_no_service": "the usbguard service is not running (`systemctl status usbguard`).",
        "err_failed": "usbguard failed (code {code}).",
        "err_control_char": "control character in a value: refused.",
        "err_bad_id": "invalid USB identifier: {value}",
        "err_no_hash": "the device has no fingerprint (hash): refused.",
        "err_not_persisted": ("the rule does not appear in the list after being added: "
                              "it would not survive a restart. Nothing was unblocked."),
        "err_need_number": "a number is required.",
        "err_no_item": "no item #{num} in the list above.",
        # --- device kinds
        "unknown": "unknown",
        "class_01": "audio",
        "class_03": "keyboard/mouse (HID)",
        "class_07": "printer",
        "class_08": "storage (key/disk)",
        "class_09": "hub",
        "class_0e": "camera/video",
        "class_e0": "wireless (Bluetooth...)",
        "class_other": "class {code}",
        # --- states
        "target_allow": "ALLOWED",
        "target_block": "BLOCKED",
        "target_reject": "REJECTED",
        # --- tables
        "col_num": "NO.",
        "col_state": "STATE",
        "col_id": "ID",
        "col_type": "TYPE",
        "col_name": "NAME",
        "col_serial": "SERIAL",
        "no_serial": "(none)",
        # --- prompts
        "confirm_hint": "[y/N]",
        "yes_word": "YES",
        "prompt_pick_allow": "Number to allow (Enter to cancel): ",
        "prompt_pick_remove": "Number to remove (Enter to cancel): ",
        "cancelled": "Cancelled.",
        "interrupted": "Interrupted.",
        # --- commands
        "no_devices": "No USB device connected.",
        "no_blocked": "No blocked device to allow.",
        "plug_and_retry": "Plug the key in, then run this command again.",
        "whitelist_empty": "The whitelist is empty.",
        "warn_not_storage": "WARNING: this is not a storage key but: {kind}.",
        "warn_hid_risk": "A keyboard allowed by mistake can type commands in your place.",
        "type_yes_prompt": "Type {word} in capitals to continue: ",
        "note_no_serial": ("Note: this device has no serial number. The rule will "
                           "apply to every device of the same model with the same "
                           "fingerprint."),
        "rule_to_add": "Rule to add:",
        "confirm_add": "Confirm adding it to the whitelist?",
        "added_ok": "OK: device allowed (permanently, on every port).",
        "warn_remove_hid": ("WARNING: this is a keyboard/mouse. Removing it may "
                            "leave you unable to control the machine."),
        "confirm_remove": "Remove rule #{num} ({label})?",
        "removed_ok": "OK: rule removed.",
        # --- service status
        "status_service": "usbguard service      : {state}",
        "status_start_hint": "  -> `sudo systemctl enable --now usbguard` to start it.",
        "status_rules": "Allow rules           : {count}",
        "status_devices": "Connected devices     : {count} ({blocked} blocked)",
        "status_user": "User                  : {user} ({group} group: yes)",
        # --- menus
        "menu_title": "=== USB whitelist ===",
        "menu_1": "Show connected devices",
        "menu_2": "Allow a blocked device",
        "menu_3": "Show the whitelist",
        "menu_4": "Remove a device from the whitelist",
        "menu_5": "Service status",
        "menu_quit": "Quit",
        "menu_prompt": "Your choice: ",
        "menu_invalid": "Invalid choice.",
        # --- access
        "denied": "Access denied: this tool is restricted to the \"{group}\" group.",
        # --- command-line help
        "help_description": ("USB whitelist management (restricted to the wheel "
                             "group). With no argument: interactive menu."),
        "help_lang": "message language: fr or en",
        "help_devices": "connected devices and their state",
        "help_list": "show the whitelist",
        "help_status": "service status",
        "help_add": "allow a blocked device",
        "help_remove": "remove a rule from the whitelist",
        "help_number": "number shown in the list (asked for if omitted)",
    },
}

# FR : Langue active. Modifiée uniquement par set_language().
# EN : Active language. Only modified by set_language().
_current_lang = DEFAULT_LANG


def set_language(lang: str) -> None:
    """Choisit la langue d'affichage. / Select the display language."""
    global _current_lang
    _current_lang = lang if lang in MESSAGES else DEFAULT_LANG


def t(key: str, **values: object) -> str:
    """Renvoie le texte `key` dans la langue active.
    Return the text for `key` in the active language.

    FR : Si la clé manque dans la langue active, on retombe sur le français
         plutôt que de planter au milieu d'une opération.
    EN : If the key is missing in the active language we fall back to French
         rather than crash in the middle of an operation.
    """
    text = MESSAGES[_current_lang].get(key) or MESSAGES[DEFAULT_LANG][key]
    return text.format(**values) if values else text


class UsbGuardError(Exception):
    """Erreur "attendue" : on l'affiche proprement, sans traceback.
    "Expected" error: shown cleanly, without a traceback.
    """


# --------------------------------------------------------------------------
# 1. Contrôle d'accès : réservé au groupe wheel
#    Access control: restricted to the wheel group
# --------------------------------------------------------------------------
def invoking_user() -> str:
    """Nom de l'humain derrière la commande.
    Name of the human behind the command.

    FR : Sous `sudo`, l'euid est 0 mais sudo place le vrai nom dans SUDO_USER.
    EN : Under `sudo` the euid is 0, but sudo stores the real name in SUDO_USER.
    """
    import pwd

    if os.geteuid() == 0 and os.environ.get("SUDO_USER"):
        return os.environ["SUDO_USER"]
    return pwd.getpwuid(os.getuid()).pw_name


def is_wheel_member() -> bool:
    """Vrai si l'utilisateur appartient au groupe wheel.
    True if the user belongs to the wheel group.
    """
    import grp
    import pwd

    try:
        wheel_gid = grp.getgrnam(WHEEL_GROUP).gr_gid
    except KeyError:
        # FR : pas de groupe wheel : on refuse par prudence.
        # EN : no wheel group: deny, to be safe.
        return False

    if os.geteuid() != 0:
        # FR : Cas normal : on regarde les groupes du processus lui-même
        #      (les vrais droits du noyau, pas ce que dit un fichier).
        # EN : Normal case: look at the process's own groups (the kernel's
        #      real credentials, not what some file claims).
        return wheel_gid == os.getegid() or wheel_gid in os.getgroups()

    # FR : On est root : soit via sudo (on contrôle l'appelant d'origine),
    #      soit une vraie session root.
    # EN : We are root: either through sudo (we check the original caller),
    #      or in a genuine root session.
    sudo_user = os.environ.get("SUDO_USER")
    if not sudo_user:
        return ALLOW_ROOT
    try:
        primary_gid = pwd.getpwnam(sudo_user).pw_gid
    except KeyError:
        return False
    return wheel_gid in os.getgrouplist(sudo_user, primary_gid)


def audit(message: str) -> None:
    """Trace dans le journal système.
    Write a trace in the system journal.

    FR : Consultable avec `journalctl -t usb-whitelist`. syslog n'exige aucun
         droit particulier : l'utilisateur wheel peut écrire sans avoir accès
         à /var/log. Le journal reste en anglais, quelle que soit la langue
         choisie, pour rester lisible par tous les administrateurs.
    EN : Readable with `journalctl -t usb-whitelist`. syslog needs no special
         privilege: a wheel user can write without having access to /var/log.
         The journal stays in English whatever the chosen language, so every
         administrator can read it.
    """
    import syslog

    syslog.openlog("usb-whitelist", 0, syslog.LOG_AUTH)
    syslog.syslog(syslog.LOG_NOTICE, f"user={invoking_user()} {message}")


# --------------------------------------------------------------------------
# 2. Dialogue avec usbguard / Talking to usbguard
# --------------------------------------------------------------------------
def run_usbguard(*args: str) -> str:
    """Exécute `usbguard <args>` et renvoie sa sortie texte.
    Run `usbguard <args>` and return its text output.
    """
    try:
        proc = subprocess.run(
            [USBGUARD, *args],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except FileNotFoundError:
        raise UsbGuardError(t("err_not_installed")) from None
    except subprocess.TimeoutExpired:
        raise UsbGuardError(t("err_timeout")) from None

    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip()
        if "ermission" in err or "denied" in err:
            raise UsbGuardError(t("err_denied"))
        if "connect" in err.lower() or "ipc" in err.lower():
            raise UsbGuardError(t("err_no_service"))
        raise UsbGuardError(err or t("err_failed", code=proc.returncode))
    return proc.stdout


# --------------------------------------------------------------------------
# 3. Lecture de la sortie d'usbguard / Parsing usbguard output
# --------------------------------------------------------------------------
# FR : Une ligne ressemble à :
# EN : A line looks like:
#   7: block id 0781:5581 serial "4C53..." name "Ultra" hash "Xy/..." \
#      parent-hash "..." via-port "2-1" with-interface { 08:06:50 } ...
# FR : On la découpe en "jetons" : texte entre guillemets, accolades, ou mots.
# EN : We split it into "tokens": quoted text, braces, or plain words.
TOKEN_RE = re.compile(r'"(?:[^"\\]|\\.)*"|[{}]|[^\s{}"]+')
LINE_RE = re.compile(r"^\s*(\d+):\s+(\S+)\s*(.*)$")
INTERFACE_OPERATORS = {"all-of", "one-of", "none-of", "equals", "equals-ordered"}
VALUE_KEYS = {
    "id", "serial", "name", "hash", "parent-hash", "via-port",
    "with-connect-type", "label",
}


@dataclass
class Entry:
    """Une ligne de `list-devices` (périphérique) ou `list-rules` (règle).
    One line of `list-devices` (a device) or `list-rules` (a rule).
    """

    num: int
    target: str                 # allow / block / reject
    vid_pid: str = ""           # "0781:5581"
    serial: str = ""
    name: str = ""
    hash: str = ""
    # FR : port physique ("1-1") et empreinte du parent : un périphérique les
    #      porte toujours ; une règle seulement si elle est « liée au port ».
    # EN : physical port ("1-1") and parent fingerprint: a device always
    #      carries them; a rule only if it is "port-bound".
    via_port: str = ""
    parent_hash: str = ""
    interfaces: List[str] = field(default_factory=list)  # ["08:06:50"]

    @property
    def is_root_hub(self) -> bool:
        return self.vid_pid.lower().startswith(ROOT_HUB_VENDOR + ":")

    @property
    def kind(self) -> str:
        """Type lisible déduit de la première interface déclarée.
        Human-readable type derived from the first declared interface.
        """
        if not self.interfaces:
            return t("unknown")
        code = self.interfaces[0].split(":")[0].lower()
        key = f"class_{code}"
        if key in MESSAGES[DEFAULT_LANG]:
            return t(key)
        return t("class_other", code=code)

    @property
    def is_storage(self) -> bool:
        return bool(self.interfaces) and all(
            i.split(":")[0].lower() == STORAGE_CLASS for i in self.interfaces
        )


def _unquote(token: str) -> str:
    """Retire les guillemets et les échappements. / Strip quotes and escapes."""
    if len(token) >= 2 and token.startswith('"') and token.endswith('"'):
        return re.sub(r"\\(.)", r"\1", token[1:-1])
    return token


def parse_line(line: str) -> Optional[Entry]:
    """Transforme une ligne usbguard en Entry (None si ce n'en est pas une).
    Turn one usbguard line into an Entry (None if it is not one).
    """
    match = LINE_RE.match(line)
    if not match:
        return None
    entry = Entry(num=int(match.group(1)), target=match.group(2))
    tokens = TOKEN_RE.findall(match.group(3))

    def read_group(i: int):
        """Lit un jeton simple, ou { a b c }. Renvoie (valeurs, index suivant).
        Read one plain token, or { a b c }. Returns (values, next index).
        """
        if i < len(tokens) and tokens[i] == "{":
            values, i = [], i + 1
            while i < len(tokens) and tokens[i] != "}":
                values.append(_unquote(tokens[i]))
                i += 1
            return values, i + 1
        if i < len(tokens):
            return [_unquote(tokens[i])], i + 1
        return [], i

    i = 0
    while i < len(tokens):
        key = tokens[i]
        i += 1
        if key == "with-interface":
            if i < len(tokens) and tokens[i] in INTERFACE_OPERATORS:
                i += 1
            entry.interfaces, i = read_group(i)
        elif key in VALUE_KEYS:
            values, i = read_group(i)
            value = " ".join(values)
            if key == "id":
                entry.vid_pid = value
            elif key == "serial":
                entry.serial = value
            elif key == "name":
                entry.name = value
            elif key == "hash":
                entry.hash = value
            elif key == "via-port":
                entry.via_port = value
            elif key == "parent-hash":
                entry.parent_hash = value
        # FR : tout autre jeton est ignoré : on reste tolérant aux nouveautés.
        # EN : any other token is ignored: we stay tolerant to new fields.
    return entry


def parse_output(text: str) -> List[Entry]:
    entries = (parse_line(line) for line in text.splitlines())
    return [e for e in entries if e is not None]


def list_devices() -> List[Entry]:
    return parse_output(run_usbguard("list-devices"))


def list_rules() -> List[Entry]:
    return parse_output(run_usbguard("list-rules"))


# --------------------------------------------------------------------------
# 4. Construction d'une règle / Building a rule
# --------------------------------------------------------------------------
HEX_ID_RE = re.compile(r"^[0-9a-f]{4}:[0-9a-f]{4}$")


def quote(value: str) -> str:
    """Met entre guillemets pour la syntaxe des règles usbguard.
    Quote a value for usbguard rule syntax.
    """
    if any(ord(c) < 32 for c in value):
        raise UsbGuardError(t("err_control_char"))
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_allow_rule(device: Entry) -> str:
    """Règle `allow` valable sur N'IMPORTE QUEL port USB.
    An `allow` rule valid on ANY USB port.

    FR : On n'utilise volontairement pas `usbguard allow-device -p`, qui ajoute
         `via-port` : la clé ne marcherait alors que dans la prise où on l'a
         déclarée. Critères retenus :
           - id     : constructeur:produit
           - serial : numéro de série (distingue deux clés du même modèle)
           - name   : nom, pour que le fichier reste lisible
           - hash   : empreinte des descripteurs USB (détecte une clé
                      reprogrammée, ex. clé qui se fait passer pour un
                      clavier = "BadUSB")
    EN : We deliberately avoid `usbguard allow-device -p`, which adds
         `via-port`: the key would then only work in the socket where it was
         declared. Criteria used:
           - id     : vendor:product
           - serial : serial number (tells two keys of the same model apart)
           - name   : name, so the rules file stays readable
           - hash   : fingerprint of the USB descriptors (detects a
                      reprogrammed key, e.g. a key posing as a keyboard,
                      a.k.a. "BadUSB")
    """
    if not HEX_ID_RE.match(device.vid_pid.lower()):
        raise UsbGuardError(t("err_bad_id", value=repr(device.vid_pid)))
    if not device.hash:
        raise UsbGuardError(t("err_no_hash"))
    parts = ["allow", "id", device.vid_pid.lower()]
    if device.serial:
        parts += ["serial", quote(device.serial)]
    if device.name:
        parts += ["name", quote(device.name)]
    parts += ["hash", quote(device.hash)]
    return " ".join(parts)


def same_device(a: Entry, b: Entry) -> bool:
    """Deux entrées désignent le même matériel (id + série + empreinte).
    Two entries designate the same hardware (id + serial + hash).
    """
    return (
        a.vid_pid.lower() == b.vid_pid.lower()
        and a.serial == b.serial
        and (not a.hash or not b.hash or a.hash == b.hash)
    )


def rule_covers(rule: Entry, device: Entry) -> bool:
    """La règle autoriserait-elle ce périphérique, TEL QUE BRANCHÉ ?
    Would this rule allow this device, AS CURRENTLY PLUGGED?

    FR : same_device() dit « même matériel ». Ici on ajoute ce qu'usbguard
         vérifie vraiment : si la règle est liée à un port (`via-port`) ou à
         un parent (`parent-hash`), le périphérique doit y être branché. Une
         règle liée au port « 2-1 » désigne la même souris, mais ne la
         reconnaît plus quand elle apparaît sur « 1-1 ».
    EN : same_device() says "same hardware". Here we add what usbguard
         really checks: if the rule is bound to a port (`via-port`) or to a
         parent (`parent-hash`), the device must sit there. A rule bound to
         port "2-1" designates the same mouse, yet no longer recognizes it
         when it shows up on "1-1".
    """
    if not same_device(rule, device):
        return False
    if rule.via_port and rule.via_port != device.via_port:
        return False
    if rule.parent_hash and rule.parent_hash != device.parent_hash:
        return False
    return True


# --------------------------------------------------------------------------
# 5. Affichage et saisie / Display and input
# --------------------------------------------------------------------------
def print_table(headers: List[str], rows: List[List[str]]) -> None:
    widths = [len(h) for h in headers]
    for row in rows:
        widths = [max(w, len(c)) for w, c in zip(widths, row)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*("-" * w for w in widths)))
    for row in rows:
        print(fmt.format(*row))


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def confirm(prompt: str) -> bool:
    """Oui/non. Accepte o/oui/y/yes dans les deux langues.
    Yes/no. Accepts o/oui/y/yes in both languages.
    """
    answer = ask(f"{prompt} {t('confirm_hint')} ").lower()
    return answer in {"o", "oui", "y", "yes"}


def pick(entries: List[Entry], wanted: Optional[int], prompt_key: str) -> Optional[Entry]:
    """Choisit une entrée : par numéro en argument, sinon en demandant.
    Choose an entry: by number given as argument, otherwise by asking.
    """
    if wanted is None:
        answer = ask(t(prompt_key))
        if not answer:
            return None
        if not answer.isdigit():
            raise UsbGuardError(t("err_need_number"))
        wanted = int(answer)
    for entry in entries:
        if entry.num == wanted:
            return entry
    raise UsbGuardError(t("err_no_item", num=wanted))


def _headers(with_state: bool) -> List[str]:
    cols = ["col_num"] + (["col_state"] if with_state else []) + [
        "col_id", "col_type", "col_name", "col_serial"]
    return [t(c) for c in cols]


def state_label(target: str) -> str:
    """allow/block/reject -> texte traduit. / allow/block/reject -> translated text."""
    key = f"target_{target}"
    return t(key) if key in MESSAGES[DEFAULT_LANG] else target


def show_devices(devices: List[Entry]) -> None:
    rows = [
        [str(d.num), state_label(d.target), d.vid_pid,
         d.kind, d.name or "-", d.serial or t("no_serial")]
        for d in devices
    ]
    print_table(_headers(with_state=True), rows)


def show_rules(rules: List[Entry]) -> None:
    rows = [[str(r.num), r.vid_pid, r.kind, r.name or "-", r.serial or t("no_serial")]
            for r in rules]
    print_table(_headers(with_state=False), rows)


def visible_devices() -> List[Entry]:
    return [d for d in list_devices() if not d.is_root_hub]


# --------------------------------------------------------------------------
# 6. Commandes / Commands
# --------------------------------------------------------------------------
def cmd_devices(_: argparse.Namespace) -> None:
    """Liste les périphériques branchés. / List connected devices."""
    devices = visible_devices()
    if not devices:
        print(t("no_devices"))
        return
    show_devices(devices)


def cmd_list(_: argparse.Namespace) -> None:
    """Affiche la liste blanche. / Show the whitelist."""
    rules = [r for r in list_rules() if r.target == "allow"]
    if not rules:
        print(t("whitelist_empty"))
        return
    show_rules(rules)


def cmd_add(args: argparse.Namespace) -> None:
    """Autorise définitivement un périphérique bloqué.
    Permanently allow a blocked device.
    """
    pending = [d for d in visible_devices() if d.target != "allow"]
    if not pending:
        print(t("no_blocked"))
        print(t("plug_and_retry"))
        return
    show_devices(pending)
    print()
    device = pick(pending, args.number, "prompt_pick_allow")
    if device is None:
        print(t("cancelled"))
        return

    # FR : Garde-fous : on explique, on ne bloque pas.
    # EN : Safeguards: we explain, we do not forbid.
    if not device.is_storage:
        print("\n" + t("warn_not_storage", kind=device.kind))
        print(t("warn_hid_risk"))
        if ask(t("type_yes_prompt", word=t("yes_word"))) != t("yes_word"):
            print(t("cancelled"))
            return
    if not device.serial:
        print("\n" + t("note_no_serial"))

    rule = build_allow_rule(device)
    print(f"\n{t('rule_to_add')}\n  {rule}")
    if not confirm(t("confirm_add")):
        print(t("cancelled"))
        return

    # FR : « Déjà autorisé » = une règle SANS port désigne ce matériel. Une
    #      règle liée à un port (ex. celle de la photo de base) ne compte
    #      pas : elle ne suit pas la clé quand le numéro de port change.
    # EN : "Already allowed" = a rule WITHOUT a port designates this
    #      hardware. A port-bound rule (e.g. the baseline snapshot's) does
    #      not count: it does not follow the key when the port number changes.
    def has_portable_rule() -> bool:
        return any(
            r.target == "allow" and not r.via_port and same_device(device, r)
            for r in list_rules()
        )

    if not has_portable_rule():
        # FR : écrit dans /etc/usbguard/rules.conf
        # EN : writes into /etc/usbguard/rules.conf
        run_usbguard("append-rule", rule)
        # FR : On VÉRIFIE avant d'annoncer « définitivement » : on relit les
        #      règles et on s'assure que la nouvelle y figure. Sinon, on
        #      s'arrête SANS débloquer ni affirmer quoi que ce soit.
        # EN : We VERIFY before announcing "permanently": we re-read the
        #      rules and make sure the new one is there. Otherwise we stop
        #      WITHOUT unblocking or claiming anything.
        if not has_portable_rule():
            raise UsbGuardError(t("err_not_persisted"))
    # FR : débloque tout de suite, sans rebrancher.
    # EN : unblocks right away, no need to re-plug.
    run_usbguard("allow-device", str(device.num))
    audit(f"action=add id={device.vid_pid} serial={device.serial!r} name={device.name!r}")
    print(t("added_ok"))


def cmd_remove(args: argparse.Namespace) -> None:
    """Retire une règle de la liste blanche. / Remove a whitelist rule."""
    rules = [r for r in list_rules() if r.target == "allow"]
    if not rules:
        print(t("whitelist_empty"))
        return
    show_rules(rules)
    print()
    rule = pick(rules, args.number, "prompt_pick_remove")
    if rule is None:
        print(t("cancelled"))
        return
    if rule.interfaces and rule.interfaces[0].lower().startswith("03:"):
        print("\n" + t("warn_remove_hid"))
    if not confirm(t("confirm_remove", num=rule.num, label=rule.name or rule.vid_pid)):
        print(t("cancelled"))
        return

    run_usbguard("remove-rule", str(rule.num))
    # FR : Retirer la règle n'éjecte pas un appareil déjà autorisé : on le
    #      bloque explicitement pour que le retrait soit immédiat.
    # EN : Removing the rule does not eject an already-authorized device: we
    #      block it explicitly so the removal takes effect immediately.
    #
    # FR : MAIS seulement si plus AUCUNE règle restante n'autorise ce
    #      périphérique (on relit les règles APRÈS le retrait) : sinon on
    #      couperait une clé encore légitimement autorisée par une autre règle.
    # EN : BUT only if NO remaining rule still allows that device (we re-read
    #      the rules AFTER the removal): otherwise we would cut a key that
    #      another rule still legitimately allows.
    remaining = [r for r in list_rules() if r.target == "allow"]
    for device in visible_devices():
        if (device.target == "allow" and same_device(device, rule)
                and not any(rule_covers(r, device) for r in remaining)):
            run_usbguard("block-device", str(device.num))
    audit(f"action=remove id={rule.vid_pid} serial={rule.serial!r} name={rule.name!r}")
    print(t("removed_ok"))


def cmd_status(_: argparse.Namespace) -> None:
    """État du service et compteurs. / Service state and counters."""
    active = subprocess.run(
        [SYSTEMCTL, "is-active", "usbguard"], capture_output=True, text=True
    ).stdout.strip()
    print(t("status_service", state=active or t("unknown")))
    if active != "active":
        print(t("status_start_hint"))
        return
    rules = [r for r in list_rules() if r.target == "allow"]
    devices = visible_devices()
    blocked = [d for d in devices if d.target != "allow"]
    print(t("status_rules", count=len(rules)))
    print(t("status_devices", count=len(devices), blocked=len(blocked)))
    print(t("status_user", user=invoking_user(), group=WHEEL_GROUP))


def choose_language() -> None:
    """Petit menu de langue, affiché en premier. / Small language menu, shown first.

    FR : Ce menu est volontairement bilingue : on ne sait pas encore quelle
         langue l'utilisateur lit.
    EN : This menu is deliberately bilingual: we do not yet know which
         language the user reads.
    """
    print("Langue / Language")
    print("  1) Français")
    print("  2) English")
    choice = ask("> ")
    set_language("en" if choice in {"2", "en", "EN", "english", "English"} else "fr")


def cmd_menu(_: argparse.Namespace) -> None:
    """Menu interactif (quand on tape juste `usb-whitelist`).
    Interactive menu (when the user just types `usb-whitelist`).
    """
    actions = {
        "1": ("menu_1", cmd_devices),
        "2": ("menu_2", cmd_add),
        "3": ("menu_3", cmd_list),
        "4": ("menu_4", cmd_remove),
        "5": ("menu_5", cmd_status),
    }
    no_args = argparse.Namespace(number=None)
    while True:
        print("\n" + t("menu_title"))
        for key, (label_key, _fn) in actions.items():
            print(f"  {key}) {t(label_key)}")
        print(f"  q) {t('menu_quit')}")
        choice = ask(t("menu_prompt")).lower()
        if choice in {"q", "quit", ""}:
            return
        if choice not in actions:
            print(t("menu_invalid"))
            continue
        print()
        try:
            actions[choice][1](no_args)
        except UsbGuardError as exc:
            print(t("error_prefix", msg=exc))


# --------------------------------------------------------------------------
# 7. Point d'entrée / Entry point
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    # FR : L'option --lang est déclarée dans un "parent" partagé, pour être
    #      acceptée AVANT ou APRÈS la commande : `--lang en list` comme
    #      `list --lang en`. Sa valeur est lue par _initial_language(), d'où
    #      SUPPRESS (le parseur complet n'a pas à la stocker).
    # EN : The --lang option is declared in a shared "parent", so it is
    #      accepted BEFORE or AFTER the command: `--lang en list` as well as
    #      `list --lang en`. Its value is read by _initial_language(), hence
    #      SUPPRESS (the full parser does not need to store it).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--lang", choices=LANGUAGES, default=argparse.SUPPRESS,
                        help=t("help_lang"))
    parser = argparse.ArgumentParser(
        prog="usb-whitelist",
        description=t("help_description"),
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command")
    for name, func in (("devices", cmd_devices), ("list", cmd_list),
                       ("status", cmd_status)):
        sub.add_parser(name, help=t(f"help_{name}"), parents=[common]) \
            .set_defaults(func=func)
    for name, func in (("add", cmd_add), ("remove", cmd_remove)):
        p = sub.add_parser(name, help=t(f"help_{name}"), parents=[common])
        p.add_argument("number", nargs="?", type=int, help=t("help_number"))
        p.set_defaults(func=func)
    return parser


def _initial_language(argv: List[str]) -> Optional[str]:
    """Langue demandée par --lang ou l'environnement, sinon None.
    Language requested by --lang or the environment, otherwise None.

    FR : On la lit AVANT de construire le vrai parseur, car l'aide (--help)
         est elle-même traduite.
    EN : We read it BEFORE building the real parser, because the help
         (--help) is itself translated.
    """
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--lang", choices=LANGUAGES)
    known, _ = pre.parse_known_args(argv)
    lang = known.lang or os.environ.get("USB_WHITELIST_LANG", "")
    return lang if lang in LANGUAGES else None


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv

    lang = _initial_language(argv)
    set_language(lang or DEFAULT_LANG)
    args = build_parser().parse_args(argv)

    # FR : Refus : on ne sait pas quelle langue lit l'utilisateur, on affiche
    #      donc les deux.
    # EN : Denial: we do not know which language the user reads, so we show
    #      both.
    if not is_wheel_member():
        for code in LANGUAGES:
            set_language(code)
            print(t("denied", group=WHEEL_GROUP), file=sys.stderr)
        return 1

    # FR : Menu de langue seulement si on est interactif, sans commande
    #      explicite et sans langue déjà imposée (un script ne doit jamais
    #      rester bloqué à attendre une réponse).
    # EN : Language menu only when interactive, with no explicit command and
    #      no language already imposed (a script must never hang waiting for
    #      an answer).
    if getattr(args, "func", None) is None and lang is None and sys.stdin.isatty():
        choose_language()

    func = getattr(args, "func", cmd_menu)
    try:
        func(args)
    except UsbGuardError as exc:
        print(t("error_prefix", msg=exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n" + t("interrupted"))
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
