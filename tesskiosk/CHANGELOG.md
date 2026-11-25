## 0.6.1
- Une caméra affichée en grand reste affichée : pas de retour automatique à l'accueil.

## 0.6.0
- Écran éteint la nuit (options screen_off_from / screen_off_to, 00:30 → 07:00 par défaut). Un toucher le rallume 1 minute.
- Sites ouverts depuis un lien (École Directe…) : bouton 🏠 ou pincement à deux doigts pour revenir au tableau de bord, clavier virtuel disponible, fermeture automatique après le délai d'inactivité.

## 0.5.0
- Clavier virtuel AZERTY : il s'affiche tout seul quand on touche un champ de saisie (option virtual_keyboard).

## 0.4.2
- Polices Roboto / Liberation : les pages des extensions (Tessbet…) s'affichent comme sur Mac/Windows.

## 0.4.1
- Retour à l'accueil : l'activité dans les pages des extensions (Tessbet, Tessfood…) compte aussi.

## 0.4.0
- Retour automatique à l'accueil après X minutes sans activité (option return_home_minutes, 0 = désactivé).

## 0.3.2
- Vérification du masquage du curseur (toucher / inactivité).

## 0.3.1
- Journal : utilisation de la puce graphique par Chrome.

## 0.3.0
- Base Alpine (comme HAOS Kiosk) : souris, clavier et tactile détectés.
- Le script ne s'arrête plus sur une erreur mineure.

## 0.2.4
- Diagnostic des périphériques d'entrée.

## 0.2.3
- Souris, clavier et tactile reconnus par l'affichage (udev).

## 0.2.2
- Journal plus détaillé en cas d'arrêt.

## 0.2.1
- Souris, clavier et tactile : initialisation des périphériques d'entrée corrigée.

## 0.2.0
- Appairage Bluetooth d'une souris ou d'un clavier (option bluetooth_pairing).
- udev : les erreurs sans gravité ne sont plus signalées.

## 0.1.0
- Première version : Chromium plein écran, décodage vidéo matériel, connexion automatique, API locale.
