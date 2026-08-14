# Attributions et licences des modèles

Whisperty fonctionne 100 % localement. Les modèles ci-dessous ne sont **pas** des
dépendances du code : ce sont des fichiers que l'utilisateur télécharge (ou que
l'installeur embarque, pour Whisper) et qui restent sur sa machine. Cette page
documente leur origine et leur licence, l'attribution étant exigée par certaines
d'entre elles.

## Modèle de transcription — Whisper (OpenAI) via faster-whisper

- Modèles `faster-whisper-<taille>` distribués par le projet
  [Systran/faster-whisper](https://huggingface.co/Systran) (conversion CTranslate2
  des modèles Whisper d'OpenAI).
- Whisper est publié par OpenAI sous licence **MIT**.
- Téléchargé à la demande (bannière du tableau de bord) ou embarqué par
  `scripts/build.ps1` lors de la construction de l'installeur.

## Modèle de diarisation des locuteurs — WeSpeaker ResNet34 (optionnel)

Utilisé uniquement si `conference.speaker_diarization.backend: onnx` (option, le
défaut `mfcc` ne télécharge rien).

- Fichier : `voxceleb_resnet34_LM.onnx` (26 530 309 octets), export ONNX officiel du
  dépôt public [Wespeaker/wespeaker-voxceleb-resnet34-LM](https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM).
- **Révision épinglée** : commit `f0c48c298fd835726c27956a5d617bad7115627e`. Le
  téléchargement vise ce commit précis (et vérifie la taille obtenue) : le binaire
  exécuté ne peut pas changer sous nos pieds si le dépôt évolue. Aucun jeton
  d'authentification n'est envoyé (`token=False`) puisque le dépôt est public.
- Projet amont : [WeSpeaker](https://github.com/wenet-e2e/wespeaker) (Apache-2.0
  pour le code).
- Le modèle est entraîné sur **VoxCeleb2**, jeu de données publié sous
  **CC-BY-4.0**. Les poids diffusés par l'organisation WeSpeaker portent également
  la mention **CC-BY-4.0** : leur utilisation, y compris commerciale, est autorisée
  **sous réserve d'attribution** — d'où cette page.
- Attribution : *WeSpeaker (Wang et al.), modèle `voxceleb_resnet34_LM`, entraîné
  sur VoxCeleb2 (Chung et al., Nagrani et al.), CC-BY-4.0.*
- **Non redistribué** avec Whisperty : le fichier est téléchargé depuis Hugging
  Face uniquement lorsque l'utilisateur clique sur « Télécharger le modèle » dans
  l'écran Configuration, puis n'est plus jamais contacté (fonctionnement hors-ligne).
  Si une future version l'embarquait dans l'installeur, la licence CC-BY-4.0
  devrait y être jointe intégralement.

## Bibliothèques

Les dépendances Python (faster-whisper, CTranslate2, onnxruntime, sounddevice,
soundcard, pystray, pynput, pywebview…) conservent leurs licences respectives,
consultables dans leurs distributions (`pip show <paquet>`).
