# TessKiosk

Extension Home Assistant qui affiche un tableau de bord **en plein écran avec Chrome** sur l'écran branché au serveur (Home Assistant OS, mini-PC x86-64).

- Chrome (Chromium) avec **décodage vidéo matériel** (Intel/AMD) : caméras fluides.
- Connexion automatique avec un utilisateur Home Assistant dédié (aucun mot de passe stocké ici : il se saisit dans la configuration de l'extension).
- Écran tactile USB reconnu automatiquement.
- Petite API locale (127.0.0.1 uniquement) pour les automatisations.

## Installation
1. Paramètres → Modules complémentaires → Boutique → ⋮ → Dépôts → ajouter `https://github.com/Tessman404/tesskiosk`.
2. Installer **TessKiosk**, puis dans Configuration : `ha_username`, `ha_password` (utilisateur non administrateur), `dashboard`.
3. Brancher l'écran (HDMI) et démarrer.

## API locale (pour `rest_command`)
| Requête | Effet |
|---|---|
| `GET  http://127.0.0.1:8099/status` | Page affichée |
| `POST http://127.0.0.1:8099/home` | Revenir au tableau de bord |
| `POST http://127.0.0.1:8099/navigate` `{"path": "/cuisine-24/cameras"}` | Ouvrir une page |
| `POST http://127.0.0.1:8099/reload` | Recharger la page |
| `POST http://127.0.0.1:8099/screen/on` / `screen/off` | Allumer / éteindre l'écran |

Le démarrage de l'affichage (X) est adapté de [HAOS Kiosk Display](https://github.com/puterboy/HAOS-kiosk) (Jeff Kosowsky, GPL-2.0).
