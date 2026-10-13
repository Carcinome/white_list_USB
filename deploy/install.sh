#!/usr/bin/env bash
# install.sh — installation HORS-LIGNE sur le poste cible (à lancer en root).
# Usage : sudo ./install.sh [-y]      (-y : ne pas demander de confirmation)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ASSUME_YES=0
[[ "${1:-}" == "-y" ]] && ASSUME_YES=1

[[ $EUID -eq 0 ]] || { echo "Ce script doit être lancé en root (sudo)."; exit 1; }
[[ -d "$HERE/rpms" ]] || { echo "Dossier rpms/ introuvable à côté du script."; exit 1; }

# --- 1. Intégrité du support ------------------------------------------------
echo "[1/6] Vérification des empreintes SHA256..."
( cd "$HERE/rpms" && sha256sum --quiet -c ../SHA256SUMS )

# --- 2. Installation des RPM, sans réseau -----------------------------------
# --disablerepo='*'      : ne contacte aucun dépôt (poste hors-ligne)
# localpkg_gpgcheck=1    : vérifie aussi la signature GPG des RPM locaux
echo "[2/6] Installation des paquets..."
dnf -y install --disablerepo='*' --setopt=localpkg_gpgcheck=1 "$HERE"/rpms/*.rpm

# --- 3. Politique de base ---------------------------------------------------
# Piège classique : si on active "tout bloquer" sans règle, le clavier/souris
# USB est bloqué aussi et on perd la main. On photographie donc ce qui est
# branché MAINTENANT et on l'autorise : c'est le point de départ.
RULES=/etc/usbguard/rules.conf
echo "[3/6] Génération de la politique de base (périphériques actuellement branchés)"
TMP="$(mktemp)"
usbguard generate-policy > "$TMP"
echo "-------- périphériques qui resteront autorisés --------"
cat "$TMP"
echo "-------------------------------------------------------"
echo "Tout le reste sera BLOQUÉ. Clavier/souris USB branchés ? (sinon: Ctrl+C)"
if [[ $ASSUME_YES -eq 0 ]]; then
    read -r -p "Continuer ? [o/N] " ans
    [[ "$ans" =~ ^[oOyY] ]] || { rm -f "$TMP"; echo "Abandon."; exit 1; }
fi
[[ -f "$RULES" ]] && cp -a "$RULES" "$RULES.bak.$(date +%Y%m%d-%H%M%S)"
install -m 0600 -o root -g root "$TMP" "$RULES"   # usbguard exige 0600
rm -f "$TMP"

# --- 4. Réglage du démon ----------------------------------------------------
# ImplicitPolicyTarget=block : ce qui ne correspond à aucune règle est bloqué.
# *DevicePolicy=apply-policy : appliquer les règles aux périphériques branchés
#                              avant/après le démarrage (et pas "tout autoriser").
echo "[4/6] Configuration du démon"
CONF=/etc/usbguard/usbguard-daemon.conf
set_opt() {   # set_opt CLE VALEUR : remplace la ligne, ou l'ajoute
    if grep -qE "^#?\s*$1=" "$CONF"; then
        sed -i -E "s|^#?\s*$1=.*|$1=$2|" "$CONF"
    else
        echo "$1=$2" >> "$CONF"
    fi
}
set_opt ImplicitPolicyTarget block
set_opt PresentDevicePolicy apply-policy
set_opt InsertedDevicePolicy apply-policy
set_opt PresentControllerPolicy keep

# --- 5. Qui a le droit de piloter le démon : le groupe wheel ----------------
# C'est la barrière la plus solide : elle est appliquée par le démon lui-même
# (identité de l'appelant vérifiée par le noyau), pas par notre script Python.
echo "[5/6] Droits du groupe wheel sur le démon"
usbguard add-user -g wheel --devices=modify,list,listen --policy=modify,list --exceptions=listen

# --- 6. L'outil + démarrage -------------------------------------------------
# root:wheel 0750 : un utilisateur hors wheel ne peut même pas l'exécuter.
echo "[6/6] Installation de l'outil et démarrage du service"
install -m 0750 -o root -g wheel "$HERE/usb_whitelist.py" /usr/local/bin/usb-whitelist
systemctl enable --now usbguard
systemctl restart usbguard

echo
echo "Terminé. Essayez :  usb-whitelist status"
echo "(Les membres de wheel doivent se reconnecter si leur groupe vient d'être modifié.)"
