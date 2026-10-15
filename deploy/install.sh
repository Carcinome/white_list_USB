#!/usr/bin/env bash
# install.sh
#
# FR : Installation HORS-LIGNE sur le poste cible. À lancer en root, depuis le
#      dossier produit par prepare_bundle.sh. Peut être relancé sans danger :
#      une liste blanche existante est conservée.
# EN : OFFLINE installation on the target workstation. Run as root, from the
#      folder produced by prepare_bundle.sh. Safe to run again: an existing
#      whitelist is kept.
#
# Usage : sudo ./install.sh [-y]
#         -y : ne pas demander de confirmation / do not ask for confirmation
# Langue / Language : USB_WHITELIST_LANG=fr|en  (default: system locale / sinon langue du système)

# FR : -e arrêt à la 1re erreur, -u variable non définie = erreur,
#      -o pipefail un pipeline échoue si une de ses étapes échoue.
# EN : -e stop at first error, -u undefined variable = error,
#      -o pipefail a pipeline fails if any of its stages fails.
set -euo pipefail

# --------------------------------------------------------------------------
# Langue / Language
# --------------------------------------------------------------------------
# FR : say "texte français" "english text" affiche la bonne version.
# EN : say "texte français" "english text" prints the right version.
case "${USB_WHITELIST_LANG:-${LANG:-fr}}" in
    en*) LANG_CODE=en ;;
    *)   LANG_CODE=fr ;;
esac
say() { if [[ $LANG_CODE == en ]]; then echo "$2"; else echo "$1"; fi; }
die() { say "$1" "$2" >&2; exit 1; }

# --------------------------------------------------------------------------
# Paramètres et vérifications préalables / Parameters and pre-checks
# --------------------------------------------------------------------------
HERE="$(cd "$(dirname "$0")" && pwd)"
ASSUME_YES=0
if [[ "${1:-}" == "-y" ]]; then ASSUME_YES=1; fi

RULES=/etc/usbguard/rules.conf                 # FR: la liste blanche / EN: the whitelist
CONF=/etc/usbguard/usbguard-daemon.conf        # FR: config du démon / EN: daemon config
ACL_FILE="/etc/usbguard/IPCAccessControl.d/:wheel"   # FR: droits du groupe / EN: group rights
TOOL=/usr/local/bin/usb-whitelist

[[ $EUID -eq 0 ]] || die "Ce script doit être lancé en root (sudo)." \
                         "This script must be run as root (sudo)."
[[ -d "$HERE/rpms" ]] || die "Dossier rpms/ introuvable à côté du script." \
                             "rpms/ folder not found next to the script."
[[ -f "$HERE/usb_whitelist.py" ]] || die "usb_whitelist.py introuvable à côté du script." \
                                         "usb_whitelist.py not found next to the script."

# --------------------------------------------------------------------------
# 1. Intégrité du support / Media integrity
# --------------------------------------------------------------------------
# FR : Relit SHA256SUMS (créé par prepare_bundle.sh) : détecte une copie
#      corrompue ou un RPM modifié en route. --quiet = n'affiche que les échecs.
# EN : Re-reads SHA256SUMS (created by prepare_bundle.sh): detects a corrupted
#      copy or an RPM altered in transit. --quiet = only print failures.
say "[1/6] Vérification des empreintes SHA256..." \
    "[1/6] Verifying SHA256 checksums..."
( cd "$HERE/rpms" && sha256sum --quiet -c ../SHA256SUMS )

# --------------------------------------------------------------------------
# 2. Installation sans réseau, par nom / Offline install, by name
# --------------------------------------------------------------------------
# FR : Le dossier rpms/ est un DÉPÔT LOCAL (index repodata/ créé par
#      prepare_bundle.sh). On demande à dnf d'installer `usbguard` et
#      `usbguard-selinux` PAR NOM, et dnf choisit seul ce qui manque :
#        - poste déjà installé : seulement les paquets absents (ici 4) ;
#        - poste neuf          : tout le nécessaire, présent dans le dépôt ;
#        - jamais de rétrogradation d'un paquet déjà installé.
#      (Donner les 133 fichiers un par un aurait rétrogradé
#      oraclelinux-release, qui possède /etc/os-release.)
#
#      --disablerepo='*'            aucun dépôt contacté (poste hors-ligne)
#      --repofrompath=usbwl,<dir>   déclare le dossier comme dépôt « usbwl »
#      --enablerepo=usbwl           et ne laisse que lui actif
#      usbwl.gpgcheck=1 + gpgkey    exige la signature GPG d'Oracle sur
#                                   chaque RPM (le bundle a voyagé)
# EN : The rpms/ folder is a LOCAL REPOSITORY (repodata/ index created by
#      prepare_bundle.sh). We ask dnf to install `usbguard` and
#      `usbguard-selinux` BY NAME, and dnf picks what is missing by itself:
#        - already-installed host: only the absent packages (4 here);
#        - brand-new host:         everything needed, found in the repo;
#        - never a downgrade of an already-installed package.
#      (Feeding the 133 files one by one would have downgraded
#      oraclelinux-release, which owns /etc/os-release.)
#
#      --disablerepo='*'            no repository contacted (offline host)
#      --repofrompath=usbwl,<dir>   declares the folder as repo "usbwl"
#      --enablerepo=usbwl           and leaves only that one active
#      usbwl.gpgcheck=1 + gpgkey    requires Oracle's GPG signature on every
#                                   RPM (the bundle has travelled)
say "[2/6] Installation des paquets (par nom, depuis le dépôt local)..." \
    "[2/6] Installing packages (by name, from the local repository)..."
GPG_KEY=/etc/pki/rpm-gpg/RPM-GPG-KEY-oracle
[[ -d "$HERE/rpms/repodata" ]] || die \
    "Index du dépôt absent (rpms/repodata) : relancez prepare_bundle.sh." \
    "Repository index missing (rpms/repodata): run prepare_bundle.sh again."
[[ -r "$GPG_KEY" ]] || die \
    "Clé GPG Oracle introuvable : $GPG_KEY" \
    "Oracle GPG key not found: $GPG_KEY"
dnf -y install --disablerepo='*' \
    --repofrompath="usbwl,$HERE/rpms" --enablerepo=usbwl \
    --setopt=usbwl.gpgcheck=1 --setopt="usbwl.gpgkey=file://$GPG_KEY" \
    usbguard usbguard-selinux

# --------------------------------------------------------------------------
# 3. Politique de base / Baseline policy
# --------------------------------------------------------------------------
# FR : Piège classique : si on active « tout bloquer » sans aucune règle, le
#      clavier/souris USB est bloqué aussi et on perd la main sur le poste.
#      On photographie donc ce qui est branché MAINTENANT (generate-policy) et
#      on l'autorise : c'est le point de départ.
#      Mais si une liste blanche existe déjà (script relancé), on n'y touche
#      pas : la remplacer par la photo effacerait le travail déjà fait.
# EN : Classic trap: if we enable "block everything" with no rule at all, the
#      USB keyboard/mouse gets blocked too and we lose control of the machine.
#      So we snapshot what is plugged in RIGHT NOW (generate-policy) and allow
#      it: that is the starting point.
#      But if a whitelist already exists (script run again) we leave it
#      alone: replacing it with the snapshot would wipe the work already done.
say "[3/6] Politique de base..." "[3/6] Baseline policy..."
# FR : "une règle" = une ligne commençant par allow/block/reject.
# EN : "a rule" = a line starting with allow/block/reject.
if [[ -s "$RULES" ]] && grep -qE '^[[:space:]]*(allow|block|reject)[[:space:]]' "$RULES"; then
    say "  Liste blanche existante conservée : $RULES" \
        "  Existing whitelist kept: $RULES"
else
    TMP="$(mktemp)"
    # FR : On retire `via-port "..."` de chaque règle. Les numéros de port
    #      (« 2-1 ») dépendent de l'ordre d'initialisation des contrôleurs et
    #      CHANGENT d'un démarrage à l'autre (constaté sur VMware : « 2-1 »
    #      est devenu « 1-1 »). Une règle liée au port ne reconnaît alors plus
    #      le périphérique, qui est bloqué. `hash` et `parent-hash` suffisent à
    #      l'identifier. (Même choix que pour les clés ajoutées par l'outil.)
    # EN : We strip `via-port "..."` from every rule. Port numbers ("2-1")
    #      depend on controller initialization order and CHANGE between
    #      boots (seen on VMware: "2-1" became "1-1"). A port-bound rule then
    #      no longer recognizes the device, which gets blocked. `hash` and
    #      `parent-hash` are enough to identify it. (Same choice as for the
    #      keys added by the tool.)
    usbguard generate-policy | sed -E 's/ via-port "[^"]*"//g' > "$TMP"
    say "-------- périphériques qui resteront autorisés --------" \
        "-------- devices that will stay allowed --------"
    cat "$TMP"
    echo "-------------------------------------------------------"
    say "Tout le reste sera BLOQUÉ. Clavier/souris USB branchés ? (sinon : Ctrl+C)" \
        "Everything else will be BLOCKED. USB keyboard/mouse plugged in? (if not: Ctrl+C)"
    if [[ $ASSUME_YES -eq 0 ]]; then
        read -r -p "$(say 'Continuer ? [o/N] ' 'Continue? [y/N] ')" answer
        if [[ ! "$answer" =~ ^[oOyY] ]]; then
            rm -f "$TMP"
            die "Abandon." "Aborted."
        fi
    fi
    # FR : usbguard exige que ce fichier soit en 0600, propriété de root.
    # EN : usbguard requires this file to be 0600, owned by root.
    install -m 0600 -o root -g root "$TMP" "$RULES"
    rm -f "$TMP"
fi

# --------------------------------------------------------------------------
# 4. Réglage du démon / Daemon settings
# --------------------------------------------------------------------------
# FR : ImplicitPolicyTarget=block       ce qui ne correspond à aucune règle
#                                       est bloqué
#      PresentDevicePolicy=apply-policy appliquer les règles aussi aux
#                                       périphériques déjà branchés au
#                                       démarrage (et non « tout autoriser »)
#      InsertedDevicePolicy=apply-policy idem pour les branchements à chaud
#      PresentControllerPolicy=keep     ne pas toucher aux contrôleurs USB
# EN : ImplicitPolicyTarget=block       anything matching no rule is blocked
#      PresentDevicePolicy=apply-policy apply the rules to devices already
#                                       plugged in at daemon start (instead
#                                       of "allow everything")
#      InsertedDevicePolicy=apply-policy same for hot-plugged devices
#      PresentControllerPolicy=keep     leave the USB controllers alone
say "[4/6] Configuration du démon..." "[4/6] Configuring the daemon..."

# FR : set_opt CLE VALEUR remplace la ligne « CLE=... » (même commentée avec
#      #), ou l'ajoute en fin de fichier si elle n'existe pas.
# EN : set_opt KEY VALUE replaces the "KEY=..." line (even if commented out
#      with #), or appends it at the end of the file if it does not exist.
set_opt() {
    if grep -qE "^#?[[:space:]]*$1=" "$CONF"; then
        sed -i -E "s|^#?[[:space:]]*$1=.*|$1=$2|" "$CONF"
    else
        echo "$1=$2" >> "$CONF"
    fi
}
set_opt ImplicitPolicyTarget block
set_opt PresentDevicePolicy apply-policy
set_opt InsertedDevicePolicy apply-policy
set_opt PresentControllerPolicy keep

# --------------------------------------------------------------------------
# 5. Qui a le droit de piloter le démon : le groupe wheel
#    Who may drive the daemon: the wheel group
# --------------------------------------------------------------------------
# FR : C'est la barrière la plus solide : elle est appliquée par le démon
#      lui-même (l'identité de l'appelant est vérifiée par le noyau), pas par
#      notre script Python. Même quelqu'un qui contournerait le script serait
#      refusé par le démon s'il n'est pas dans wheel.
# EN : This is the strongest barrier: it is enforced by the daemon itself (the
#      caller's identity is verified by the kernel), not by our Python
#      script. Even someone bypassing the script is refused by the daemon
#      unless they are in wheel.
say "[5/6] Droits du groupe wheel sur le démon..." \
    "[5/6] Granting the wheel group rights on the daemon..."
if [[ -e "$ACL_FILE" ]]; then
    say "  Déjà configuré." "  Already configured."
else
    # FR : devices   = voir/autoriser/bloquer les périphériques, écouter les
    #                  événements ; policy = lire/modifier les règles.
    # EN : devices   = list/allow/block devices, listen to events;
    #      policy    = read/modify the rules.
    usbguard add-user -g wheel \
        --devices=modify,list,listen --policy=modify,list --exceptions=listen
fi

# --------------------------------------------------------------------------
# 6. L'outil + démarrage / The tool + start-up
# --------------------------------------------------------------------------
# FR : Propriétaire root, groupe wheel, mode 0750 : un utilisateur hors wheel
#      ne peut même pas exécuter le fichier.
# EN : Owner root, group wheel, mode 0750: a user outside wheel cannot even
#      execute the file.
say "[6/6] Installation de l'outil et démarrage du service..." \
    "[6/6] Installing the tool and starting the service..."
install -m 0750 -o root -g wheel "$HERE/usb_whitelist.py" "$TOOL"
# FR : Remet le contexte SELinux correct sur le fichier (si disponible).
# EN : Restores the right SELinux context on the file (if available).
if command -v restorecon >/dev/null 2>&1; then restorecon "$TOOL"; fi

# FR : enable = démarrer à chaque boot ; restart = relire la config et les
#      règles maintenant (au 1er lancement, équivaut à start).
# EN : enable = start at every boot; restart = reload config and rules now
#      (on first run it behaves like start).
systemctl enable usbguard
systemctl restart usbguard

# --------------------------------------------------------------------------
# Contrôle final / Final check
# --------------------------------------------------------------------------
if [[ "$(systemctl is-active usbguard)" != "active" ]]; then
    die "Le service usbguard ne tourne pas : voir « journalctl -u usbguard »." \
        "The usbguard service is not running: see 'journalctl -u usbguard'."
fi

echo
say "Terminé. Essayez :  usb-whitelist status" \
    "Done. Try:  usb-whitelist status"
say "(Les membres de wheel doivent se reconnecter si leur groupe vient d'être modifié.)" \
    "(wheel members must log in again if their group membership was just changed.)"
