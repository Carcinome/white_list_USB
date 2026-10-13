# usb-whitelist — liste blanche USB pour Oracle Linux 9

Outil en ligne de commande (Python, bibliothèque standard uniquement) pour
autoriser des clés USB sur les postes OL 9.6 custom. Réservé au groupe `wheel`.

## Comment ça marche

```
 utilisateur (wheel)
        │  usb-whitelist add
        ▼
 usb_whitelist.py ──► /usr/bin/usbguard (CLI) ──► démon usbguard ──► noyau Linux
                                                       │
                                            /etc/usbguard/rules.conf
                                          (la liste blanche, pour TOUTE la machine)
```

* **Le blocage est fait par USBGuard**, pas par notre script. Le noyau refuse
  d'« autoriser » (`authorized=0`) tout périphérique qui ne correspond à aucune règle.
* **La liste est liée à la machine**, pas à un compte : `/etc/usbguard/rules.conf`
  s'applique quel que soit l'utilisateur connecté, ancien ou futur.
* **Une règle** = `allow id <VID:PID> serial "<série>" name "<nom>" hash "<empreinte>"`.
  - `serial` : distingue deux clés du même modèle.
  - `hash` : empreinte des descripteurs USB ; une clé reprogrammée (BadUSB, qui
    se fait passer pour un clavier) change d'empreinte et est bloquée.
  - Pas de `via-port` : la clé marche sur n'importe quelle prise.

## Restriction au groupe wheel (3 couches)

| Couche | Où | Effet |
|---|---|---|
| Fichier `root:wheel 0750` | `/usr/local/bin/usb-whitelist` | un non-wheel ne peut pas l'exécuter |
| Contrôle Python | `is_wheel_member()` | message clair + refus |
| ACL du démon | `usbguard add-user -g wheel …` | **la vraie barrière** : même en contournant le script, `usbguard` refuse un non-wheel |

## Utilisation

```bash
usb-whitelist            # menu interactif
usb-whitelist devices    # ce qui est branché, et son état
usb-whitelist add        # autoriser un périphérique bloqué (demande confirmation)
usb-whitelist list       # voir la liste blanche
usb-whitelist remove     # retirer une règle
usb-whitelist status     # état du service
```

Chaque ajout/retrait est tracé : `journalctl -t usb-whitelist`.

## Déploiement

1. **Sur une machine OL 9 connectée** (dépôts accessibles) :
   ```bash
   ./deploy/prepare_bundle.sh ./bundle
   ```
   → télécharge usbguard + toutes ses dépendances (`dnf download --resolve --alldeps`,
   comme sur un poste « neuf »).
2. Copier `bundle/` sur le poste cible, puis :
   ```bash
   sudo ./install.sh
   ```

## Tests

```bash
python -m unittest discover -s tests -v    # logique pure, sans usbguard
```

## Retour arrière

```bash
sudo systemctl disable --now usbguard
sudo rm /usr/local/bin/usb-whitelist
```
