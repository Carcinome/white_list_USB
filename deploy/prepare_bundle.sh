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
#      modifié dans /etc/yum.repos.d. Défaut : "ol9_baseos_latest ol9_appstream".
#      USB_WHITELIST_REPOS="" = n'en activer aucun (dépôts déjà actifs).
# EN : Repositories: the custom OS disables them. This script enables, FOR ITS
#      OWN COMMANDS ONLY (--enablerepo), the repos in USB_WHITELIST_REPOS.
#      Nothing is changed in /etc/yum.repos.d. Default: "ol9_baseos_latest
#      ol9_appstream". USB_WHITELIST_REPOS="" = enable none (already active).

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
REPOS="${USB_WHITELIST_REPOS-ol9_baseos_latest ol9_appstream}"
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

# --------------------------------------------------------------------------
# 2. Quels paquets ? / Which packages?
# --------------------------------------------------------------------------
# FR : usbguard          = le démon
#      usbguard-tools    = la commande `usbguard` (selon la version, elle
#                          peut être dans le paquet principal ou ici)
#      usbguard-selinux  = la politique SELinux qui l'autorise à fonctionner
#      python3           = l'interpréteur de notre outil
# EN : usbguard          = the daemon
#      usbguard-tools    = the `usbguard` command (depending on the version
#                          it may be in the main package or here)
#      usbguard-selinux  = the SELinux policy allowing it to run
#      python3           = the interpreter for our tool
WANTED=(usbguard usbguard-tools usbguard-selinux python3)
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
# 4. Empreintes + copie de l'outil / Checksums + copying the tool
# --------------------------------------------------------------------------
# FR : SHA256SUMS permet à install.sh de détecter un fichier corrompu ou
#      modifié pendant le transport (clé USB, copie réseau...).
# EN : SHA256SUMS lets install.sh detect a file corrupted or modified in
#      transit (USB key, network copy...).
say "[4/4] Empreintes SHA256 et copie de l'outil..." \
    "[4/4] SHA256 checksums and copying the tool..."
( cd "$DEST/rpms" && sha256sum ./*.rpm > ../SHA256SUMS )

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
