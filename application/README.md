# PDF Teleporter

Compresse des documents PDF en archives `.psdi` compactes pour les transmettre
sur des liaisons radio à bande étroite (TNC Packet, VARA HF/FM, pièces jointes
Winlink Express), et les recompose en PDF lisibles à la réception.

Un SITREP de 600 ko qui demanderait un quart d'heure en VARA HF tient dans
environ 120 ko et passe en trois minutes, sa mise en page intacte à l'arrivée.

---

## Origine et attribution

Cette application est **dérivée du travail de F1GBD (ADRASEC 77 – FNRASEC)**,
auteur du PDFteleporter original et du format `.psdi`. La conception du format,
le choix des deux modes d'encodage, les préréglages de qualité et l'essentiel
des correctifs de rendu qui font que la recomposition ressemble au document
d'origine sont son travail, pas le nôtre.

L'original était distribué sous **GNU GPL v3** sous forme de binaire PyInstaller
Windows, sans code source publié. La GPL v3 accorde explicitement le droit
d'accéder au source correspondant et de modifier l'œuvre ; le source a donc été
reconstitué depuis le bytecode livré, puis réécrit. Le dossier `reference/` de
l'archive complète conserve ce matériel de reconstitution.

Ce dérivé reste sous **GNU GPL v3**, comme l'exige la licence de l'original.
PyMuPDF, utilisé par le moteur, est sous AGPL v3.

**Le format `.psdi` n'a pas été modifié.** C'est un contrat d'interopérabilité :
une station qui utilise la version de F1GBD doit pouvoir recomposer ce que cette
version émet, et réciproquement. Toutes les optimisations décrites plus bas ont
été retenues précisément parce qu'elles restent lisibles par un décodeur écrit
sur la spécification d'origine.

## Périmètre

Cet outil compresse et recompose, rien de plus. Il ne parle pas à la radio.

L'émission est le travail de Winlink, avec VARA ou Direwolf selon la liaison :
l'archive `.psdi` part en pièce jointe d'un message Winlink Express, et la
station réceptrice la recompose avec ce même outil. Le moteur original de F1GBD
comportait une couche de fragmentation TNC et de tramage VARA (`prepare_for_tnc`,
`prepare_for_vara` et leurs réciproques) parce qu'il alimentait son application
TCQ ; elle n'a pas été reprise, et ne le sera pas.

Cette décision a une conséquence pratique : **la contrainte qui compte est la
limite de pièce jointe Winlink**, pas le débit du modem. Les estimations de
temps par mode radio restent affichées parce qu'elles renseignent sur la durée
de la session, mais c'est l'avertissement de dépassement de taille qui décide
si le message passera.

---

## Installation

```
pip install -r requirements.txt
python pdfteleporter.py
```

Python 3.10 ou ultérieur. Aucune compilation nécessaire pour un usage depuis les
sources.

## Utilisation

### Interface graphique

Compression à gauche, recomposition à droite, journal horodaté en bas. La
sélection d'une archive déclenche immédiatement la vérification de sa signature
et de son CRC : un fichier abîmé en transit est repéré avant qu'on perde du
temps à le recomposer.

L'interface est en français et suit le réglage clair/sombre de Windows.

### Menu contextuel de l'Explorateur

*Outils → Ajouter au menu contextuel de l'Explorateur* enregistre deux verbes :

- clic droit sur un `.pdf` → **Téléporter : compresser en .psdi**
- clic droit sur un `.psdi` → **Téléporter : recomposer le PDF**

Sous Windows 11, ces entrées apparaissent sous *Afficher plus d'options* (ou
Maj+F10). Atteindre le menu de premier niveau exige une extension shell MSIX
signée, disproportionnée pour un outil employé quelques fois par exercice.

### Ligne de commande

```
python pdfteleporter.py compress SITREP.pdf -q low
python pdfteleporter.py rebuild  SITREP.psdi -o sortie.pdf
python pdfteleporter.py inspect  SITREP.psdi
python pdfteleporter.py presets
python pdfteleporter.py shell install
```

## Préréglages de qualité

| Préréglage | ppp | JPEG | Coordonnées | Liaison visée |
|---|---|---|---|---|
| `ultra_low` | 72 | 20 | point entier | Urgence, Packet 1200 bauds |
| `low` | 90 | 30 | point entier | Packet 9600, VARA HF lent |
| `medium` | 120 | 45 | 0,1 pt | VARA HF / FM, le choix courant |
| `high` | 150 | 55 | 0,1 pt | VARA FM rapide |

*Texte seul* supprime toutes les images : c'est le transfert le plus rapide
possible quand seul le libellé compte.

---

## Comment l'encodage fonctionne

Deux encodages partagent un même conteneur.

**Structuré** garde le texte comme texte : chaque fragment porte sa boîte
englobante, sa taille, sa couleur et une classification grossière de police ;
les images sont extraites une fois par xref puis replacées par référence. Les
glyphes ne deviennent jamais des pixels, ce qui rend ce mode bien plus compact
pour un document né numérique.

**Image de page** aplatit chaque page en un seul JPEG. Plus volumineux, mais
c'est la seule chose qui fonctionne pour un scan, et le seul chemin qui compose
correctement les masques d'encre et respecte la rotation de page.

Le choix n'est pas laissé à l'opérateur. Un arrêté préfectoral scanné ressemble
à un PDF mais se comporte comme une photographie ; l'encoder en structuré
produit une page pivotée de 90° sur fond noir. La détection repère la rotation
et les pages majoritairement image sans texte, et bascule d'elle-même en
journalisant pourquoi.

### Illustrations vectorielles

Un graphique en secteurs est fait d'arcs, une courbe de segments obliques : le
mode structuré n'a aucun moyen de les décrire, puisqu'il ne stocke que du texte
et des rectangles. Les zones de dessin qui ne se réduisent pas à des rectangles
sont donc détectées, rendues à la résolution du préréglage, et rangées comme
des images ordinaires — un décodeur écrit sur la spécification d'origine les
affiche sans rien connaître de ce mécanisme.

Le texte et les rectangles couverts par une de ces zones sont retirés du
manifeste : ils sont déjà dans l'image, et les dessiner une seconde fois
poserait une copie nette sur une copie floue. Les images qui chevauchent une
zone (ombres portées, pastilles de légende) y sont absorbées, faute de quoi
elles seraient extraites séparément.

Le rendu passe par `get_pixmap`, qui compose la zone comme le ferait un
lecteur : transparence, masques et modes de fusion sortent justes, ce que
l'extraction objet par objet ne sait pas faire.

Contrepartie assumée : les étiquettes d'un graphique deviennent de l'image et
ne sont donc plus sélectionnables. Sur un bulletin de huit pages, cela
représente environ un pour cent des mots — auparavant ces mêmes étiquettes
étaient soit absentes, soit des taches noires.

### Correctifs de rendu hérités de l'original

Ces traitements viennent de F1GBD et ont été conservés à l'identique. Ils sont
le fruit de son travail sur de vrais documents et chacun corrige un défaut
visible en réception :

- **Ligatures LibreOffice.** LibreOffice écrit les ligatures `ti` et `tt` en
  points de code non standard (U+019F, U+01A9) qui arrivent en caractères de
  remplacement. Elles sont décomposées à l'extraction ; là où le glyphe est déjà
  perdu, la substitution est déduite du contexte.
- **Tracés multi-segments.** La chaîne PDF de Microsoft dessine les cadres en
  chemins multi-segments. Prendre la boîte englobante globale d'un chemin plutôt
  que de parcourir ses sous-éléments peint un rectangle noir pleine page.
- **Cases à cocher AcroForm.** La coche vit dans le flux d'apparence `/AP /N` du
  widget, que ni `get_text` ni `get_drawings` ne restituent. Les cases cochées
  sont relevées séparément et redessinées en polyligne géométrique, sans
  dépendance de police.
- **Interligne des très petites polices.** Une hauteur de rendu proportionnelle
  (`line_height × 1,15` ou `fontsize × 1,20`, le maximum des deux) et une marge
  horizontale d'un demi-point évitent le chevauchement des lignes et le
  débordement de cellule sur les tableaux Excel à polices de ~4 pt.

---

## Ce qui change par rapport à l'original

### Architecture

| Original | Ici |
|---|---|
| Tkinter | PyQt6, style Fusion forcé |
| Moteur et IHM en deux fichiers monolithiques | Moteur séparé (`format` / `engine` / `presets`), IHM isolée |
| Pas de ligne de commande | CLI headless complet |
| Un exécutable fenêtré | Deux : fenêtré (IHM) et console (CLI) |
| Aucune intégration bureau | Menu contextuel Explorateur |
| Pas d'installateur | Installateur Inno Setup 7 |
| Pas d'icône ni de métadonnées | Icône multi-résolution et ressources de version |

Le moteur d'origine était déjà une bibliothèque autonome sans dépendance à
l'IHM — l'auteur l'avait conçue pour être appelée depuis son application TCQ.
Cette séparation a été préservée et renforcée : le clic droit dans l'Explorateur
appelle le CLI, pas l'interface graphique, et ne démarre donc pas Qt pour une
simple conversion.

### Compression : −21 % à `medium`, −27 % à `low`

Le profilage montre que le texte pèse 91 % du manifeste compressé ; les
rectangles vectoriels tombent de 28 837 à 379 octets parce que LZMA exploite
déjà leur répétition. Optimiser ailleurs était sans objet — le dédoublonnage de
rectangles a été implémenté puis retiré, gain mesuré exactement nul.

| Changement | Effet |
|---|---|
| Omission des clés de fragment égales à leur défaut (`f`, `fa`, `c`) | −2 % |
| Omission de la boîte du fragment sur une ligne à fragment unique | −18 % |
| LZMA2 `pb=0` avec recherche de correspondances extrême | −2 % |
| Coordonnées au point entier sur les deux préréglages bas | −6 % |

Mesuré de bout en bout sur un rapport de six pages : **5 223 → 4 116 octets à
`medium`**, et **3 812 octets à `low`**.

Le cas du fragment unique pèse le plus lourd parce qu'un fragment seul couvre
exactement sa ligne : sa boîte était donc stockée deux fois. Le lecteur retombe
déjà sur la boîte de ligne quand un fragment n'en a pas — l'omission est donc
transparente pour tout décodeur.

L'arrondi au point entier a été vérifié plutôt que supposé : un tableau de douze
lignes en texte 7 pt se recompose sans débordement de cellule ni chevauchement.
L'arrondi au demi-point a été mesuré aussi et s'avère *pire* que pas d'arrondi
du tout — il ajoute `,5` à des valeurs qui étaient entières.

Le mode image gagne séparément : le JPEG est écrit en progressif, identique
pixel pour pixel et 5 à 9 % plus petit, et une page sans couleur significative
perd ses plans de chrominance. Un arrêté scanné à l'encre noire sort environ
14 % plus léger sans rien perdre.

Deux changements mineurs au passage : les charges utiles JPEG ne sont plus
recompressées en LZMA quand cela les gonfle, le drapeau enregistrant le choix ;
et les images sont triées numériquement à l'écriture, si bien que deux
compressions du même document produisent des archives identiques au bit près.

### Correctifs

**Rotation ignorée en mode texte seul.** L'original désactivait entièrement la
détection automatique de mode dès que « texte seul » était coché
(`if mode == 'struct' and not skip_images:`). Un document pivoté cochant cette
case se recomposait donc de travers. Ignorer les images est un choix légitime ;
ignorer une rotation ne l'est jamais, puisque le mode structuré travaille en
coordonnées non pivotées. La détection tourne désormais toujours, seule la
raison « scan » étant écartée en mode texte seul.

**Rectangles inventés.** Toute polyligne d'au moins quatre points était prise
pour un rectangle. La série de données d'un graphique en courbes devenait donc
un cadre coloré autour de sa propre étendue — un rectangle jaune posé sur un
graphique COVID — et chaque segment droit d'un glyphe vectorisé devenait une
petite tache noire : sur une page d'exemple, 97 rectangles produits pour 35
tracés réels. Un rectangle est désormais vérifié et non supposé : quatre coins,
tous les côtés horizontaux ou verticaux, côtés consécutifs perpendiculaires.
Le cas LibreOffice reste couvert, y compris quand MuPDF replie les quatre
segments en un quadrilatère.

**Colonnes de tableau effondrées.** Un PDF peut composer toute une ligne de
tableau en un seul objet texte, chaque cellule étant placée par un déplacement
explicite du curseur sans glyphe intermédiaire. Le fragment se lit alors
« 128847 119323 » avec une simple espace, alors que les glyphes sont
réellement distants de quatre-vingts points. Reconstruit à partir du fragment
seul, tout se tassait contre le bord gauche de la ligne et chaque colonne après
la première était fausse.

L'extraction passe donc par `rawdict` : seules les boîtes par caractère
révèlent où les glyphes se trouvent vraiment. Un fragment est coupé là où ses
glyphes sautent de plus de 0,45 fois la taille de police — nettement au-dessus
d'une espace ordinaire (~0,25) et bien en dessous des écarts qu'emploient les
tableaux réels (~0,75 et plus). À la recomposition, les fragments sont
regroupés par position et non simplement concaténés : ceux qui s'enchaînent
vraiment, comme un mot en gras au milieu d'une phrase, restent dans la même
boîte, si bien que le style en ligne continue de fonctionner.

Les espaces de remplissage sont conservées volontairement : un lecteur qui
concatène la ligne — ce que fait l'implémentation d'origine — lit toujours
« 36620 91,7 » et non « 3662091,7 ». Il perd les positions de colonnes, ce qui
n'est pas pire qu'avant.

**Masques de transparence.** `extract_image` ne renvoie que l'image de base.
Quand l'objet porte un masque doux — la façon dont un PDF stocke un PNG
transparent — cette base est noire partout où le masque la rend invisible : une
ombre portée devenait un cadre noir, un graphique en anneau un carré noir. Le
masque est maintenant récupéré et composé avant toute utilisation des pixels.

**Empaquetage.** Le binaire d'origine embarquait 846 Mo décompressés, dont
330 Mo de PyTorch, plus transformers, pyarrow, scipy, sklearn et onnxruntime —
aucun atteignable depuis le code, tous ramassés par PyInstaller dans
l'environnement de développement. Le fichier `.spec` nomme explicitement les
paquets à exclure, et le script de compilation alerte si la sortie dépasse
250 Mo.

**Cohérence de version.** Le module d'origine annonçait `1.0.5` dans son
en-tête alors que la constante indiquait `1.0.6` ; l'historique n'avait pas été
mis à jour.

---

## Robustesse en réception

Une archive qui arrive par radio est une entrée non fiable : toutes les
longueurs qu'elle contient sont contrôlées par l'émetteur, et LZMA se dilate
très fortement. Le décodeur applique donc des plafonds — taille d'archive,
manifeste décompressé, charge utile, nombre de pages et d'images — largement
au-dessus de ce que produit un document réel, si bien qu'ils n'interfèrent
jamais avec du trafic légitime.

La décompression est incrémentale et s'arrête au plafond : un fichier de 30 ko
qui se dilaterait en 200 Mo est rejeté après le premier bloc, sans jamais
allouer l'expansion complète.

Le manifeste est recoupé avant tout dessin. Un manifeste déclarant plus de
pages que de dimensions de page est cohérent en interne — son CRC est correct —
et provoquait auparavant une `IndexError` au milieu de la recomposition. Il est
désormais rejeté à la lecture.

Toutes les défaillances remontent une seule exception, `PsdiError`, portant un
message explicite. L'opérateur lit « Archive tronquée : le manifeste demande
609 octets » au lieu d'une `LZMAError` ou d'une `struct.error`.

**Ces contrôles ne concernent que la lecture.** L'encodeur est inchangé : les
24 vecteurs de référence capturés avant l'ajout sont identiques au bit près
après. L'interopérabilité et le poids des archives ne bougent pas.

---

## Tests

```
python -m unittest discover -s tests -t .
```

57 tests, sans dépendance en plus de celles de l'application.

| Fichier | Objet |
|---|---|
| `tests/test_format.py` | conteneur : troncature, plafonds, entrées hostiles |
| `tests/test_engine.py` | aller-retour, choix de mode, déterminisme, fidélité |
| `tests/test_interop.py` | vecteurs de référence et décodage historique |

Les documents de test sont construits par `tests/fixtures.py` plutôt que livrés
en fichiers PDF. Un PDF porte un horodatage de création et une disposition
d'objets qui varie d'une version de PyMuPDF à l'autre : un fichier livré
dériverait, ou masquerait une dérive. Ce qui doit rester stable est
l'**archive**, et elle ne dépend que du texte, de la géométrie et des images —
jamais des métadonnées du PDF.

### Vecteurs de référence

`tests/vectors/golden.json` fige les empreintes des archives produites depuis
des entrées fixes. Toute modification future qui change les octets émis fait
échouer le test, avec le détail des tailles avant et après. C'est la seule
protection réelle contre une rupture d'interopérabilité silencieuse.

Ce filet a été éprouvé en désactivant volontairement une optimisation : le test
a bien signalé les quatre vecteurs affectés.

Les vecteurs se régénèrent par

```
python -m tests.test_interop --update
```

à ne faire que délibérément, en documentant pourquoi la sortie a changé.

### Décodage historique

`TestLegacyDecoder` réimplémente le chemin de lecture tel que l'original le
faisait — `struct` et `json` nus, avec `.get(clé, défaut)` pour chaque champ
optionnel — et vérifie qu'il récupère bien le document. C'est ce qui prouve que
les optimisations de compression restent compatibles : les clés égales à leur
défaut sont omises du manifeste, ce qui n'est sûr que si le lecteur fournit ces
défauts. Le test échoue aussi si une optimisation cesse d'être exercée, pour
éviter qu'il passe sans rien vérifier.

Il contrôle également que le flux LZMA2 réglé (`pb=0`, recherche extrême) se
décode toujours avec la spécification de filtre nue, et que les charges utiles
JPEG stockées brutes commencent bien par la signature JPEG.

---

## Format d'archive

Petit-boutiste partout.

```
[0:4]   signature b'PSDI'
[4:6]   uint16  version

version 1 — structuré
    uint32  crc32 du manifeste non compressé
    uint32  longueur du manifeste compressé
    octets  manifeste, JSON, flux LZMA brut, filtre LZMA2 unique
    uint16  nombre d'images
    par image : uint16 xref | uint8 drapeaux | uint32 longueur | octets

version 2 — image de page
    uint16  nombre de pages
    par page : float32 largeur | float32 hauteur | uint8 drapeaux |
               uint32 longueur | octets
```

Le bit 0 des drapeaux signale une charge utile compressée en LZMA. Il n'est pas
toujours positionné : les données JPEG sont déjà codées par entropie et LZMA les
gonfle en général, chaque charge utile est donc stockée dans la forme la plus
petite des deux.

**Cette disposition ne doit pas être modifiée.** Réordonner ou redimensionner un
champ rompt l'interopérabilité avec les stations en service.

---

## Compilation pour Windows

```
pip install -r requirements.txt pyinstaller
build.cmd full
```

`build.cmd` seul construit uniquement l'application ; `full` compile aussi
l'installateur. La sortie arrive dans `dist\PDFteleporter\` et
`dist\installer\`.

Deux exécutables sortent d'une seule analyse PyInstaller :

| | |
|---|---|
| `PDFteleporter.exe` | fenêtré — l'IHM, et ce que lance le menu contextuel |
| `psditool.exe` | console — pour que la ligne de commande puisse afficher |

Un exécutable fenêtré n'a pas de `stdout` sous Windows : piloter le CLI via
`PDFteleporter.exe` depuis un terminal n'afficherait rien du tout.

### Si le build démarre sur « No module named 'pymupdf' »

L'environnement de compilation n'avait pas PyMuPDF, qui n'a donc jamais été
embarqué. PyInstaller rétrograde un import caché manquant en simple
avertissement et termine avec succès : la panne n'apparaît qu'au lancement.

C'est presque toujours une affaire de deux interpréteurs — les dépendances
installées dans un Python, PyInstaller lancé sous un autre. La commande
`pyinstaller` nue se résout via le `PATH`. Utilisez `python -m PyInstaller`.

Les deux extrémités sont désormais protégées : le `.spec` refuse de construire
si PyMuPDF, Pillow ou PyQt6 n'est pas importable, en nommant l'interpréteur
qu'il a testé, et `build.cmd` vérifie après coup que `_internal\pymupdf` existe
réellement.

PyMuPDF ne fournit aucun hook PyInstaller et PyInstaller n'en embarque pas non
plus ; ses extensions compilées sont donc collectées explicitement. Les motifs
de recherche par défaut (`lib*.so`, `*.dll`) ne correspondent à rien de ce que
PyMuPDF livre — les extensions s'appellent `_extra.pyd` et `_mupdf.pyd` — et
sont donc redéfinis.

### Localisation dans le build

Le catalogue français de Qt (`qtbase_fr.qm`) est chargé au démarrage et embarqué
par le `.spec` : le hook PyQt6 de PyInstaller ne collecte pas le répertoire des
traductions, et sans lui la fenêtre serait en français tandis que chaque
sélecteur de fichiers et bouton standard fourni par Qt resterait en anglais.

### Installateur

`installer\pdfteleporter.iss` exige **Inno Setup 7**. Il emploie
`SetupArchitecture=x64`, qui n'existe pas en Inno Setup 6 : cette version échoue
sur une directive inconnue plutôt que de produire silencieusement autre chose.

Choix notables :

- `ArchitecturesAllowed` vaut `x64compatible` (posé implicitement par
  `SetupArchitecture`), ce qui couvre Windows 11 Arm64 exécutant du x64 en
  émulation. L'identifiant `x64` nu est déprécié et exclurait ces machines.
- `PrivilegesRequired=lowest` avec choix par dialogue : un opérateur sans droits
  administrateur peut installer, l'application n'écrivant que dans le registre
  utilisateur.
- `MinVersion=10.0`, car PyQt6 ne démarre pas sur Windows 7 et le défaut d'Inno
  (`6.1sp1`) laisserait l'installateur s'exécuter quand même.
- La portée d'enregistrement du menu contextuel suit le mode d'installation. Une
  installation par utilisateur écrit dans `HKEY_CURRENT_USER` ; une installation
  administrative est élevée, si bien que `HKEY_CURRENT_USER` désignerait la
  ruche de l'*administrateur* et non celle de l'opérateur — le menu serait
  enregistré pour un compte que personne n'utilise, et une désinstallation
  élevée ne retrouverait jamais les clés de l'opérateur. Les installations
  administratives écrivent donc dans `HKEY_LOCAL_MACHINE`.

  L'alternative apparemment évidente, `runasoriginaluser`, ne fonctionne pas :
  elle n'est valide que dans `[Run]`, et il n'existe aucun équivalent pour
  `[UninstallRun]` — la fonction Pascal `ExecAsOriginalUser` est explicitement
  non supportée à la désinstallation.
- Le désinstalleur balaie les deux portées avant de supprimer les fichiers.

### Signature de code et SmartScreen

Les métadonnées de version **ne satisfont pas** SmartScreen. Les deux mécanismes
sont sans rapport : Windows Defender SmartScreen vérifie une signature
Authenticode et la réputation attachée au certificat. Un exécutable non signé
déclenche l'écran bleu *« Windows a protégé votre ordinateur »* au premier
lancement, quelle que soit la qualité de sa ressource de version, et l'opérateur
doit cliquer *Informations complémentaires → Exécuter quand même*.

Ce que la ressource de version apporte réellement : un onglet Propriétés
correct, une entrée saine dans Programmes et fonctionnalités, une identification
exploitable par les inventaires logiciels, et un signal heuristique de moins
pour les antivirus.

Les options réelles, par coût croissant :

1. **Rien.** Documenter le clic *Exécuter quand même*. Parfaitement viable pour
   une diffusion à un groupe prévenu.
2. **Certificat auto-signé déployé en Éditeurs approuvés par GPO.** Gratuit et
   propre dans un parc géré. Inutile hors de ce parc.
3. **Certificat OV** (~200–400 €/an). Depuis la baseline CA/Browser Forum de
   2023, la clé privée doit résider sur un token matériel ou un HSM cloud. La
   réputation SmartScreen n'est *pas* immédiate : elle s'accumule avec les
   téléchargements.
4. **Certificat EV** (~400–700 €/an). Réputation immédiate. Seule option qui
   supprime l'avertissement dès le premier téléchargement.

La signature est câblée mais désactivée. Définir `SIGNTOOL` et `SIGN_ARGS` dans
l'environnement suffit :

```
set SIGNTOOL=C:\Program Files (x86)\Windows Kits\10\bin\x64\signtool.exe
set SIGN_ARGS=/fd sha256 /tr http://timestamp.digicert.com /td sha256 /a
build.cmd full
```

Pour l'installateur, configurer un Sign Tool dans l'IDE Inno Setup et
décommenter `SignTool` et `SignedUninstaller` dans le `.iss`.
`SignedUninstaller` n'est pas facultatif si l'on signe : le désinstalleur est
généré sur la machine cible à l'installation et n'est donc pas couvert par la
signature de l'installateur.

Deux choix réduisent déjà les faux positifs indépendamment de la signature : le
build est en un dossier plutôt qu'un fichier unique — un exécutable *onefile* se
décompresse dans `%TEMP%` à chaque lancement, comportement heuristiquement
suspect en soi — et la compression UPX est désactivée, plusieurs moteurs
signalant les binaires packés UPX à vue.

---

## Conventions de langue

L'interface, le menu contextuel et les messages destinés à l'opérateur sont en
français : cet outil est employé par des opérateurs français des transmissions
de sécurité civile, et une interface anglaise y devient une source d'hésitation
sous contrainte de temps.

Le code, les commentaires et les identifiants restent en anglais. Les
identifiants qui circulent en ligne de commande ou dans les archives (`low`,
`struct`, les clés de débit radio) ne sont jamais des libellés d'affichage :
reformuler un texte visible ne doit pas casser une commande ni une archive.

## Licence

GNU GPL v3, héritée de l'implémentation originale de F1GBD. PyMuPDF est sous
AGPL v3.
