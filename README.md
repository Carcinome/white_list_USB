# usb-whitelist — Oracle Linux 9

**[Français](#français)** · **[English](#english)**

---

<a id="français"></a>
# 🇫🇷 Français

Outil en ligne de commande (Python, bibliothèque standard uniquement) pour
autoriser des clés USB sur les postes Oracle Linux 9.6 « custom ». Il est
réservé aux membres du groupe `wheel`.

## 1. Comment ça marche

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
  d'activer (« autoriser ») tout périphérique qui ne correspond à aucune règle.
* **La liste est liée à la machine**, pas à un compte : `/etc/usbguard/rules.conf`
  s'applique quel que soit l'utilisateur connecté, ancien ou futur.
* **Une règle** ressemble à :
  `allow id 0781:5581 serial "4C53…" name "Ultra USB 3.0" hash "Xy/…"`
  - `id` : constructeur:produit.
  - `serial` : numéro de série ; distingue deux clés du même modèle.
  - `hash` : empreinte des descripteurs USB ; une clé reprogrammée (BadUSB, qui
    se fait passer pour un clavier) change d'empreinte et reste bloquée.
  - pas de `via-port` : la clé fonctionne sur n'importe quelle prise.

## 2. Glossaire

| Terme | Signification |
|---|---|
| **Daemon / démon / service** | Programme qui tourne en permanence en arrière-plan et réagit aux événements (ici : un branchement USB). Démarré par `systemd`. |
| **USBGuard** | Le démon qui décide d'autoriser ou de bloquer chaque périphérique USB selon des règles. |
| **Règle** | Une ligne de `rules.conf`. La première qui correspond à un périphérique l'emporte. |
| **Politique implicite** | Ce qui arrive quand aucune règle ne correspond. Ici : `block`. |
| **wheel** | Groupe Unix des administrateurs autorisés à utiliser `sudo`. |
| **RPM** | Format de paquet logiciel d'Oracle Linux / RHEL. |
| **Hors-ligne** | Poste sans accès aux dépôts : on lui apporte tous les RPM à l'avance. |

## 3. Restriction au groupe wheel (3 couches)

| Couche | Où | Effet |
|---|---|---|
| Fichier `root:wheel 0750` | `/usr/local/bin/usb-whitelist` | un non-wheel ne peut pas l'exécuter |
| Contrôle Python | `is_wheel_member()` | message clair + refus |
| ACL du démon | `usbguard add-user -g wheel …` | **la vraie barrière** : même en contournant le script, le démon refuse un non-wheel |

## 4. Utilisation

```bash
usb-whitelist            # menu interactif (choix de la langue d'abord)
usb-whitelist devices    # ce qui est branché, et son état
usb-whitelist add        # autoriser un périphérique bloqué (avec confirmation)
usb-whitelist list       # voir la liste blanche
usb-whitelist remove     # retirer une règle
usb-whitelist status     # état du service
```

**Langue** (par ordre de priorité) : option `--lang fr|en`, puis variable
`USB_WHITELIST_LANG`, puis menu de langue si on lance le menu interactif.
Sans terminal (script, cron) : français, sans jamais attendre de réponse.

**Traçabilité** : chaque ajout/retrait est écrit dans le journal système, en
anglais : `journalctl -t usb-whitelist`.

## 5. Déploiement

1. **Sur une machine OL9 connectée** (dépôts accessibles) :
   ```bash
   ./deploy/prepare_bundle.sh ./bundle
   ```
   Télécharge usbguard et toutes ses dépendances
   (`dnf download --resolve --alldeps`, comme pour un poste « neuf »), calcule
   les empreintes SHA256 et rassemble l'outil et l'installateur.
2. **Copier `bundle/` sur le poste cible**, brancher clavier/souris USB, puis :
   ```bash
   sudo ./install.sh        # -y pour ne pas demander de confirmation
   ```
   Le script peut être relancé : une liste blanche existante est conservée.

## 6. Tests

```bash
python -m unittest discover -s tests -v    # logique pure, sans usbguard
```

Ils couvrent la lecture des sorties usbguard, la construction des règles
(dont l'injection par un nom de clé piégé) et la cohérence des traductions
FR/EN.

## 7. À vérifier lors du premier essai sur la VM

Ces points reposent sur la documentation d'usbguard, pas sur un test réel :

1. Les paquets existent : `dnf info usbguard usbguard-tools usbguard-selinux`.
2. Les options d'`add-user` : `usbguard add-user --help`.
3. `append-rule` persiste dans `rules.conf` après redémarrage du service.
4. Le format de `usbguard list-devices` correspond à ce que lit `parse_line`.
5. Le fichier d'ACL créé est bien `/etc/usbguard/IPCAccessControl.d/:wheel`.

## 8. Retour arrière

```bash
sudo systemctl disable --now usbguard
sudo rm /usr/local/bin/usb-whitelist
```

---

<a id="english"></a>
# 🇬🇧 English

Command-line tool (Python, standard library only) to whitelist USB keys on
custom Oracle Linux 9.6 workstations. Restricted to members of the `wheel`
group.

## 1. How it works

```
 user (wheel)
      │  usb-whitelist add
      ▼
 usb_whitelist.py ──► /usr/bin/usbguard (CLI) ──► usbguard daemon ──► Linux kernel
                                                        │
                                             /etc/usbguard/rules.conf
                                        (the whitelist, for the WHOLE machine)
```

* **Blocking is done by USBGuard**, not by our script. The kernel refuses to
  enable ("authorize") any device that matches no rule.
* **The list belongs to the machine**, not to an account:
  `/etc/usbguard/rules.conf` applies whoever is logged in, past or future.
* **A rule** looks like:
  `allow id 0781:5581 serial "4C53…" name "Ultra USB 3.0" hash "Xy/…"`
  - `id`: vendor:product.
  - `serial`: serial number; tells two keys of the same model apart.
  - `hash`: fingerprint of the USB descriptors; a reprogrammed key (BadUSB,
    posing as a keyboard) gets a different fingerprint and stays blocked.
  - no `via-port`: the key works in any socket.

## 2. Glossary

| Term | Meaning |
|---|---|
| **Daemon / service** | A program that runs permanently in the background and reacts to events (here: a USB plug-in). Started by `systemd`. |
| **USBGuard** | The daemon that allows or blocks each USB device according to rules. |
| **Rule** | One line of `rules.conf`. The first one matching a device wins. |
| **Implicit policy** | What happens when no rule matches. Here: `block`. |
| **wheel** | Unix group of administrators allowed to use `sudo`. |
| **RPM** | Software package format of Oracle Linux / RHEL. |
| **Offline** | A workstation with no repository access: all RPMs are brought in advance. |

## 3. Restriction to the wheel group (3 layers)

| Layer | Where | Effect |
|---|---|---|
| File `root:wheel 0750` | `/usr/local/bin/usb-whitelist` | a non-wheel user cannot execute it |
| Python check | `is_wheel_member()` | clear message + refusal |
| Daemon ACL | `usbguard add-user -g wheel …` | **the real barrier**: even bypassing the script, the daemon refuses a non-wheel user |

## 4. Usage

```bash
usb-whitelist            # interactive menu (language choice first)
usb-whitelist devices    # what is plugged in, and its state
usb-whitelist add        # allow a blocked device (with confirmation)
usb-whitelist list       # show the whitelist
usb-whitelist remove     # remove a rule
usb-whitelist status     # service state
```

**Language** (in priority order): `--lang fr|en` option, then the
`USB_WHITELIST_LANG` variable, then the language menu when starting the
interactive menu. Without a terminal (script, cron): French, and never
waits for an answer.

**Audit trail**: every addition/removal is written to the system journal, in
English: `journalctl -t usb-whitelist`.

## 5. Deployment

1. **On a connected OL9 machine** (repositories reachable):
   ```bash
   ./deploy/prepare_bundle.sh ./bundle
   ```
   Downloads usbguard and all its dependencies
   (`dnf download --resolve --alldeps`, as for a "brand new" machine),
   computes SHA256 checksums and gathers the tool and the installer.
2. **Copy `bundle/` to the target workstation**, plug in the USB
   keyboard/mouse, then:
   ```bash
   sudo ./install.sh        # -y to skip the confirmation prompt
   ```
   The script can be run again: an existing whitelist is kept.

## 6. Tests

```bash
python -m unittest discover -s tests -v    # pure logic, no usbguard needed
```

They cover parsing of usbguard output, rule building (including injection
through a booby-trapped key name) and consistency of the FR/EN translations.

## 7. To check on the first VM run

These points rely on usbguard's documentation, not on a real test:

1. The packages exist: `dnf info usbguard usbguard-tools usbguard-selinux`.
2. The `add-user` options: `usbguard add-user --help`.
3. `append-rule` persists into `rules.conf` after a service restart.
4. The format of `usbguard list-devices` matches what `parse_line` reads.
5. The ACL file created is indeed `/etc/usbguard/IPCAccessControl.d/:wheel`.

## 8. Rollback

```bash
sudo systemctl disable --now usbguard
sudo rm /usr/local/bin/usb-whitelist
```
