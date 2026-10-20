# PDF Teleporter

Compresse des documents PDF en archives `.psdi` compactes pour les transmettre
sur des liaisons radio à bande étroite (TNC Packet, VARA HF/FM, pièces jointes
Winlink Express), et les recompose en PDF lisibles à la réception.

Un SITREP de 600 ko qui demanderait un quart d'heure en VARA HF tient dans
environ 120 ko et passe en trois minutes, sa mise en page intacte à l'arrivée.
Entre stations équipées de la version 2.5.0 ou plus, l'archive compacte `.psdz`
retire encore 20 à 45 % de ce poids.

---

## Origine et attribution

Cette application est **dérivée du travail de F1GBD (ADRASEC 77 – FNRASEC)**,
auteur du PDFteleporter original et du format `.psdi`. La conception du format,
le choix des deux modes d'encodage, les préréglages de qualité et l'essentiel
des correctifs de rendu qui font que la recomposition ressemble au document
d'origine sont son travail, pas le nôtre.

L'original est distribué sous **GNU GPL v3** sous forme de binaire PyInstaller
Windows, sans code source publié — la version 1.0.6 comme la 2.1.0 d'août 2026.
La GPL v3 accorde explicitement le droit d'accéder au source correspondant et de
modifier l'œuvre ; le source a donc été reconstitué depuis le bytecode livré,
puis réécrit. Le dossier `reference/` de l'archive complète conserve ce matériel
pour les deux versions : moteur `pdf_trans` 1.0.6 et `psdi_lib` 2.1.0, interface
2.1.0, désassemblages et outils.

L'original a lui-même évolué entre-temps. Ses versions 2.0.1 et 2.1.0 ont
apporté une interface PyQt6, le glisser-déposer et l'estimation différée, un
bundle allégé et une extraction du texte retouchée ; le moteur, lui, est à une
fonction près le bytecode 1.0.6 (`reference/NOTES-EXTRACTION.md` détaille la
comparaison). Quand ce document parle de « l'original », il s'agit de la 2.1.0 ;
ce qui ne vaut que pour la 1.0.6 est signalé.

Ce dérivé reste sous **GNU GPL v3**, comme l'exige la licence de l'original.
PyMuPDF, utilisé par le moteur, est sous AGPL v3.

**Le format `.psdi` n'a pas été modifié.** C'est un contrat d'interopérabilité :
une station qui utilise la version de F1GBD doit pouvoir recomposer ce que cette
version émet, et réciproquement. Toutes les optimisations décrites plus bas ont
été retenues précisément parce qu'elles restent lisibles par un décodeur écrit
sur la spécification d'origine.

La version 2.5.0 ajoute un second format, `.psdz`, que le décodeur de F1GBD ne
sait pas lire. Il ne sert qu'entre stations équipées de cette version ou d'une
suivante et n'est produit que sur demande explicite (voir « Archives compactes
`.psdz` »). Le `.psdi` de la 2.5.0 ne diffère de celui de la 2.4.1 que par les
espaces rétablies entre les mots (voir « Correctifs »), et reste lisible tel
quel par la version de F1GBD.

Cette compatibilité est vérifiée contre le vrai moteur de F1GBD, pas contre
l'idée qu'on s'en fait : `reference/tools/bench.py` charge `psdi_lib` 2.1.0
directement depuis son bytecode et fait recomposer chaque archive de chaque côté
par chaque décodeur. Huit documents de test, quatre préréglages, quatre sens :
les 128 recompositions passent (`reference/bench/bench.json`). La suite de tests
fait de même à chaque exécution quand `reference/` est présent
(`TestOriginalDecoder`).

## Périmètre

Cet outil compresse et recompose, rien de plus. Il ne parle pas à la radio.

L'émission est le travail de Winlink, avec VARA ou Direwolf selon la liaison :
l'archive `.psdi` part en pièce jointe d'un message Winlink Express, et la
station réceptrice la recompose avec ce même outil. Le moteur de F1GBD comporte
— en 2.1.0 toujours — une couche de fragmentation TNC et de tramage VARA
(`prepare_for_tnc`, `prepare_for_vara` et leurs réciproques) et un
`PDFTransferManager`, parce qu'il alimente son application TCQ ; rien de cela
n'a été repris, et ne le sera pas.

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
sources. Les archives compactes `.psdz` demandent un Pillow qui sait traiter
l'AVIF : les roues de la 11.3 et des suivantes l'embarquent, sauf celles de
Windows ARM64, et `requirements.txt` exige la 11.3. L'exécutable Windows,
construit en x64, l'a sur toutes les machines, Arm64 comprises.

## Utilisation

### Interface graphique

Compression à gauche, recomposition à droite, journal horodaté en bas. La
sélection d'une archive déclenche immédiatement la vérification de sa signature
et de son CRC : un fichier abîmé en transit est repéré avant qu'on perde du
temps à le recomposer.

L'interface est en français et suit le réglage clair/sombre de Windows.

L'interface accepte le **glisser-déposer** d'un `.pdf`, d'une image, d'un
`.psdi` ou d'un `.psdz` : le fichier part dans le bon panneau selon son
extension, sans qu'il faille viser la bonne moitié de la fenêtre. Un dépôt
multiple est refusé plutôt que deviné.

La case **Compatible avec la version F1GBD (archive .psdi)** est cochée à chaque
démarrage. Décochée, la compression produit une archive compacte `.psdz`,
nettement plus légère mais lisible seulement par PDF Teleporter 2.5.0 et plus ;
l'estimation de taille suit l'état de la case, et le journal rappelle la
restriction après chaque compression compacte. La case n'est pas mémorisée d'une
session à l'autre, volontairement : une archive que la station destinataire ne
sait pas ouvrir coûte une demande et une seconde émission, bien plus que les
octets gagnés.

Les **estimations sont mesurées, pas devinées**. L'archive est réellement
compressée en mémoire, en arrière-plan, ce qui donne la taille exacte et non un
produit de la taille source par un coefficient — lequel se trompait de 16 % sur
un bulletin chargé de photographies, précisément quand savoir si le message
passe sous la limite Winlink compte le plus. La mesure est différée de 450 ms :
changer trois fois de qualité de suite ne lance qu'une compression, celle du
réglage retenu.

Ces trois comportements — glisser-déposer, estimation par compression réelle en
arrière-plan, délai de 450 ms — existent aussi dans l'original 2.1.0 ; la 1.0.6
estimait par coefficient.

### Menu contextuel de l'Explorateur

*Outils → Ajouter au menu contextuel de l'Explorateur* enregistre trois verbes :

- clic droit sur un `.pdf` ou une image (JPEG, PNG, BMP, TIFF, WebP) →
  **Téléporter : compresser en .psdi (compatible F1GBD)**, qui ouvre un
  sous-menu en cascade proposant les quatre qualités, du transfert le plus
  rapide au plus lent
- au même endroit, **Téléporter : compresser en .psdz (compact)**, même
  sous-menu, pour l'archive compacte
- clic droit sur un `.psdi` ou un `.psdz` → **Téléporter : recomposer le PDF**

Le choix de la qualité est la seule décision que l'opérateur ait réellement à
prendre ; l'obliger à ouvrir l'interface pour la prendre viderait le clic droit
de son intérêt. Celui du format décide des stations qui pourront lire le
fichier : il se fait donc là où il se voit, dans le libellé de l'entrée, et
l'extension le montre ensuite. Chaque format a son type de fichier dans
l'Explorateur, « Archive PDF Teleporter » et « Archive compacte PDF
Teleporter ».

Sous Windows 11, ces entrées apparaissent sous *Afficher plus d'options* (ou
Maj+F10). Atteindre le menu de premier niveau exige une extension shell MSIX
signée, disproportionnée pour un outil employé quelques fois par exercice.

### Ligne de commande

```
python pdfteleporter.py compress SITREP.pdf -q low
python pdfteleporter.py compress SITREP.pdf -q low --compact
python pdfteleporter.py rebuild  SITREP.psdi -o sortie.pdf
python pdfteleporter.py inspect  SITREP.psdz
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

### Fontes sans table ToUnicode

Un PDF annoté sous Okular — ou avec tout outil qui embarque une fonte CID en
encodage Identity sans table ToUnicode — expose ses saisies sous forme
d'identifiants de glyphes, pas de caractères. Le texte est perdu pour tout
lecteur, PyMuPDF compris ; seuls les glyphes dessinés subsistent.

L'heuristique de ligatures de l'original, avant sa version 2.1.0,
transformait chaque glyphe non résolu en « ti » : `F4LTV` ressortait en
`tititititi`, et le reste de la page étant parfait, rien ne signalait la perte.
Le discriminant est simple : une
ligature est un glyphe isolé dans un mot lisible, alors qu'une saisie perdue
l'est en entier. La détection se fait **au niveau du dictionnaire de fontes**
(sous-type Type0, encodage Identity, pas de ToUnicode) plutôt que sur les
caractères, parce qu'un identifiant de glyphe qui coïncide avec un code ASCII
imprimable paraît lisible et passerait tout test textuel.

Le rapprochement entre le nom du dictionnaire et celui que rapporte le fragment
tolère la troncature : les producteurs plafonnent `BaseFont` à 31 caractères,
si bien que « DejaVu Sans Condensed Bold Oblique » est stocké amputé alors que
le fragment porte le nom complet. Un second filet, textuel, condamne tout
fragment contenant ne serait-ce qu'un caractère de contrôle — la prose n'en
contient jamais — là où un jugement par proportion laissait passer `)\x17/79`,
rendu d'Arial pour `F4LTV`, qui paraît lisible à 80 %. Le test de non-régression
balaie plusieurs fontes, Arial compris sous Windows, parce que le premier
correctif passait sur DejaVu et échouait sur Arial.

Les fragments concernés sont rastérisés par le même mécanisme que les
illustrations : le raster reproduit exactement ce que l'opérateur a tapé, et
l'archive reste lisible par tout décodeur du format. Le mode « texte seul » les
conserve aussi — l'opérateur a choisi d'écarter les images, pas de perdre ses
propres saisies. Le test de non-régression construit un vrai PDF privé de sa
table ToUnicode plutôt qu'une simulation sur des chaînes.

F1GBD a traité le même défaut dans sa version 2.1.0, par un autre chemin : son
extraction passe désormais le drapeau `TEXTFLAGS_DICT`, dont
`CID_FOR_UNKNOWN_UNICODE` fait sortir les glyphes non résolus en identifiants
bruts plutôt qu'en U+FFFD, et la substitution `ti`/`tt` n'est plus tentée
qu'entre deux minuscules. Un indicatif n'est donc plus maquillé en mot : il
ressort tel quel, `)\x17/79`. Le texte reste perdu, puisque rien dans le PDF ne
permet de le retrouver — c'est précisément pourquoi il est rastérisé ici. Le
banc de mesure le montre sur le PDF de test privé de ToUnicode : son archive
pèse 347 octets, la saisie absente ; la nôtre 725, la saisie présente.

### Images en entrée

Une photographie de formulaire prise au téléphone ou un scan JPEG, PNG, TIFF,
BMP ou WebP se compresse comme un PDF. Il n'y a pas de couche texte à
structurer : l'image devient une page de l'archive version 2, telle quelle, et
toute station lisant ce format la recompose sans rien connaître de nouveau.

Un TIFF multipage donne une page par image. L'orientation EXIF est appliquée,
sans quoi une photo prise en travers ressortirait en travers. La transparence
est aplatie sur blanc, la couleur du papier. La taille de page est choisie pour
que le grand côté corresponde à celui d'un A4, en gardant exactement le rapport
d'aspect, et l'image est ré-échantillonnée à la résolution du préréglage — une
photo de 12 mégapixels et un scan à 150 ppp de la même feuille pèsent la même
chose sur l'air. Une page A4 photographiée tient dans environ 30 ko en « Très
basse », lisible.

Le menu contextuel propose le même sous-menu de qualités sur ces types de
fichiers.

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

**Texte vectorisé.** Excel écrit les étiquettes d'axe de ses graphiques en
contours de glyphes : un tracé de courbes par mot, sans aucun caractère
derrière. Chaque mot fait 12 à 24 pt de côté, sous le plancher de 24 pt qui
évite de rastériser la moindre fioriture, et les mots sont espacés de 25 à
40 pt, au-delà du rayon de fusion ordinaire. Résultat sur un SITREP réel : sept
noms de villes sur dix disparaissaient sous un graphique en barres. Une
rangée ou une colonne d'au moins trois petits tracés alignés est désormais
reconnue comme un libellé et rastérisée d'un bloc ; un tracé isolé l'est aussi
quand il est assez long et fait d'assez de courbes pour être un titre d'axe
pivoté plutôt qu'un ornement. Un ornement seul reste écarté, et le plancher
reste en place : une page de petites décorations ne se transforme pas en
centaines de vignettes. Coût mesuré sur le même SITREP : 0,6 à 2,4 ko selon
le préréglage, soit environ un pour cent de l'archive.

### Correctifs de rendu hérités de l'original

Ces traitements viennent de F1GBD et ont été conservés à l'identique. Ils sont
le fruit de son travail sur de vrais documents et chacun corrige un défaut
visible en réception :

- **Ligatures LibreOffice.** LibreOffice écrit les ligatures `ti` et `tt` en
  points de code non standard (U+019F, U+01A9) qui arrivent en caractères de
  remplacement. Elles sont décomposées à l'extraction ; là où le glyphe est déjà
  perdu, la substitution est déduite du contexte. La 2.1.0 de l'original
  restreint cette déduction au glyphe encadré de deux minuscules ; ici elle est
  réservée au glyphe isolé dans un mot lisible, un fragment majoritairement
  perdu étant rastérisé (voir plus haut).
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

| Original 2.1.0 | Ici |
|---|---|
| PyQt6, feuille de style sombre fixe (Tkinter jusqu'en 1.0.6) | PyQt6, style Fusion suivant le réglage clair/sombre du système |
| Moteur et IHM en deux fichiers monolithiques (`psdi_lib`, `PDFteleporter`) | Moteur séparé (`format` / `engine` / `presets`), IHM isolée |
| Un chemin passé en argument ouvre l'IHM dessus ; aucune conversion sans fenêtre | CLI headless complet |
| Un exécutable fenêtré | Deux : fenêtré (IHM) et console (CLI) |
| Glisser-déposer, pas de menu contextuel | Glisser-déposer et menu contextuel Explorateur |
| Installateur Windows (depuis la 2.1.0) ou archive 7z portable | Installateur Inno Setup 7, ressources de version Windows |
| Couche TNC/VARA et gestionnaire de transfert embarqués | Compression seule ; l'émission est laissée à Winlink |
| Un format, `.psdi` | `.psdi` inchangé, et au choix `.psdz`, compact (2.5.0) |

Le moteur d'origine était déjà une bibliothèque autonome sans dépendance à
l'IHM — l'auteur l'avait conçue pour être appelée depuis son application TCQ.
Cette séparation a été préservée et renforcée : le clic droit dans l'Explorateur
appelle le CLI, pas l'interface graphique, et ne démarre donc pas Qt pour une
simple conversion.

### Compression par rapport à `psdi_lib` 2.1.0

Mesuré contre le moteur de F1GBD lui-même, chargé depuis son bytecode
(`reference/bench/`), sur les documents de test de la suite :

| Document | Préréglage | `psdi_lib` 2.1.0 | Ici (2.4.0) | Écart |
|---|---|---|---|---|
| Rapport texte, 3 pages | `ultra_low`, `low` | 2 963 | 1 749 | −41 % |
| Rapport texte, 3 pages | `medium`, `high` | 2 963 | 1 813 | −39 % |
| Rapport riche (photos, tableau à filets, liens) | `ultra_low` | 4 657 | 1 791 | −62 % |
| Rapport riche | `medium` | 9 018 | 5 709 | −37 % |
| Rapport riche | `high` | 8 306 | 7 971 | −4 % |
| Tableau de 12 lignes | `low` | 887 | 493 | −44 % |
| Graphique et photographie | `ultra_low` | 13 752 | 6 665 | −52 % |
| Graphique et photographie | `medium` | 24 616 | 21 845 | −11 % |
| Bordures Excel et LibreOffice | `ultra_low` | 1 078 | 702 | −35 % |
| Bordures Excel et LibreOffice | `medium` | 1 078 | 725 | −33 % |
| Page pivotée (mode image) | `low` | 59 848 | 59 665 | −0,3 % |

Les gains viennent du manifeste et, depuis la 2.3.0, des images qui ne sont pas
des photographies. Là où le JPEG d'une photographie fait tout — mode image, ou
préréglage `high` sur un document de photographies — les deux moteurs se
valent, ce qui est attendu : les paramètres JPEG sont ceux du format. Depuis la
2.4.0, aucune ligne n'est plus lourde que l'original, y compris celle des
bordures à `medium`, qui porte pourtant les traits du tableau LibreOffice que
l'original ne stocke pas du tout (voir la 2.3.0 et la 2.4.0). Sur ces
documents, la 2.5.0 émet les mêmes `.psdi` octet pour octet ; l'archive
compacte `.psdz` est mesurée à part, plus bas.

Le détail de chaque changement, mesuré isolément, suit. Les pourcentages de ce
tableau-ci sont des avant/après sur notre propre moteur, pas des écarts à
l'original.

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

Mesuré de bout en bout sur un rapport de six pages, avant et après ces quatre
changements : **5 223 → 4 116 octets à `medium`** (−21 %), et **3 812 octets à
`low`** (−27 %).

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

### Images, filets et liens : −22 % à `ultra_low` (version 2.2.0)

Sur un document chargé de photographies, c'est l'encodage des **images** qui
domine, pas le texte. Un SITREP de huit pages avec logos et graphiques pesait
66 091 octets à `ultra_low` ; trois ajouts le ramènent à 49 500.

| Changement | Effet sur le SITREP |
|---|---|
| Images réduites à leur taille **affichée** plutôt qu'à leur taille native | −22 % à `ultra_low`, −18 % à `medium` |
| Filets et bordures encodés en traits vectoriels (`line`) au lieu d'être rastérisés | −5 % supplémentaires |
| Liens hypertexte conservés (`lk`) | +169 octets pour 13 liens |

**Images à leur taille affichée.** Un logo de 1 200 px placé dans une case de
40 pt était réduit par le préréglage de résolution, mais à partir de ses pixels
natifs : à 72 dpi il restait trop grand pour ce que la page en montre. Le moteur
relève désormais la plus grande boîte d'affichage de chaque image sur l'ensemble
du document (une image partagée entre pages est optimisée pour son plus grand
usage) et plafonne la réduction à cette taille, avec 10 % de marge. Les pixels
supprimés n'auraient jamais été visibles.

**Traits vectoriels.** Un tracé fait uniquement de segments horizontaux ou
verticaux (lignes de tableau, soulignés, règles de section) n'est plus traité
comme une illustration à rastériser : chaque segment devient une entrée
`{"t": "line", "r": [x0, y0, x1, y1], "c": couleur, "w": épaisseur}` dans la
liste `dr` de la page. Quelques octets au lieu d'une image. *Depuis la 2.3.0,
ces traits sont stockés en rectangles sans épaisseur, que le décodeur d'origine
dessine — voir plus bas ; les entrées `line` restent lues.*

**Liens.** Les annotations `/URI` de chaque page sont relevées dans une liste
`lk` et réinsérées à la recomposition. Un bulletin qui renvoie vers un site de
l'OMS ou une carte en ligne garde ses liens cliquables après transit radio.

**Pourquoi ces ajouts ne cassent rien.** Le format `.psdi` n'a pas changé :
même signature, même version 1, mêmes structures. Le décodeur d'origine parcourt
`dr` avec `if not d['t'] == 'rect': continue`, il saute donc les traits ; il
n'a jamais lu de clé `lk`, il l'ignore. Un port fidèle de sa boucle de
recomposition a été exécuté sur les nouvelles archives : huit pages, 2 513 mots,
aucune exception. La réduction d'image, elle, ne touche que la charge JPEG, que
tout décodeur affiche telle quelle.

Les vecteurs de référence ont été étendus d'une fixture `rich_report`
(photographies, tableau à filets, liens) sur deux préréglages. Désactiver le
plafond d'affichage les fait échouer — c'est leur rôle.

### Recomposition : 16 s → 0,9 s, et une barre qui avance (version 2.2.1)

Le symptôme était une barre de progression figée à 50 % pendant toute la
recomposition, puis 100 % d'un coup. La cause immédiate : le moteur ne
signalait que deux étapes, avant et après la boucle des pages. La cause de
fond, trouvée en mesurant : la recomposition d'un bulletin de huit pages
prenait 16 secondes, dont 7 pour la seule page du tableau paludisme.

**Pourquoi 16 secondes.** Chaque fragment de texte était posé par
`insert_htmlbox`, qui construit un document temporaire d'une page et le
greffe sur la page cible via `show_pdf_page`. Cette greffe commence par lister
toutes les ressources déjà présentes sur la page pour choisir un nom libre ;
la liste s'allonge à chaque fragment, et le coût par fragment avec elle —
7,8 ms pour les cinquante premiers d'une page, 44 ms pour les cinquante
derniers. Une page de 294 lignes coûtait donc le carré de sa longueur.

**Le correctif.** Les fragments d'une page sont désormais mis en page par la
même machinerie `Story`, avec le même ajustement à la cellule, mais dessinés
dans une seule page `DocumentWriter` de la taille de la page cible, greffée
une fois à la fin. L'arithmétique est copiée d'`insert_htmlbox`, et
l'équivalence est vérifiée par un test qui recompose par les deux chemins et
compare les mots que le PDF rapporte : même texte, mêmes positions à 0,002 pt
près sur 2 508 mots. Le PDF local passe au passage de 943 ko à 537 ko, les
polices étant embarquées une fois par page au lieu d'une fois par fragment.

**La barre.** Le moteur rapporte sur une échelle fixe (`PROGRESS_SCALE`),
par unités de travail pondérées : une par ligne de texte à la recomposition,
une par image et cinq par page à la compression, l'enregistrement LZMA
compté selon le nombre de pages. Les mises à jour sont limitées à une toutes
les 50 ms, les changements de phase passant toujours. Mesuré sur le même
bulletin : à la moitié du temps, la barre est à 37–46 % ; aucun intervalle
immobile ne dépasse 60 ms.

Le moteur 2.1.0 de F1GBD emprunte toujours ce chemin, un `insert_htmlbox` par
fragment, et le banc le mesure : 1,4 à 1,8 s pour recomposer le rapport de
trois pages, contre 0,13 à 0,23 s ici, à archive égale.

### Étiquettes d'axe vectorisées (version 2.2.2)

Sur le graphique en barres de la page 4 du SITREP OMS Mali, les noms de villes
sous l'axe n'étaient pas du texte mais des contours vectoriels — voir « Texte
vectorisé » plus haut. Seuls trois sur dix survivaient, ceux qui tombaient par
hasard dans une zone d'illustration assez grande. Le regroupement en rangée
les restitue tous, sous forme d'une à trois vignettes placées comme des images
ordinaires, lisibles par le décodeur d'origine. Les vecteurs de référence sont
inchangés : aucune fixture ne contenait de texte vectorisé, ce que les quatre
tests ajoutés corrigent (rangée reconnue, aller-retour, titre isolé conservé,
ornement isolé toujours écarté).

### Tableaux, bordures et images : −11 % sur quatre documents réels (version 2.3.0)

Mesuré sur quatre documents d'exercice réels — un bilan préfectoral de cinq
pages, un point de situation exporté d'Excel, le même exporté de LibreOffice,
et le SITREP OMS Mali de huit pages — à chacun des quatre préréglages, contre
la version 2.2.2 et contre `psdi_lib` 2.1.0 de F1GBD exécuté depuis son
bytecode. Tailles d'archive en octets :

| Document | Préréglage | F1GBD 2.1.0 | 2.2.2 | 2.3.0 | 2.3.0 / 2.2.2 |
|---|---|---|---|---|---|
| Bilan préfectoral | `ultra_low` | 15 719 | 8 773 | 7 927 | −10 % |
| Bilan préfectoral | `medium` | 25 137 | 11 945 | 10 552 | −12 % |
| Point de situation Excel | `ultra_low` | 24 592 | 18 933 | 13 233 | −30 % |
| Point de situation Excel | `medium` | 24 592 | 24 177 | 14 363 | −41 % |
| Point de situation LibreOffice | `ultra_low` | 11 556 | 14 091 | 12 757 | −9 % |
| Point de situation LibreOffice | `medium` | 11 556 | 17 930 | 17 133 | −4 % |
| SITREP OMS | `ultra_low` | 64 301 | 50 241 | 45 698 | −9 % |
| SITREP OMS | `medium` | 124 656 | 112 227 | 104 950 | −6,5 % |

Sur les seize archives (quatre documents, quatre préréglages) : 580 352 →
514 522 octets, **−11,3 %** ; contre 682 154 octets pour `psdi_lib` 2.1.0 sur
les mêmes seize. Chaque recomposition a été comparée à l'original, page par
page, à 100 dpi : l'écart est identique ou plus faible partout, par notre
décodeur comme par celui de F1GBD.

**Pourquoi LibreOffice reste plus lourd chez nous que chez F1GBD.** Son moteur
ne relève que les rectangles ; un trait isolé est ignoré. Or LibreOffice dessine
chaque bord de cellule comme un trait séparé : son archive ne contient aucune
bordure de tableau, et sa recomposition sort sans quadrillage. Les 5 600 octets
d'écart à `medium` sont ces bordures.

**Les bordures sont des morceaux.** Excel dessine chaque bord de cellule comme
son propre rectangle plein de 0,84 pt, LibreOffice comme son propre trait : une
ligne de tableau arrive en une douzaine de pièces bout à bout. Sur la feuille
Excel, les dessins faisaient 75 % de l'archive, et 52 % sur LibreOffice. Un
compactage par page (`_compact_drawings`) raccorde les pièces de même style
qui se prolongent exactement, remplace un remplissage répété par sa dernière
copie, retire le blanc posé sur le papier blanc et ce que l'arrondi réduit à un
point, puis range chaque série de même style par position, ce que LZMA
code bien mieux que l'ordre cellule par cellule du producteur.

Ce compactage ne doit rien changer à la page, et trois règles y veillent :

- **L'ordre de peinture.** Une pièce ne rejoint une pièce antérieure que si rien
  d'une autre couleur, peint entre les deux, ne la recouvre — sinon elle
  passerait dessous. Toucher compte comme recouvrir.
- **Ce qui est peint deux fois reste peint deux fois.** Un trait plus fin qu'un
  pixel ou deux, peint deux fois, sort plus foncé à l'écran : LibreOffice trace
  chaque bordure intérieure deux fois, une par cellule voisine, et l'original
  en est plus sombre. Fusionner ces doublons rendait les bordures d'un ton plus
  clair qu'à l'origine à 100 % — mesuré, puis écarté. Les pièces fines ne se
  raccordent donc que bout à bout, jamais en se chevauchant.
- **Un tracé est peint d'un seul coup.** À l'inverse, les segments d'un même
  tracé qui se recouvrent ne reçoivent l'encre qu'une fois ; ils sont joints
  (`_join_path_segments`).

Deux différences restent possibles, larges d'un pixel l'une et l'autre : la
couture anticrénelée entre deux pièces jointes disparaît, et le contour d'un
aplat épais peint plusieurs fois à l'identique, ramené à une seule couche,
perd l'assombrissement que ces couches donnaient à ses pixels de bord.
Mesuré sur toutes les pages des quatre documents, aux quatre
préréglages, dessins seuls, avec et sans compactage, à 150 dpi : aucun pixel ne
bouge de plus de 67 niveaux de gris sur 255 ; 16 sur 252 millions bougent de
plus de 64, tous sur la feuille Excel, et 3 716 de plus de 32. Sur les pages
entières recomposées à 100 dpi, texte et images compris, l'écart est le même
par notre décodeur et par celui de F1GBD : 2 pixels sur 112 millions au-delà
de 64 niveaux.

**Les traits deviennent des rectangles sans épaisseur.** En 2.2.x, un trait
était stocké en entrée `line`, que le décodeur d'origine saute : une station
équipée de la version de F1GBD recevait nos tableaux LibreOffice sans une seule
bordure. Un rectangle de hauteur nulle, tracé à l'épaisseur du trait, couvre
exactement les mêmes pixels qu'un trait à bouts plats — vérifié identique au
pixel près de 150 à 600 dpi — et c'est un `rect` ordinaire, que les deux
décodeurs dessinent. Sur le document LibreOffice recomposé par le décodeur de
F1GBD, l'écart moyen à l'original tombe de 8,7 à 2,0 niveaux de gris.

**Les bordures Excel ne disparaissent plus en « Très basse » et « Basse ».**
Ces deux préréglages arrondissent les coordonnées au point entier. Une bordure
de 0,84 pt devenait épaisse de 1 pt, ou de 0 — sur la feuille Excel, 866
bordures sur 4 196 disparaissaient. Aux deux préréglages bas, un remplissage
de moins de 2 pt est donc stocké comme un trait : la position s'arrondit, mais
l'épaisseur voyage à part, au dixième de point, et ne peut plus s'annuler. À
`medium` et `high`, le dixième de point garde déjà l'épaisseur : la forme
remplie y reste, plus courte et plus fidèle.

**Les graphiques et les logos passent en PNG quand c'est mieux.** Les deux
décodeurs confient chaque image à `insert_image` de MuPDF, qui reconnaît le PNG
aussi bien que le JPEG. Un logo, un graphique, une étiquette d'axe vectorisée
sont quelques aplats aux bords nets : ce que le JPEG code le plus mal, avec des
halos autour de chaque contour. Chaque image d'une archive structurée est donc
proposée en JPEG — progressif et non progressif, le plus petit des deux, mêmes
pixels — puis, si elle a moins de 4 096 couleurs, en PNG : en niveaux de gris
sans perte pour une image grise, sur ses propres couleurs si elle en a 256 au
plus, sinon sur la plus petite palette calculée qui convient. Un PNG n'est
retenu que s'il est plus fidèle que le JPEG d'au moins 1 dB de PSNR — ce que le
gris sans perte est toujours, et ce que la palette des couleurs propres n'est
pas d'office : en y rapportant les pixels, Pillow confond des couleurs
distantes de quelques niveaux. Ensuite la plus petite forme gagne, mesurée
telle que le conteneur la stockera, après son LZMA éventuel — c'est pourquoi le
PNG est écrit non compressé : LZMA code les indices de palette mieux que le
deflate du PNG. La palette calculée est affinée par k-moyennes, sans quoi le
cœur des lettres s'éclaircit (126 au lieu de 89 sur les étiquettes du SITREP ;
108 avec l'affinage). Sur les deux documents à images, aux quatre préréglages,
40 images passent en PNG : 33 910 → 23 273 octets stockés (−31 %), et aucune
n'a un SSIM ni un PSNR inférieur à celui du JPEG remplacé. Les photographies
restent en JPEG ; les archives en mode image de page aussi, car le bruit d'un
scan rend toute palette plusieurs fois plus lourde.

**Le dictionnaire LZMA est ajusté à la donnée.** Le préréglage 9 réserve
64 Mio de dictionnaire, et l'allocation coûtait 38 ms par appel — sur un
document à petites images, chacune compressée seule, la moitié du temps de
compression. Le dictionnaire suit désormais la taille de la donnée, sans
changer les octets produits — à un octet près, sur un seul des vecteurs de
référence. Il est aussi plafonné à 8 Mio : un flux LZMA2
brut ne mémorise pas la taille de son dictionnaire, et le décodeur d'origine
prend la valeur par défaut, 8 Mio. Une charge de plus de 8 Mio compressée avec
64 Mio pouvait ainsi devenir illisible chez lui (« Corrupt input data »,
reproduit en test) ; aucune archive réelle n'atteignait ce seuil.

**Essayé et écarté**, mesures à l'appui :

| Piste | Mesure | Décision |
|---|---|---|
| JPEG 2000 pour les photographies | 19 à 112 % *plus lourd* que le JPEG à PSNR et SSIM égaux | écarté |
| Réglage LZMA (`lc`, `lp`, recherche) | −0,3 % au mieux | sans objet |
| Ordre des clés JSON | ±1 %, selon le document | sans objet |
| Fusion des doublons fins de LibreOffice | −1,3 % de plus à `medium`, bordures plus claires à l'écran | écarté (visible) |
| Raccord des bordures Excel par-dessus les croisements | −5 % sur la feuille Excel, là où la bordure qui croise couvre le vide ; ailleurs ce vide est l'intérieur blanc d'une double bordure | pas retenu : une règle de plus pour un gain modeste |
| Optimisation sans perte mozjpeg | −1,5 % sur les photographies | possible, mais ajoute une dépendance native au build |
| Supprimer la marge de 10 % sur la résolution des images | −4 à −7 % sur le SITREP | écarté : c'est une baisse de résolution |

**Limite constatée, héritée du format.** Les deux décodeurs peignent toujours
les rectangles, puis les images, puis le texte. Un cadre tracé *par-dessus*
une image dans l'original passe donc dessous à la recomposition : la carte de
la page 4 du SITREP perd ainsi son cadre noir de 3 pt, caché sous l'ombre portée
— chez F1GBD comme ici. Y remédier demanderait de rastériser ces zones ; ce
n'est pas fait.

**Relu avant publication.** Une relecture indépendante de la 2.3.0 — 14 500
listes de dessins aléatoires rendues à 72, 150 et 600 dpi, un PDF hostile passé
par le vrai décodeur de F1GBD, les cas limites du codec d'images — n'a trouvé
aucune entrée où le compactage change visiblement la page, ni aucune que le
décodeur d'origine refuse. Elle a relevé ailleurs cinq défauts du code neuf et
deux plus anciens, tous corrigés et reproduits en test (`TestReviewFindings`) :

- un tracé à la fois rempli et contouré qui ne forme pas un rectangle — un
  cadre en L, un trait peint par `B` — perdait ses traits : régression du
  premier jet de la 2.3.0 ;
- un aplat géant débordant de la page faisait parcourir au compactage des
  millions de cases de sa grille, 24 s pour un seul : l'index s'arrête
  désormais au bord de la page ;
- une épaisseur de 0,10 à 0,15 pt, arrondie à 0,1, tombait au seuil où les deux
  décodeurs tracent un filet d'un pixel, bien plus sombre à 150 dpi : elle est
  gardée au centième, 0,11 pt au moins ;
- la palette « exacte » des images de 256 couleurs ou moins ne l'était pas tout
  à fait — Pillow y confond des couleurs voisines de quelques niveaux — et
  échappait au contrôle de fidélité : elle y passe comme les autres ;
- une seconde quantification tramée ne servait à rien, Pillow ignorant le
  tramage quand il calcule lui-même la palette : retirée ;
- plus ancien, déjà dans la 2.2.2 : notre décodeur abandonnait la
  recomposition (« rect must be finite and not empty ») sur une image placée
  dans un cadre de moins d'un demi-point, que l'arrondi des préréglages bas
  réduisait à une largeur nulle. L'encodeur ne l'émet plus, et le décodeur saute
  une image sans surface, absente ou illisible au lieu d'échouer. Le décodeur de
  F1GBD la tolérait déjà ;
- plus ancien aussi : un rectangle tracé de hauteur nulle (`re` puis `S`),
  façon courante de dessiner un filet, était jeté à tous les préréglages ; c'est
  maintenant un trait.

La même relecture a montré qu'un aplat que l'arrondi réduit à une hauteur
nulle n'est pas tout à fait invisible — MuPDF le trace d'un pixel pâle : il
n'est plus retiré. Ces corrections coûtent 45 octets sur les seize archives
réelles, tous au bilan préfectoral en « Très basse » et « Basse », et ne
changent aucun vecteur de référence.

### Tableaux LibreOffice et Excel plus légers que l'original, quadrillage compris (version 2.4.0)

Après la 2.3.0, le point de situation exporté de LibreOffice restait plus lourd
ici que chez F1GBD — 17 133 octets contre 11 556 à « Moyenne » — parce que son
moteur jette les bordures du tableau. Il est désormais plus léger avec son
quadrillage, sans changer le format ni ajouter de dépendance. Mêmes quatre
documents, tailles en octets :

| Document | Préréglage | F1GBD 2.1.0 | 2.3.0 | 2.4.0 | 2.4.0 / F1GBD |
|---|---|---|---|---|---|
| Point de situation LibreOffice | `ultra_low`, `low` | 11 556 | 12 757 | 10 105 | −13 % |
| Point de situation LibreOffice | `medium`, `high` | 11 556 | 17 133 | 10 879 | −6 % |
| Point de situation Excel | `ultra_low`, `low` | 24 592 | 13 233 | 11 506 | −53 % |
| Point de situation Excel | `medium`, `high` | 24 592 | 14 363 | 10 979 | −55 % |
| Bilan préfectoral | `medium` | 25 137 | 10 552 | 10 268 | −59 % |
| SITREP OMS | `medium` | 124 656 | 104 950 | 102 071 | −18 % |

Sur les seize archives : 514 522 → 476 740 octets (−7,3 %), contre 682 154 pour
`psdi_lib` 2.1.0 (−30 %). L'écart moyen à l'original, page par page à 100 dpi,
est le même au millième de niveau de gris près sur chacune, ou un peu plus
faible, par notre décodeur comme par celui de F1GBD. Le format ne change pas :
ce sont les mêmes clés, écrites autrement.

**Les mots coupés aux ligatures sont recollés.** LibreOffice dessine les
ligatures « ti » et « tt » dans une police à part : « Identité » arrive en trois
fragments, « Iden », « ti », « té », chacun avec sa boîte. Les deux décodeurs
les remettent côte à côte dans la même boîte HTML. Recollés selon la règle même
de notre décodeur — même style, écart inférieur à 0,45 fois le corps —, ils
donnent la même page, vérifiée au pixel près par les deux décodeurs. Sur le
document LibreOffice, ces boîtes de fragments pesaient un dixième de
l'archive.

**Une ligne qui tient dans sa boîte la garde ouverte.** Chaque décodeur pose
une ligne dans une boîte HTML construite sur la boîte stockée : le coin haut
gauche place le texte, le bord droit et le bas ne servent qu'à savoir s'il
tient — sinon il est réduit. Une ligne qui tient déjà en taille réelle se
dessine donc à l'identique dans toute boîte plus grande, et la plus grande est
la page : son coin bas droit, répété sur chaque ligne, ne coûte presque rien à
LZMA. Le moteur mesure chaque ligne dans la police de base qu'emploient les
décodeurs — mêmes chasses que la mise en page HTML, vérifié sur 600 lignes des
documents de test. Une ligne réduite pour tenir garde sa boîte exacte, puisque
c'est elle qui fixe la réduction ; de même une ligne à plusieurs fragments, ou
dont un caractère manque à la police de base. Notre décodeur donne des pixels
identiques. Celui de F1GBD fait passer chaque ligne par une page temporaire à
la taille de la boîte : chaque glyphe se retrouve à moins d'un millième de
point de sa place précédente — mesuré sur les 13 338 caractères du SITREP —, ce
qui peut faire basculer l'anticrénelage d'un bord de lettre d'un pixel, rien
de plus. Le coin de la page est aussi le choix le plus court : ramener le bas
de la boîte au haut de la ligne coûterait 2 à 4 % de plus, et le décodeur de
F1GBD, qui découpe chaque ligne à sa boîte, y perdrait le bas des lettres.

**Les coordonnées sont écrites à largeur fixe.** `84.100` au lieu de `84.1`,
`  84` au lieu de `84` : JSON lit les mêmes nombres, et le manifeste reste du
JSON valide. Mais chaque rectangle a maintenant la longueur du précédent, et
LZMA code une répétition à distance inchangée bien moins cher qu'à une distance
nouvelle — or deux bordures voisines ne diffèrent que d'une coordonnée ou
deux. Sur les deux exports de tableur, −11 à −13 % du manifeste compressé au
dixième de point, −2 à −3 % au point entier. Le texte écrit est relu et comparé
au manifeste avant de partir.

**Quatre traits qui ferment un cadre deviennent un rectangle tracé.**
LibreOffice dessine le cadre intérieur de chaque cellule en quatre traits
séparés. Un rectangle tracé dessine les quatre mêmes côtés : une entrée et
quatre nombres au lieu de quatre entrées et douze nombres. Les côtés gardent
exactement leur position. Seuls les coins peuvent changer : un trait qui
dépassait du cadre, ou s'arrêtait avant, d'au plus un pas d'arrondi — 0,1 pt,
ou un demi-trait au point entier — s'arrête maintenant pile sur le cadre, et le
carré du coin est peint une fois au lieu de deux. Le document LibreOffice
compte 219 cadres de ce type, soit 9 % de son archive. Mesuré sur toutes les
pages des quatre documents, aux quatre préréglages, dessins seuls, à 150 dpi :
aucun pixel ne bouge de plus de 99 niveaux de gris, et 200 sur 252 millions
bougent de plus de 64 — les 184 des coins de cadre plus les 16 de la feuille
Excel déjà notés en 2.3.0. Les deux versions sont indiscernables à 1000 %.

Deux ajustements de moindre portée : toutes les lignes d'une page tiennent
dans un seul bloc de texte, aucun décodeur ne donnant de sens aux blocs (−1 à
−2 %) ; et une série de traits de même couleur est triée d'un seul tenant même
quand l'épaisseur change, le noir composant de la même façon quelle que soit
l'épaisseur qui le pose (−2 % sur LibreOffice, où les bordures alternent trois
épaisseurs).

**Essayé et écarté**, mesures à l'appui :

| Piste | Mesure | Décision |
|---|---|---|
| Quadrillage LibreOffice tramé en PNG transparent | 7,2 Ko à 150 dpi, 13 Ko à 300 dpi, contre 4,3 Ko en vecteurs (7,7 Ko en 2.3.0) ; flou au zoom | écarté |
| Traits raboutés malgré leurs chevauchements | −21 % sur la 2.3.0 à `medium` ; les recouvrements peints deux fois s'éclaircissent | écarté (visible) |
| Type d'entrée compact pour la grille, ignoré par l'original | non mesuré : les stations F1GBD perdraient le quadrillage | écarté |
| Réglage LZMA par document (`lc`, `lp`, `pb`) | ±1 % ; `pb=0` reste le meilleur sur l'ensemble | sans objet |
| Lignes de texte triées par position | ±1 % selon le document, et l'ordre de peinture du texte qui se chevauche change | écarté |

### Mentions de copyright et de licence (version 2.4.1)

Aucun fichier ne portait de mention de copyright : la ressource de version
disait seulement « Licensed under the GNU GPL v3 », l'installateur annonçait
« PDF Teleporter project » et la fenêtre À propos se limitait à nommer la
licence. Or ce sont les mentions que la GPL oblige à conserver ; sans elles,
l'obligation d'attribution n'a pas d'objet. Chaque fichier source reçoit son
en-tête (voir « Licence »), la fenêtre À propos et `--version` affichent les
mentions requises par l'article 5 d) avec l'adresse des sources, l'installateur
renseigne éditeur, copyright et URL du projet. Aucune ligne de code
fonctionnelle n'est modifiée : les vecteurs de référence sont identiques.
`tests/test_notices.py` vérifie que tout nouveau fichier porte son en-tête et
que les versions de l'installateur et des exécutables concordent.

### Archives compactes `.psdz` (version 2.5.0)

Toutes les versions précédentes ont gagné à l'intérieur du format `.psdi`, sous
la contrainte que pose le décodeur de F1GBD. Cette contrainte a un prix : le
manifeste reste du JSON, qui répète à chaque fragment ses noms de clés et écrit
ses nombres en toutes lettres, et les photographies restent des JPEG. La 2.5.0
ajoute un second format qui lève les deux, au prix de la compatibilité :
l'archive compacte, enregistrée sous l'extension `.psdz`. Sur les quatre
documents réels des sections précédentes, tailles en octets :

| Document | Préréglage | F1GBD 2.1.0 | `.psdi` | `.psdz` | `.psdz` / `.psdi` | `.psdz` / F1GBD |
|---|---|---|---|---|---|---|
| SITREP OMS | `ultra_low` | 64 301 | 44 178 | 28 650 | −35 % | −55 % |
| SITREP OMS | `low` | 93 884 | 65 726 | 41 106 | −37 % | −56 % |
| SITREP OMS | `medium` | 124 656 | 102 216 | 61 512 | −40 % | −51 % |
| SITREP OMS | `high` | 166 411 | 140 849 | 81 358 | −42 % | −51 % |
| Point de situation LibreOffice | `ultra_low`, `low` | 11 556 | 9 764 | 5 966 | −39 % | −48 % |
| Point de situation LibreOffice | `medium`, `high` | 11 556 | 10 539 | 6 725 | −36 % | −42 % |
| Point de situation Excel | `ultra_low`, `low` | 24 592 | 11 506 | 6 230 | −46 % | −75 % |
| Point de situation Excel | `medium`, `high` | 24 592 | 10 979 | 6 213 | −43 % | −75 % |
| Bilan préfectoral | `ultra_low` | 15 719 | 7 828 | 6 286 | −20 % | −60 % |
| Bilan préfectoral | `low` | 21 012 | 8 378 | 6 682 | −20 % | −68 % |
| Bilan préfectoral | `medium` | 25 137 | 10 280 | 8 212 | −20 % | −67 % |
| Bilan préfectoral | `high` | 26 442 | 10 844 | 8 737 | −19 % | −67 % |

Sur les seize archives : 475 875 → 292 811 octets (−38 %), contre 682 154 pour
`psdi_lib` 2.1.0 (−57 %). Le SITREP à « Moyenne » passe de 8 min 06 s à
4 min 52 s en VARA HF selon l'estimation de l'application. Chacune des seize
archives compactes se recompose avec exactement les mêmes mots que son `.psdi`.

**Seulement sur demande.** Le décodeur de F1GBD ne lit pas ce format. Il le
refuse proprement, « Version d'archive non supportée: 3 », sans rien en tirer
de faux — la suite le vérifie sur son bytecode —, mais la station qui le reçoit
devra redemander le document. D'où les garde-fous : la case « Compatible avec la
version F1GBD (archive .psdi) » est cochée à chaque démarrage, le menu
contextuel a une entrée par format, le CLI une option `--compact`, et
l'extension annonce le format avant l'envoi. Le décodeur, lui, se fie au numéro
de version inscrit dans le fichier, pas à l'extension. Le format `.psdi` n'a
pas changé : les vecteurs de référence de la 2.4.1 passent tels quels.

**Le manifeste en colonnes** (version 3 du conteneur). Le contenu est celui du
manifeste JSON, rangé autrement : les boîtes de toutes les lignes ensemble,
chacune codée par différence avec la précédente ; les styles de fragment —
corps, couleur, graisse, famille — dans une table, chaque fragment n'en portant
que le numéro ; les dessins regroupés par style, chacun codé par rapport au
précédent du même style ; tout le texte d'un seul tenant à la fin. Les nombres
sont des entiers de longueur variable, en unités de la précision du préréglage,
et LZMA compresse ensuite l'ensemble comme avant. Décodé, ce corps redonne
exactement le manifeste JSON de la version 1 : l'encodeur le vérifie avant
d'écrire, et un manifeste que les colonnes ne sauraient pas décrire part en
JSON dans le corps compact plutôt que d'être approché. Le JSON est aussi gardé
quand il est le plus court, ce qui n'arrive que sur une page presque vide : sur
le banc décrit plus bas, aucun `.psdz` ne dépasse le `.psdi` du même document
de plus d'un octet. Le
manifeste compressé perd de 20 % (bilan préfectoral) à 46 % (Excel).

**Les photographies en AVIF, à fidélité au moins égale.** L'AVIF, issu du
codec vidéo AV1, rend une image comparable à celle d'un JPEG en bien moins
d'octets — encore faut-il vérifier « comparable ». Pour chaque photographie,
l'encodeur produit d'abord le JPEG que le `.psdi` aurait stocké et mesure sa
fidélité à l'image source par deux mesures, le PSNR et le SSIM standard
(fenêtre glissante de 7 pixels, celle de scikit-image). Il cherche
ensuite par dichotomie la qualité AVIF la plus basse qui égale ou dépasse ce
JPEG **sur les deux**. Le WebP est essayé aussi sur les petites images, où
l'AVIF a peu de prise, et le plus petit des candidats qui passent l'emporte, le
JPEG compris : une image où rien ne gagne reste un JPEG. Une illustration
stockée en PNG passe en WebP sans perte, aux mêmes pixels. Les images perdent
38 à 44 % sur le SITREP selon le préréglage, 13 à 21 % sur les deux petites
illustrations du bilan préfectoral.

Le double critère n'est pas une précaution de principe. Avec le PSNR seul, la
recherche retenait sur quatre images un AVIF plus lisse, au PSNR au moins égal
mais au SSIM inférieur — moins net à l'œil.

**Banc d'essai sur 51 PDF.** Le format a été passé sur 51 documents réels : les
19 documents de Direwolf, le manuel de FreeDV, la documentation de codec2, des
scans, des documents LaTeX, Google Docs, LibreOffice, des formulaires, des
images CMJN et les quatre documents ci-dessus, aux quatre préréglages, soit
204 archives et 749 pages. Le texte recomposé depuis le `.psdz` est identique
mot pour mot à celui du `.psdi` dans les 204 cas ; aucun pixel ne diffère en
dehors des images, et 2 570 pages sur 2 996 sont identiques au pixel près. Les
4 118 images recodées ont été remesurées avec scikit-image contre les pixels
mêmes que le JPEG codait : aucune n'est moins fidèle. Le banc a d'abord servi à
corriger l'encodeur. Sa première version estimait le SSIM sur des fenêtres
disjointes, plus vite ; sur les images petites ou minces, cette estimation
laissait passer des encodages très légèrement en dessous du JPEG — 29 images
dès les premiers documents passés, au pire −0,009 de SSIM. Il calcule désormais le SSIM exact, identique à celui
de scikit-image à 10⁻⁷ près. Sur l'ensemble du banc, le `.psdz` pèse 28 % de
moins que le `.psdi`.

**Les pages en image** (version 4 du conteneur) — scans, pages pivotées —
reçoivent la même recherche, page par page : −60 à −69 % sur le document pivoté
de la suite de tests.

**Ce que cela coûte.**

- *Le temps de compression.* Chercher la qualité AVIF code chaque photographie
  plusieurs fois : le SITREP se compresse en 3,6 à 9,6 s au lieu d'environ
  1 s selon le préréglage, le guide de Direwolf — 172 pages, 85 images — en
  une demi-minute au lieu de 6 à 9 s. Les documents sans
  photographie restent sous la seconde. L'estimation de taille de l'interface,
  qui compresse réellement, prend le même temps.
- *À l'écran, une image un peu plus lisse.* L'égalité de fidélité est garantie
  à la résolution de l'archive. Affichée à une autre échelle, une image
  recodée, plus lisse que le JPEG qu'elle remplace, ne le bat plus toujours :
  le banc compare chaque image recomposée à l'original rendu à 100 dpi, et sur
  les 519 zones où la recomposition reproduit l'original, le `.psdz` a un SSIM
  au moins égal dans 83 % des cas et un PSNR au moins égal dans 76 % ; en
  moyenne les deux sont meilleurs. Les écarts contraires restent sous 0,025 de
  SSIM et 1,5 dB de PSNR, sans perte de lisibilité visible sur les
  comparaisons côte à côte.
- *Le poids du PDF recomposé.* MuPDF ne sait incorporer ni l'AVIF ni le WebP :
  le décodeur les décode avec Pillow et range les pixels sans perte. Le SITREP
  recomposé à « Moyenne » pèse 1,66 Mo au lieu de 593 ko — sans effet sur la
  transmission, puisque c'est l'archive qui voyage, et sans perte ajoutée. La
  recomposition dure toujours environ une seconde.
- *Une dépendance plus récente.* Pillow 11.3 ou ultérieur, première version
  dont les roues embarquent l'AVIF. Lancé depuis les sources avec un Pillow
  qui ne l'a pas, le décodeur refuse un `.psdz` contenant de l'AVIF avant de
  rien recomposer, avec un message qui dit quoi installer ; le `.spec` refuse
  de construire un exécutable sans ce codec.

**Essayé et écarté** :

| Piste | Mesure | Décision |
|---|---|---|
| AVIF apparié au JPEG sur le seul PSNR | quatre images moins nettes (SSIM inférieur) | remplacé par le double critère |
| JPEG XL | greffon tiers nécessaire pour Pillow, et MuPDF ne le lit pas non plus | écarté |
| SSIM estimé sur des fenêtres disjointes, plus rapide | 29 images petites ou minces acceptées très légèrement sous le JPEG, selon le SSIM de référence | remplacé par le SSIM exact |
| WebP sans perte à l'effort maximal (méthode 6) | environ 1,5 point de mieux sur les illustrations, 35 fois plus lent : une minute au lieu de 9 s sur une revue de 23 pages | effort 4 retenu |

Au passage, un défaut ancien corrigé : depuis que la compression du PDF est un
sous-menu en cascade (série 2.0), la détection du menu contextuel cherchait sous
ce verbe une commande qu'il n'a plus. Le menu se lisait donc toujours absent —
*Outils* proposait de l'ajouter alors qu'il l'était, jamais de le retirer, et
`psditool shell status` répondait « non installé ». Elle cherche désormais la
commande du verbe de recomposition, qui en a une dans toutes les dispositions
que cet outil a écrites.

### Correctifs

**Espaces perdus entre les mots (2.5.0).** LaTeX ne tape aucune espace : il
place chaque mot en déplaçant le curseur. MuPDF devine alors les espaces et
donne à chacune un fragment à elle seule, que l'encodeur jetait avec tous les
fragments vides — celui de F1GBD aussi. Les mots se recollaient : la
recomposition du manuel de FreeDV se lisait « FreeDVisofficiallysupported… ».
Le même défaut touchait, plus rarement, les documents Word où une espace tapée
porte un autre style que ses voisines : « maladiesémergentes » dans le SITREP,
« complètementindisponible » dans le bilan préfectoral.

L'espace est maintenant rendue au mot qui la précède, là où les deux mots
tombent dans la même boîte de texte à la recomposition ; au-delà, chacun est
placé seul et une espace finale ne ferait que l'élargir. Une espace tapée est
toujours rendue. Une espace devinée ne l'est que sur une page qui ne tape
presque pas les siennes — moins d'une tapée pour dix devinées — et la règle
n'est pas une précaution : un tableau LibreOffice imprimé en PDF place chaque
glyphe à part, et MuPDF y devine des espaces au milieu de « sinistrées ». Sur
les 51 documents du banc d'essai, la part des mots de l'original retrouvés à la
recomposition passe de 74 % à 97 % : de 22 % à 100 % pour le manuel de FreeDV,
de 28 % à 99,5 % pour la documentation de codec2, de 35 % à 97 % pour un
ouvrage LaTeX de 117 pages. Les documents Word gagnent jusqu'à un point, le
tableau LibreOffice ne bouge pas.

Ces espaces sont de simples caractères dans le texte du manifeste : le
décodeur de F1GBD les affiche comme les autres — `TestOriginalDecoder` le
vérifie — et le format ne change pas. À « Moyenne », le `.psdi` est identique
octet pour octet pour 36 des 51 documents ; pour les autres il varie de −1,7 %
à +2,5 %, et de +10 % sur une page de 1 Ko. Les vecteurs de référence de la
2.4.1 passent tels quels.

**Espaces de trop, espaces oubliées : ce que MuPDF lit de travers (2.5.0).**
Deux défauts restaient après ce correctif. Ils ne viennent pas de l'encodeur :
MuPDF les commet déjà en lisant l'original.

*Des lettres posées à pas fixe.* Le point de situation LibreOffice se
recomposait en « Nbr e de places r est ant es en cent r es d’ héber gt ». Le
PDF, imprimé par « Microsoft: Print To PDF », place chaque lettre de ces
cellules au pas d'une police à chasse fixe, 0,6 em — la copie Excel du même
tableau les compose en Courier New —, mais les dessine en DejaVu Sans, dont le
« i » fait moins de la moitié de ce pas et le « m » davantage. Derrière chaque
lettre étroite reste un vide, jusqu'à un tiers d'em, que MuPDF prend pour une
espace ; l'original lui-même s'affiche lettres écartées.

L'encodeur reconnaît maintenant une police posée à pas fixe quelle que soit la
largeur de ses lettres : sur la page, la plupart des pas d'une lettre à la
suivante sont égaux à 5 % près, et ce pas est pris par au moins huit lettres
différentes dont les largeurs s'écartent d'au moins un tiers du pas. Un texte
proportionnel avance de la largeur de chaque lettre et ne remplit pas la
première condition ; une police à chasse fixe, dont chaque lettre a la largeur
du pas, ne laisse aucun vide à mal lire ; quelques capitales de largeur voisine
tombent parfois sur un même pas — « PQ », « AB » dans les pages de géométrie
d'un ouvrage LaTeX du banc —, jamais huit lettres. Sur une telle police, c'est
le pas qui tranche : deux lettres à un pas l'une de l'autre sont du même mot,
et une espace prend un pas de plus, tapée ou laissée vide. Une ligature compte
autant de pas que de lettres : « trafic », « affectés », « fluvial » ne sont
plus coupés en deux. Deux mots séparés par un seul pas vide passent dans le
même fragment, l'espace entre eux, sans quoi le décodeur de F1GBD, qui
concatène les fragments d'une ligne, lisait « demandede ». 154 des 567 lignes
du document sont corrigées, et son vocabulaire est désormais exactement celui
de la copie Excel, aux valeurs des cellules près, qui diffèrent entre les deux
fichiers.

*Des espaces jamais devinées après une ligature ou un symbole.* MuPDF ne
devine pas d'espace après un caractère qui n'est ni du latin de base, ni du
grec, du cyrillique, de l'hébreu ou de l'arabe, ni une ponctuation générale, un
exposant ou un symbole monétaire (`may_add_space` dans son `stext-device.c`).
C'est voulu : le chinois, le japonais ou le thaï s'écrivent sans espaces. Mais
la règle laisse de côté les ligatures latines, rangées dans un autre bloc
Unicode — d'où « trade offwith » dans la documentation de codec2 —, et les
symboles mathématiques : « x ∈X », « [0, 1] →X ». Sur une page dont toutes les
espaces sont devinées, l'encodeur ajoute après ces caractères l'espace que
MuPDF aurait devinée après une lettre, à la même distance, 0,15 em : après les
ligatures latines, les lettres latines et grecques des blocs étendus, les
flèches, les opérateurs et autres symboles techniques ou géométriques, les
codes privés des polices de symboles et les alphabets mathématiques, jamais
après une écriture sans espaces. Le banc en compte 1 997 dans l'ouvrage LaTeX
de 117 pages, 63 dans codec2, 2 dans un article LaTeX, toutes après un symbole
ou une ligature.

La mesure du correctif précédent compte les mots que MuPDF lit dans
l'original : elle tient ces lectures fautives pour justes, et compte comme
perdus les mots une fois corrigés — avec elle, le banc redescend de 97 % à
96 %, l'ouvrage de 117 pages de 97 % à 91 %. Mesurée contre une lecture juste,
la part des mots retrouvés à la recomposition passe de 77 % à 100 % pour le
point de situation, contre la copie Excel du même tableau, mots alphabétiques
seulement puisque les valeurs des cellules diffèrent — par le décodeur de
F1GBD aussi, qui partait de 76 %. Contre `pdftotext` (Poppler), qui place ces
espaces, elle passe de 71,7 % à 81,7 % pour l'ouvrage de 117 pages, de 92,9 %
à 93,7 % pour codec2 et de 98,15 % à 98,17 % pour l'article ; l'écart qui
reste tient au découpage des formules — indices, délimiteurs, accents —, pas aux
espaces entre les mots.

Les 47 autres documents du banc donnent des archives identiques octet pour
octet, comme les documents de `reference/` et les vecteurs de référence. Le
point de situation LibreOffice perd 3 % dans les deux formats — moins
d'espaces, moins de fragments —, les trois documents LaTeX varient de −0,05 %
à +0,1 %, et sur l'ensemble du banc le `.psdz` reste 28 % plus léger que le
`.psdi`. Le format ne change pas : ce ne sont que des caractères du texte. Deux
documents de test reproduisent ces défauts, avec leurs vecteurs, et
`TestOriginalDecoder` vérifie que le décodeur de F1GBD lit les mots corrigés.

**Rotation ignorée en mode texte seul.** L'original désactive entièrement la
détection automatique de mode dès que « texte seul » est coché
(`if mode == 'struct' and not skip_images:`) — la 2.1.0 a conservé cette ligne.
Un document pivoté coché ainsi se recompose donc de travers. Ignorer les images
est un choix légitime ;
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

**Empaquetage.** Le binaire 1.0.6 embarquait 846 Mo décompressés et 9 478
modules, dont 330 Mo de PyTorch, plus transformers, pyarrow, scipy, sklearn et
onnxruntime — aucun atteignable depuis le code, tous ramassés par PyInstaller
dans l'environnement de développement. F1GBD a réglé ce point de son côté : la
2.1.0 tient en 107 Mo et 270 modules. Ici, le fichier `.spec` nomme
explicitement les paquets à exclure, et le script de compilation alerte si la
sortie dépasse 250 Mo.

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
vecteurs de référence capturés avant l'ajout sont identiques au bit près
après. L'interopérabilité et le poids des archives ne bougent pas.

Depuis la 2.3.0, une image dont le cadre n'a pas de surface, dont la charge
utile manque ou que MuPDF ne sait pas lire n'est plus une défaillance : elle
est sautée avec un avertissement dans le journal, et le reste de la page se
recompose (voir « Relu avant publication »).

Le corps d'une archive compacte est lu avec la même méfiance, et plus de
raisons de l'exercer : un format binaire en colonnes n'a pas la syntaxe JSON
pour trahir une corruption. Table des sections tronquée, données en trop après
la dernière page, nombre trop long, page décrite deux fois, numéro de style
inexistant, texte qui n'est pas de l'UTF-8 : chaque cas remonte une `PsdiError`
qui nomme ce qui ne va pas, et `tests/test_compact.py` abîme une archive de
chacune de ces façons. Une image AVIF ou WebP n'est décodée que sous un plafond
de 50 mégapixels.

---

## Tests

```
python -m unittest discover -s tests -t .
```

264 tests, sans dépendance en plus de celles de l'application.

| Fichier | Objet |
|---|---|
| `tests/test_format.py` | conteneur : troncature, plafonds, entrées hostiles, dictionnaire LZMA, coordonnées à largeur fixe |
| `tests/test_engine.py` | aller-retour, choix de mode, déterminisme, fidélité, compactage des dessins et cadres, codage du texte, espaces entre les mots — y compris lettres à pas fixe et espaces après ligatures et symboles —, codec des images |
| `tests/test_compact.py` | archives `.psdz` : manifeste en colonnes rendu à l'identique, repli JSON, corps abîmés, choix AVIF et WebP à fidélité au moins égale, pages en image, ligne de commande |
| `tests/test_interop.py` | vecteurs de référence `.psdi` et `.psdz`, décodage historique, décodeur d'origine exécuté depuis son bytecode (texte, espaces rétablies ou corrigées, bordures, images, place des glyphes, refus des `.psdz`) |
| `tests/test_shell.py` | menu contextuel : forme des clés de registre, une cascade par format, commandes comprises par le CLI, détection et désinstallation |
| `tests/test_notices.py` | en-têtes de copyright et SPDX, mentions de la fenêtre À propos et de `--version`, concordance des versions |

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

Depuis la 2.5.0, cinq vecteurs figent aussi des archives compactes, sur des
documents sans image : les octets AVIF et WebP dépendent de la version des
bibliothèques que Pillow embarque, qui n'est pas du ressort de ce projet. Les
images compactes sont couvertes par des tests de propriété — taille, fidélité
mesurée — plutôt que d'empreinte. `tests/vectors/sample-2.5.0-medium.psdz`
protège l'autre sens : écrite par la 2.5.0 et conservée telle quelle, cette
archive doit continuer à se recomposer à l'identique quel que soit l'avenir de
l'encodeur, puisque des stations en garderont.

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

Ce décodeur simulé a désormais un juge. `reference/tools/bench.py` charge le
`psdi_lib` 2.1.0 de F1GBD depuis son bytecode, dans le même interpréteur et
avec le même MuPDF que son binaire, et fait recomposer nos archives par son
moteur et les siennes par le nôtre. Le test unitaire reste parce qu'il tourne
sans le bytecode, à chaque exécution de la suite ; le banc est ce qui prouve
que la simulation est fidèle.

`TestOriginalDecoder` fait entrer ce juge dans la suite : quand
`reference/disasm/psdi_lib.pyc` est à côté de l'application et que
l'interpréteur est un Python 3.13 — un `.pyc` ne se charge que dans la version
qui l'a produit —, nos archives de cinq documents de test, à trois préréglages,
sont recomposées par le vrai décodeur de F1GBD : même texte que par le nôtre,
bordures LibreOffice présentes, images PNG placées, et chaque glyphe à moins
d'un millième de point de sa place quand la boîte de ligne est ouverte (voir
la 2.4.0). Une archive compacte, structurée ou en image, y est refusée par son
numéro de version, avec un message, au lieu d'être lue de travers. Ailleurs, ces
cinq tests sont sautés, pas échoués. `PSDI_REFERENCE_PYC` peut désigner le
fichier explicitement.

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

version 3 — structuré compact (.psdz)
    uint32  crc32 du corps non compressé
    uint32  longueur du corps compressé
    octets  corps, flux LZMA brut, filtre LZMA2 unique
    uint16  nombre d'images, puis les images comme en version 1

version 4 — image de page compacte (.psdz)
    disposition de la version 2
```

Le bit 0 des drapeaux signale une charge utile compressée en LZMA. Il n'est pas
toujours positionné : les données JPEG sont déjà codées par entropie et LZMA les
gonfle en général, chaque charge utile est donc stockée dans la forme la plus
petite des deux.

Une charge utile de version 1 est un JPEG ou, depuis la 2.3.0, un PNG ; les deux
décodeurs la passent telle quelle à `insert_image`, qui reconnaît le format à
ses octets. Une charge utile de version 2 est toujours un JPEG. Le dictionnaire
LZMA de l'encodeur ne dépasse jamais 8 Mio, la fenêtre par défaut du décodeur
d'origine.

**Cette disposition ne doit pas être modifiée.** Réordonner ou redimensionner un
champ rompt l'interopérabilité avec les stations en service.

Les versions 3 et 4 sont les archives compactes. Elles gardent la signature et
le champ de version, si bien qu'un lecteur antérieur — celui de F1GBD compris —
les refuse par leur numéro au lieu de les lire de travers. Une charge utile y
peut aussi être un AVIF ou un WebP, reconnus à leur signature comme le JPEG et
le PNG ; MuPDF ne sachant incorporer ni l'un ni l'autre, le décodeur les décode
avec Pillow et place les pixels sans perte.

Le corps de la version 3 commence par un octet. `1` : la suite est le manifeste
JSON d'une archive de version 1, écrit tel quel pour un manifeste que les
colonnes ne savent pas décrire. `0` : la suite est faite de onze sections,
chacune une longueur `uint32` suivie de ses octets :

| Section | Contenu |
|---|---|
| en-tête | JSON : les clés de premier niveau du manifeste autres que `pages`, le nombre de pages, l'échelle des coordonnées (1, 10 ou 100 unités par point), les tables de styles de fragment et de dessin, et si les références d'images étaient des chaînes |
| pages | par page : masque des clés présentes, numéro, nombres d'éléments, coin de boîte ouverte |
| lignes | par ligne : nombre de fragments et deux drapeaux (boîte ouverte, boîte de fragment présente) |
| boîtes de ligne | codées par différence avec la ligne précédente |
| fragments | par fragment : numéro dans la table des styles |
| boîtes de fragment | relatives à leur ligne |
| styles de dessin | par dessin : numéro dans la table des styles de dessin |
| boîtes de dessin | codées par différence avec le dessin précédent du même style |
| compléments | placements d'images, cases de formulaire cochées, zones de liens |
| longueurs | longueur en octets de chaque chaîne, dans l'ordre |
| texte | toutes les chaînes, en UTF-8, mises bout à bout |

Les nombres des sections entières sont des entiers LEB128, les signés en
zigzag ; une coordonnée est un entier en unités de 1/échelle de point. Le
décodage redonne un manifeste égal à celui qui a été encodé, ce que l'encodeur
vérifie avant de retenir les colonnes.

Le manifeste JSON est lui aussi un contrat. Les clés que cette version écrit en
plus de la spécification d'origine sont toutes *optionnelles* et ignorées par un
décodeur qui ne les connaît pas :

| Clé | Où | Contenu | Comportement du décodeur d'origine |
|---|---|---|---|
| `{"t": "line", …}` | entrée de `dr` | trait ; écrit par la 2.2.x seulement, toujours lu | sauté par `if not d['t'] == 'rect'` |
| `lk` | page | liste `[{"b": boîte, "u": uri}]` | jamais lue |

Depuis la 2.3.0, un trait est un `rect` ordinaire de hauteur ou de largeur
nulle, tracé (`c`, `w`) : rien de nouveau pour un décodeur, et le décodeur
d'origine le dessine.

La 2.4.0 n'ajoute aucune clé, mais écrit les mêmes autrement — trois points
qu'un autre décodeur doit savoir :

- les coordonnées des tableaux `r` et `b` sont écrites à largeur fixe :
  `84.100`, `  84`. Tout lecteur JSON y lit les mêmes nombres ;
- la boîte `b` d'une ligne qui tient en taille réelle dans sa boîte d'origine
  a pour coin bas droit celui de la page. Elle ne décrit plus l'étendue du
  texte, seulement la place où il peut se poser. Un décodeur qui centrerait ou
  justifierait le texte dans sa boîte ne pourrait plus s'y fier ; aucun ne le
  fait, ni celui de F1GBD ni celui-ci ;
- toutes les lignes d'une page sont dans un seul bloc de `tb`, et un rectangle
  tracé sans remplissage peut être le cadre de quatre traits d'origine.

Ces deux comportements sont ceux de la boucle `_rebuild_struct_mode` de
`psdi_lib` 2.1.0, inchangée depuis `pdf_trans` 1.0.6. Ajouter une clé à ce
tableau n'est acceptable qu'à cette même condition : le décodeur d'origine doit
l'ignorer sans erreur, ce que le test `TestInteropSafeAdditions` vérifie en
exécutant un port fidèle de cette boucle, et ce que le banc de `reference/`
confirme sur le bytecode réel.

---

## Compilation pour Windows

```
build.cmd full
```

Rien à installer au préalable : le script provisionne son propre environnement
virtuel sous `.venv-build`, y installe les dépendances et PyInstaller, puis
exécute chaque étape dedans.

| | |
|---|---|
| `build.cmd` | l'application seule |
| `build.cmd full` | l'application, puis l'installateur |
| `build.cmd fresh` | reconstruit l'environnement de zéro |

L'environnement dédié n'est pas une question de propreté. PyInstaller embarque
tout ce qu'il trouve d'importable dans l'environnement depuis lequel il
s'exécute : compiler depuis un interpréteur généraliste est exactement la façon
dont une version se retrouve à transporter des paquets que l'application
n'importe jamais — la 1.0.6 de l'original livrait 846 Mo ainsi. Cela supprime
aussi le
piège des deux interpréteurs, où les dépendances sont installées dans un Python
et PyInstaller lancé sous un autre, produisant un exécutable qui n'échoue que
sur une machine vierge.

La sortie arrive dans `dist\PDFteleporter\` et `dist\installer\`, avec une
empreinte SHA-256 à côté de l'installateur.

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
réellement. Depuis la 2.5.0, il refuse aussi un Pillow qui ne sait pas traiter
l'AVIF et le WebP : l'exécutable produit ne saurait pas recomposer les `.psdz`
qu'on lui enverrait.

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

Copyright (C) 2026 The PDF Teleporter contributors. D'après PDF Teleporter et
le format `.psdi` de F1GBD (Jean-Louis Naudin), ADRASEC 77 – FNRASEC.

GNU GPL v3 (`GPL-3.0-only`), héritée de l'implémentation originale : celle-ci
renvoie au texte de la version 3 sans mention « ou ultérieure », et un dérivé
ne peut pas élargir ce qu'il a reçu. PyMuPDF est sous AGPL v3, PyQt6 sous
GPL v3.

Chaque fichier source porte sa mention de copyright et son identifiant SPDX ;
ceux qui descendent de l'implémentation de F1GBD (`engine.py`, `format.py`,
`presets.py`, `test_interop.py`) le nomment. C'est par ces mentions que la GPL
organise l'attribution : quiconque redistribue ce code doit les conserver
intactes (article 4). Le gabarit qui termine `LICENSE.txt` fait partie du texte
de la licence et reste tel quel.

La fenêtre À propos et `psditool --version` affichent le copyright, l'absence de
garantie, l'emplacement du texte de la licence et l'adresse du code source
(article 5 d). Un installateur diffusé hors de ce dépôt — dans un recueil
d'outils, sur une clé d'exercice — doit être accompagné de cette adresse ou des
sources elles-mêmes (article 6).
