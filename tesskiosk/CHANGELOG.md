## 0.8.2
- Clavier virtuel aussi dans les pages intégrées (Tessfood, Tessbet, Tessvine…).

## 0.8.1
- Présence lue directement auprès de Home Assistant (l'écran ne se rallumait pas au retour).

## 0.8.0
- Écran éteint quand personne n'est à la maison (option screen_off_when_away, par défaut Thomas et Alexia), rallumé dès que l'un revient. Un toucher le rallume ; il se rééteint après 5 min sans toucher (away_wake_minutes) si personne n'est revenu.

## 0.7.1
- Tableaux remplacés par des œuvres modernes de style scandinave, dessinées sur place (montagnes en aplats, fjords, soleil, sapins, aurores), en plein écran sans bordure ni légende. Plus de téléchargement.

## 0.7.0
- Tableaux : écarter deux doigts sur le tableau de bord affiche des paysages scandinaves (peintures du domaine public, Wikimedia Commons), un nouveau toutes les 15 minutes. Un toucher les arrête.
- Pincer vers l'intérieur ramène aussi à l'accueil depuis n'importe quel onglet du tableau de bord.

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
