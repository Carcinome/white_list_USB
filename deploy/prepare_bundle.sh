#!/usr/bin/env bash
# prepare_bundle.sh
#
# FR : À lancer sur UNE machine Oracle Linux 9 AYANT accès aux dépôts.
#      Produit un dossier autonome (RPM + outil + installateur) à copier sur
#      les postes hors-ligne (clé USB, partage...).
# EN : Run on ONE Oracle Linux 9 machine that CAN reach the repositories.
#      Produces a self-contained folder (RPMs + tool + installer) to copy to
#      the offline workstations (USB key, share...).
#
# Usage : ./prepare_bundle.sh [output_folder]      (default / défaut : ./bundle)
# Langue / Language : USB_WHITELIST_LANG=fr|en  (default: system locale / sinon langue du système)
#
# FR : Dépôts : l'OS custom les désactive. Ce script active, POUR SES SEULES
#      COMMANDES (--enablerepo), les dépôts de USB_WHITELIST_REPOS. Rien n'est
#      modifié dans /etc/yum.repos.d. Défaut : déduit de la version de l'OS
#      (OL 9.6 -> "ol9_u6_baseos_base ol9_appstream").
#      USB_WHITELIST_REPOS="" = n'en activer aucun (dépôts déjà actifs).
# EN : Repositories: the custom OS disables them. This script enables, FOR ITS
#      OWN COMMANDS ONLY (--enablerepo), the repos in USB_WHITELIST_REPOS.
#      Nothing is changed in /etc/yum.repos.d. Default: derived from the OS
#      version (OL 9.6 -> "ol9_u6_baseos_base ol9_appstream").
#      USB_WHITELIST_REPOS="" = enable none (already active).

# FR : -e  arrêt à la première commande en erreur
#      -u  une variable non définie est une erreur (évite les fautes de frappe)
#      -o pipefail  un pipeline échoue si UNE de ses étapes échoue
# EN : -e  stop at the first failing command
#      -u  an undefined variable is an error (catches typos)
#      -o pipefail  a pipeline fails if ANY of its stages fails
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

# --------------------------------------------------------------------------
# Dépôts à activer / Repositories to enable
# --------------------------------------------------------------------------
# FR : ${VAR-défaut} (sans « : ») n'applique le défaut que si VAR n'est PAS
#      définie ; une variable définie mais vide veut dire « aucun dépôt ».
#      Chaque nom devient une option --enablerepo=<nom>, passée à dnf plus bas.
# EN : ${VAR-default} (without ":") applies the default only when VAR is NOT
#      set; a variable set but empty means "no repository".
#      Each name becomes a --enablerepo=<name> option, passed to dnf below.
#
# FR : Sans variable, on DÉDUIT les dépôts de la version de l'OS en cours :
#      un OL 9.6 prend `ol9_u6_baseos_base` (la base 9.6) et non
#      `ol9_baseos_latest`, qui est toujours la DERNIÈRE mise à jour mineure.
#      Mélanger du 9.8 sur un 9.6 ferait refuser l'installation par dnf
#      (systemd est un paquet protégé) ou changerait la version de l'OS.
# EN : With no variable, we DERIVE the repos from the running OS version:
#      OL 9.6 takes `ol9_u6_baseos_base` (the 9.6 base), not
#      `ol9_baseos_latest`, which is always the LATEST minor update. Mixing
#      9.8 onto a 9.6 would make dnf refuse the install (systemd is a
#      protected package) or silently change the OS version.
OS_RELEASE=/etc/os-release

# FR : Garde-fou : une variable USB_WHITELIST_* inconnue est presque
#      sûrement une faute de frappe, que bash ignorerait en silence.
# EN : Safeguard: an unknown USB_WHITELIST_* variable is almost surely a
#      typo, which bash would silently ignore.
for v in $(compgen -e | grep '^USB_WHITELIST_' || true); do
    case "$v" in
        USB_WHITELIST_LANG|USB_WHITELIST_REPOS|USB_WHITELIST_PACKAGES) ;;
        *)
            say "Variable inconnue : $v  (valides : USB_WHITELIST_LANG, USB_WHITELIST_REPOS, USB_WHITELIST_PACKAGES)" \
                "Unknown variable: $v  (valid: USB_WHITELIST_LANG, USB_WHITELIST_REPOS, USB_WHITELIST_PACKAGES)" >&2
            exit 1
            ;;
    esac
done

if [[ -n "${USB_WHITELIST_REPOS+x}" ]]; then
    REPOS="$USB_WHITELIST_REPOS"            # FR: choix explicite / EN: explicit choice
else
    OS_VERSION=""
    if [[ -r "$OS_RELEASE" ]]; then
        OS_VERSION="$(. "$OS_RELEASE" && echo "${VERSION_ID:-}")"
    fi
    if [[ "$OS_VERSION" =~ ^9\.([0-9]+)$ ]]; then
        REPOS="ol9_u${BASH_REMATCH[1]}_baseos_base ol9_appstream"
    else
        REPOS="ol9_baseos_latest ol9_appstream"
    fi
fi
ENABLE_ARGS=()
for r in $REPOS; do
    ENABLE_ARGS+=("--enablerepo=$r")
done

# --------------------------------------------------------------------------
# Paramètres / Parameters
# --------------------------------------------------------------------------
DEST="${1:-./bundle}"
# FR : Dossier où se trouve ce script (pour retrouver install.sh à côté).
# EN : Folder holding this script (to find install.sh next to it).
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$DEST/rpms"

# --------------------------------------------------------------------------
# 1. La sous-commande `dnf download` / The `dnf download` subcommand
# --------------------------------------------------------------------------
# FR : On affiche les dépôts réellement utilisés : plus de surprise silencieuse.
# EN : We print the repositories actually used: no more silent surprises.
say "Dépôts utilisés (activés pour ce script seulement) : ${REPOS:-aucun}" \
    "Repositories used (enabled for this script only): ${REPOS:-none}"

# FR : `dnf download` vient du plugin dnf-plugins-core. On ne l'installe que
#      s'il manque (l'installer demande les droits root).
# EN : `dnf download` comes from the dnf-plugins-core plugin. We only install
#      it if missing (installing needs root).
if ! dnf download --help >/dev/null 2>&1; then
    say "[1/4] Installation du plugin dnf-plugins-core..." \
        "[1/4] Installing the dnf-plugins-core plugin..."
    dnf -y "${ENABLE_ARGS[@]}" install dnf-plugins-core >/dev/null
else
    say "[1/4] Plugin 'dnf download' déjà présent." \
        "[1/4] 'dnf download' plugin already present."
fi

# FR : createrepo_c fabrique l'index (« métadonnées ») qui transforme le
#      dossier rpms/ en dépôt que dnf sait interroger. On le vérifie TÔT :
#      échouer après 100 Mo de téléchargement serait une perte de temps.
#      Il n'est nécessaire qu'ICI, jamais sur les postes cibles.
# EN : createrepo_c builds the index ("metadata") that turns the rpms/ folder
#      into a repository dnf can query. We check it EARLY: failing after
#      100 MB of downloads would waste time. It is only needed HERE, never
#      on the target workstations.
if ! command -v createrepo_c >/dev/null 2>&1; then
    say "createrepo_c est absent. Installez-le (une fois), puis relancez :" \
        "createrepo_c is missing. Install it (once), then run again:" >&2
    echo "  sudo dnf ${ENABLE_ARGS[*]} install createrepo_c" >&2
    exit 1
fi

# --------------------------------------------------------------------------
# 2. Quels paquets ? / Which packages?
# --------------------------------------------------------------------------
# FR : usbguard-1.0.0-16.el9          = le démon ET la commande `usbguard`
#                                       (dans cette version, ils ne sont pas
#                                       séparés : pas de usbguard-tools)
#      usbguard-selinux-1.0.0-16.el9  = la politique SELinux assortie
#      python3                        = l'interpréteur de notre outil
#
#      POURQUOI ÉPINGLER LA VERSION : la 1.1.4 (la plus récente d'AppStream)
#      exige selinux-policy >= 38.1.65, alors qu'un OL 9.6 a la 38.1.53. dnf
#      refuse alors toute la résolution. La 1.0.0-16 s'installe proprement
#      (vérifié par simulation sur la VM : 4 paquets, 0 mise à jour).
#      Pour une autre liste : USB_WHITELIST_PACKAGES="nom-1.2.3-4.el9 ..."
# EN : usbguard-1.0.0-16.el9          = the daemon AND the `usbguard` command
#                                       (in this version they are not split:
#                                       no usbguard-tools)
#      usbguard-selinux-1.0.0-16.el9  = the matching SELinux policy
#      python3                        = the interpreter for our tool
#
#      WHY PIN THE VERSION: 1.1.4 (the newest in AppStream) requires
#      selinux-policy >= 38.1.65, whereas OL 9.6 ships 38.1.53. dnf then
#      refuses the whole resolution. 1.0.0-16 installs cleanly (verified by
#      a dry run on the VM: 4 packages, 0 upgrades).
#      For another list: USB_WHITELIST_PACKAGES="name-1.2.3-4.el9 ..."
read -r -a WANTED <<< "${USB_WHITELIST_PACKAGES:-usbguard-1.0.0-16.el9 usbguard-selinux-1.0.0-16.el9 python3}"
PKGS=()

# FR : On teste l'existence de chaque nom AVANT de télécharger. Un seul nom
#      inconnu ferait échouer tout `dnf download` ; ici on le signale et on
#      continue avec les autres.
# EN : We test each name BEFORE downloading. One unknown name would make the
#      whole `dnf download` fail; here we report it and carry on.
say "[2/4] Vérification des paquets dans les dépôts..." \
    "[2/4] Checking packages in the repositories..."
for p in "${WANTED[@]}"; do
    # FR : repoquery affiche une ligne par version trouvée ; vide = inconnu.
    # EN : repoquery prints one line per version found; empty = unknown.
    if [[ -n "$(dnf -q "${ENABLE_ARGS[@]}" repoquery "$p" 2>/dev/null)" ]]; then
        PKGS+=("$p")
    else
        say "  [!] $p introuvable dans les dépôts : ignoré" \
            "  [!] $p not found in the repositories: skipped"
    fi
done

# FR : Garde-fou : sans aucun paquet, inutile de continuer.
# EN : Safeguard: with no package at all there is no point continuing.
if [[ ${#PKGS[@]} -eq 0 ]]; then
    say "Aucun paquet trouvé : dépôts inaccessibles ?" \
        "No package found: repositories unreachable?"
    exit 1
fi

# --------------------------------------------------------------------------
# 3. Téléchargement / Download
# --------------------------------------------------------------------------
# FR : --resolve  calcule les dépendances des paquets demandés.
#      --alldeps  télécharge TOUTES les dépendances, même celles déjà
#                 installées sur cette machine. C'est ce qui simule un poste
#                 « neuf » : sans cette option, dnf sauterait tout ce que
#                 cette machine possède déjà, et l'installation hors-ligne
#                 échouerait faute de dépendances.
#      --destdir  dossier de destination des .rpm
# EN : --resolve  computes the dependencies of the requested packages.
#      --alldeps  downloads ALL dependencies, even those already installed
#                 on this machine. This is what simulates a "brand new"
#                 workstation: without it, dnf would skip everything this
#                 machine already has, and the offline install would fail
#                 for lack of dependencies.
#      --destdir  destination folder for the .rpm files
say "[3/4] Téléchargement (avec toutes les dépendances)..." \
    "[3/4] Downloading (with all dependencies)..."

# FR : On repart d'un dossier vide : des RPM restés d'une exécution précédente
#      (autres dépôts, autres versions) se mélangeraient silencieusement aux
#      nouveaux, et l'installation hors-ligne installerait ce mélange.
# EN : We start from an empty folder: RPMs left over from a previous run
#      (other repos, other versions) would silently mix with the new ones,
#      and the offline install would install that mixture.
rm -f "$DEST"/rpms/*.rpm
rm -rf "$DEST"/rpms/repodata

dnf "${ENABLE_ARGS[@]}" download --resolve --alldeps \
    --destdir "$DEST/rpms" "${PKGS[@]}"

# FR : Les dépôts proposent aussi des builds 32 bits (i686) par compatibilité.
#      Notre poste est en x86_64 : on les écarte pour ne pas gonfler le
#      bundle ni installer de bibliothèques inutiles.
# EN : The repositories also offer 32-bit (i686) builds for compatibility.
#      Our workstation is x86_64: we discard them so the bundle stays small
#      and no useless libraries get installed.
shopt -s nullglob
I686=("$DEST"/rpms/*.i686.rpm)
shopt -u nullglob
if [[ ${#I686[@]} -gt 0 ]]; then
    rm -f "${I686[@]}"
    say "  ${#I686[@]} paquet(s) i686 écarté(s)." \
        "  ${#I686[@]} i686 package(s) discarded."
fi

# --------------------------------------------------------------------------
# 4. Index du dépôt, empreintes, copie de l'outil
#    Repository index, checksums, copying the tool
# --------------------------------------------------------------------------
# FR : rpms/ devient un DÉPÔT LOCAL : install.sh demandera à dnf d'installer
#      `usbguard` PAR NOM depuis ce dépôt. dnf ne prendra alors que ce qui
#      manque sur le poste, et ne rétrogradera jamais un paquet déjà installé
#      (ex. oraclelinux-release 1.0.8 -> 1.0.7). Un poste neuf recevra tout.
# EN : rpms/ becomes a LOCAL REPOSITORY: install.sh will ask dnf to install
#      `usbguard` BY NAME from this repo. dnf then only takes what the
#      workstation lacks, and never downgrades an installed package (e.g.
#      oraclelinux-release 1.0.8 -> 1.0.7). A brand-new machine gets it all.
say "[4/4] Index du dépôt, empreintes SHA256, copie de l'outil..." \
    "[4/4] Repository index, SHA256 checksums, copying the tool..."
createrepo_c --quiet "$DEST/rpms"

# FR : SHA256SUMS couvre les .rpm ET l'index (repodata/) : install.sh peut
#      ainsi détecter un fichier corrompu ou modifié pendant le transport.
#      find -print0 | sort -z | xargs -0 : robuste aux espaces dans les noms.
# EN : SHA256SUMS covers the .rpm files AND the index (repodata/): install.sh
#      can thus detect a file corrupted or modified in transit.
#      find -print0 | sort -z | xargs -0: safe with spaces in names.
( cd "$DEST/rpms" && find . -type f -print0 | sort -z | xargs -0 sha256sum > ../SHA256SUMS )

# FR : L'outil et l'installateur voyagent dans le même dossier.
# EN : The tool and the installer travel in the same folder.
cp "$HERE/install.sh" "$DEST/install.sh"
cp "$HERE/../usb_whitelist.py" "$DEST/usb_whitelist.py"
chmod +x "$DEST/install.sh"

COUNT="$(find "$DEST/rpms" -name '*.rpm' | wc -l)"
echo
say "Bundle prêt dans : $DEST  ($COUNT RPM)" \
    "Bundle ready in: $DEST  ($COUNT RPMs)"
say "Copiez-le sur le poste cible puis lancez : sudo ./install.sh" \
    "Copy it to the target workstation then run: sudo ./install.sh"
