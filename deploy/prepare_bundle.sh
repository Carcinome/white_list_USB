#!/usr/bin/env bash
# prepare_bundle.sh — à lancer sur UNE machine Oracle Linux 9 AYANT accès aux dépôts.
# Produit un dossier autonome (RPM + outil + installateur) à copier sur les postes
# hors-ligne (clé USB, partage...).
#
# Usage : ./prepare_bundle.sh [dossier_de_sortie]      (défaut : ./bundle)
set -euo pipefail

DEST="${1:-./bundle}"
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$DEST/rpms"

# `dnf download` vient du plugin dnf-plugins-core (déjà présent en général).
dnf -y install dnf-plugins-core >/dev/null

# Paquets voulus. On teste leur existence avant : si un nom n'existe pas dans
# les dépôts, on le signale au lieu de faire échouer tout le téléchargement.
WANTED=(usbguard usbguard-tools usbguard-selinux python3)
PKGS=()
for p in "${WANTED[@]}"; do
    if [[ -n "$(dnf -q repoquery "$p" 2>/dev/null)" ]]; then
        PKGS+=("$p")
    else
        echo "[!] $p introuvable dans les dépôts : ignoré"
    fi
done

# --resolve : calcule les dépendances.
# --alldeps : télécharge TOUTES les dépendances, même celles déjà installées
#             ici. C'est ce qui simule un poste "neuf" : sans cette option, dnf
#             sauterait tout ce que cette machine possède déjà.
dnf download --resolve --alldeps --destdir "$DEST/rpms" "${PKGS[@]}"

# Empreintes : permettent de détecter une copie corrompue sur le support.
( cd "$DEST/rpms" && sha256sum ./*.rpm > ../SHA256SUMS )

# L'outil et l'installateur voyagent dans le même dossier.
cp "$HERE/install.sh" "$DEST/install.sh"
cp "$HERE/../usb_whitelist.py" "$DEST/usb_whitelist.py"
chmod +x "$DEST/install.sh"

echo
echo "Bundle prêt dans : $DEST  ($(ls "$DEST/rpms" | wc -l) RPM)"
echo "Copiez-le sur le poste cible puis lancez : sudo ./install.sh"
