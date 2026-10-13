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

Dépendances / Dependencies : standard library only (Python 3.9 on OL9).
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import List, Optional

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

# FR : Classes USB (premier octet de "with-interface") -> libellé lisible.
# EN : USB classes (first byte of "with-interface") -> human-readable label.
USB_CLASSES = {
    "01": "audio",
    "03": "clavier/souris (HID)",
    "07": "imprimante",
    "08": "stockage (clé/disque)",
    "09": "hub",
    "0e": "caméra/vidéo",
    "e0": "sans fil (Bluetooth...)",
}
STORAGE_CLASS = "08"

TARGET_LABELS = {"allow": "AUTORISÉ", "block": "BLOQUÉ", "reject": "REJETÉ"}


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
         à /var/log.
    EN : Readable with `journalctl -t usb-whitelist`. syslog needs no special
         privilege: a wheel user can write without having access to /var/log.
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
        raise UsbGuardError(
            "usbguard n'est pas installé (lancer deploy/install.sh)."
        ) from None
    except subprocess.TimeoutExpired:
        raise UsbGuardError("usbguard ne répond pas (démon bloqué ?).") from None

    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip()
        if "ermission" in err or "denied" in err:
            raise UsbGuardError(
                "accès refusé par le démon USBGuard. Vous êtes-vous reconnecté "
                "depuis votre ajout au groupe wheel ?"
            )
        if "connect" in err.lower() or "ipc" in err.lower():
            raise UsbGuardError(
                "le service usbguard ne tourne pas "
                "(`systemctl status usbguard`)."
            )
        raise UsbGuardError(err or f"usbguard a échoué (code {proc.returncode}).")
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
            return "inconnu"
        code = self.interfaces[0].split(":")[0].lower()
        return USB_CLASSES.get(code, f"classe {code}")

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
        raise UsbGuardError("caractère de contrôle dans une valeur : refusé.")
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
        raise UsbGuardError(f"identifiant USB invalide : {device.vid_pid!r}")
    if not device.hash:
        raise UsbGuardError("le périphérique n'a pas d'empreinte (hash) : refusé.")
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
    return ask(f"{prompt} [o/N] ").lower() in {"o", "oui", "y", "yes"}


def pick(entries: List[Entry], wanted: Optional[int], verb: str) -> Optional[Entry]:
    """Choisit une entrée : par numéro en argument, sinon en demandant.
    Choose an entry: by number given as argument, otherwise by asking.
    """
    if wanted is None:
        answer = ask(f"Numéro à {verb} (Entrée pour annuler) : ")
        if not answer:
            return None
        if not answer.isdigit():
            raise UsbGuardError("il faut saisir un numéro.")
        wanted = int(answer)
    for entry in entries:
        if entry.num == wanted:
            return entry
    raise UsbGuardError(f"aucun élément n°{wanted} dans la liste ci-dessus.")


def show_devices(devices: List[Entry]) -> None:
    rows = [
        [str(d.num), TARGET_LABELS.get(d.target, d.target), d.vid_pid,
         d.kind, d.name or "-", d.serial or "(aucun)"]
        for d in devices
    ]
    print_table(["N°", "ÉTAT", "ID", "TYPE", "NOM", "SÉRIE"], rows)


def visible_devices() -> List[Entry]:
    return [d for d in list_devices() if not d.is_root_hub]


# --------------------------------------------------------------------------
# 6. Commandes / Commands
# --------------------------------------------------------------------------
def cmd_devices(_: argparse.Namespace) -> None:
    """Liste les périphériques branchés. / List connected devices."""
    devices = visible_devices()
    if not devices:
        print("Aucun périphérique USB branché.")
        return
    show_devices(devices)


def cmd_list(_: argparse.Namespace) -> None:
    """Affiche la liste blanche. / Show the whitelist."""
    rules = [r for r in list_rules() if r.target == "allow"]
    if not rules:
        print("La liste blanche est vide.")
        return
    rows = [[str(r.num), r.vid_pid, r.kind, r.name or "-", r.serial or "(aucun)"]
            for r in rules]
    print_table(["N°", "ID", "TYPE", "NOM", "SÉRIE"], rows)


def cmd_add(args: argparse.Namespace) -> None:
    """Autorise définitivement un périphérique bloqué.
    Permanently allow a blocked device.
    """
    pending = [d for d in visible_devices() if d.target != "allow"]
    if not pending:
        print("Aucun périphérique bloqué à autoriser.")
        print("Branchez la clé, puis relancez cette commande.")
        return
    show_devices(pending)
    print()
    device = pick(pending, args.number, "autoriser")
    if device is None:
        print("Annulé.")
        return

    # FR : Garde-fous : on explique, on ne bloque pas.
    # EN : Safeguards: we explain, we do not forbid.
    if not device.is_storage:
        print(f"\nATTENTION : ce n'est pas une clé de stockage mais : {device.kind}.")
        print("Un clavier autorisé à tort peut taper des commandes à votre place.")
        if ask("Tapez OUI en majuscules pour continuer : ") != "OUI":
            print("Annulé.")
            return
    if not device.serial:
        print("\nNote : cet appareil n'a pas de numéro de série. La règle vaudra "
              "pour tous les appareils du même modèle ayant la même empreinte.")

    rule = build_allow_rule(device)
    print(f"\nRègle à ajouter :\n  {rule}")
    if not confirm("Confirmer l'ajout à la liste blanche ?"):
        print("Annulé.")
        return

    already = any(
        same_device(device, r) for r in list_rules() if r.target == "allow"
    )
    if not already:
        # FR : écrit dans /etc/usbguard/rules.conf
        # EN : writes into /etc/usbguard/rules.conf
        run_usbguard("append-rule", rule)
    # FR : débloque tout de suite, sans rebrancher.
    # EN : unblocks right away, no need to re-plug.
    run_usbguard("allow-device", str(device.num))
    audit(f"action=add id={device.vid_pid} serial={device.serial!r} name={device.name!r}")
    print("OK : périphérique autorisé (définitivement, sur tous les ports).")


def cmd_remove(args: argparse.Namespace) -> None:
    """Retire une règle de la liste blanche. / Remove a whitelist rule."""
    rules = [r for r in list_rules() if r.target == "allow"]
    if not rules:
        print("La liste blanche est vide.")
        return
    print_table(
        ["N°", "ID", "TYPE", "NOM", "SÉRIE"],
        [[str(r.num), r.vid_pid, r.kind, r.name or "-", r.serial or "(aucun)"]
         for r in rules],
    )
    print()
    rule = pick(rules, args.number, "retirer")
    if rule is None:
        print("Annulé.")
        return
    if rule.interfaces and rule.interfaces[0].lower().startswith("03:"):
        print("\nATTENTION : c'est un clavier/souris. Le retirer peut vous "
              "empêcher de piloter la machine.")
    if not confirm(f"Retirer la règle n°{rule.num} ({rule.name or rule.vid_pid}) ?"):
        print("Annulé.")
        return

    run_usbguard("remove-rule", str(rule.num))
    # FR : Retirer la règle n'éjecte pas un appareil déjà autorisé : on le
    #      bloque explicitement pour que le retrait soit immédiat.
    # EN : Removing the rule does not eject an already-authorized device: we
    #      block it explicitly so the removal takes effect immediately.
    for device in visible_devices():
        if device.target == "allow" and same_device(device, rule):
            run_usbguard("block-device", str(device.num))
    audit(f"action=remove id={rule.vid_pid} serial={rule.serial!r} name={rule.name!r}")
    print("OK : règle retirée.")


def cmd_status(_: argparse.Namespace) -> None:
    """État du service et compteurs. / Service state and counters."""
    active = subprocess.run(
        [SYSTEMCTL, "is-active", "usbguard"], capture_output=True, text=True
    ).stdout.strip()
    print(f"Service usbguard      : {active or 'inconnu'}")
    if active != "active":
        print("  -> `sudo systemctl enable --now usbguard` pour le démarrer.")
        return
    rules = [r for r in list_rules() if r.target == "allow"]
    devices = visible_devices()
    blocked = [d for d in devices if d.target != "allow"]
    print(f"Règles d'autorisation : {len(rules)}")
    print(f"Périphériques branchés: {len(devices)} (dont {len(blocked)} bloqué(s))")
    print(f"Utilisateur           : {invoking_user()} (groupe {WHEEL_GROUP} : oui)")


def cmd_menu(_: argparse.Namespace) -> None:
    """Menu interactif (quand on tape juste `usb-whitelist`).
    Interactive menu (when the user just types `usb-whitelist`).
    """
    actions = {
        "1": ("Voir les périphériques branchés", cmd_devices),
        "2": ("Autoriser un périphérique bloqué", cmd_add),
        "3": ("Voir la liste blanche", cmd_list),
        "4": ("Retirer un périphérique de la liste blanche", cmd_remove),
        "5": ("État du service", cmd_status),
    }
    no_args = argparse.Namespace(number=None)
    while True:
        print("\n=== Liste blanche USB ===")
        for key, (label, _fn) in actions.items():
            print(f"  {key}) {label}")
        print("  q) Quitter")
        choice = ask("Votre choix : ").lower()
        if choice in {"q", "quit", ""}:
            return
        if choice not in actions:
            print("Choix invalide.")
            continue
        print()
        try:
            actions[choice][1](no_args)
        except UsbGuardError as exc:
            print(f"Erreur : {exc}")


# --------------------------------------------------------------------------
# 7. Point d'entrée / Entry point
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="usb-whitelist",
        description="Gestion de la liste blanche USB (réservé au groupe wheel). "
                    "Sans argument : menu interactif.",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("devices", help="périphériques branchés et leur état").set_defaults(func=cmd_devices)
    sub.add_parser("list", help="afficher la liste blanche").set_defaults(func=cmd_list)
    sub.add_parser("status", help="état du service").set_defaults(func=cmd_status)
    for name, func, text in (
        ("add", cmd_add, "autoriser un périphérique bloqué"),
        ("remove", cmd_remove, "retirer une règle de la liste blanche"),
    ):
        p = sub.add_parser(name, help=text)
        p.add_argument("number", nargs="?", type=int,
                       help="numéro affiché dans la liste (sinon on le demande)")
        p.set_defaults(func=func)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if not is_wheel_member():
        print(f"Accès refusé : cet outil est réservé au groupe « {WHEEL_GROUP} ».",
              file=sys.stderr)
        return 1

    func = getattr(args, "func", cmd_menu)
    try:
        func(args)
    except UsbGuardError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrompu.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
