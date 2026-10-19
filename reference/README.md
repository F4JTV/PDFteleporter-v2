# PDF Teleporter — matériel de référence

Ce dossier conserve ce dont l'application a été reconstituée : l'implémentation
originale de PDF Teleporter et du format `.psdi`, œuvre de **F1GBD
(Jean-Louis Naudin), ADRASEC 77 – FNRASEC**, en versions 1.0.6 et 2.1.0.

Rien ici n'est importé par `application/` ni embarqué par le build. Le dossier
est conservé parce que le format doit rester interopérable et que la seule
description faisant autorité de ce format est ce bytecode : c'est en le faisant
tourner que la suite de tests (`TestOriginalDecoder`) et le banc vérifient que
les archives émises par l'application restent lisibles par l'original.

## Contenu

```
sources/pdf_trans.py         moteur 1.0.6, 37 fonctions + PDFTransferManager
sources/psdi_lib.py          moteur 2.1.0 — le même bytecode à une fonction près
sources/PDFteleporter.py     IHM 2.1.0, PyQt6 (la 1.0.6 était en Tkinter)
disasm/*.dis                 désassemblages complets, le recours faisant foi
disasm/*.pyc                 le bytecode extrait lui-même (CPython 3.13)
bench/bench.json             banc psdi_lib 2.1.0 contre psditool 2.4.0 : tailles,
                             temps, et les 128 recompositions croisées
bench/*.diff                 différence instruction par instruction des deux moteurs
tools/bench.py               le banc — charge psdi_lib depuis son .pyc
tools/cmp_pyc2.py            comparaison de deux .pyc objet de code par objet de code
tools/pycdc-python313.patch  support Python 3.13 pour Decompyle++
NOTES-EXTRACTION.md          notes d'extraction de la 2.1.0 et comparaison avec la 1.0.6
```

## Provenance et droits

L'original est distribué sous **GNU GPL v3** en bundle PyInstaller pour
Windows, sans code source publié. La GPL v3 accorde le droit d'accéder au source
correspondant et de modifier l'œuvre ; le source a donc été récupéré depuis le
bytecode livré.

Les fichiers de `sources/` et de `disasm/`, comme la différence de `bench/`,
reproduisent le travail de F1GBD tel que le bytecode le contient ; seules la
décompilation et la mise en forme sont de notre fait. Ils restent sous sa
licence. Les outils de `tools/`, le banc et les notes ont été écrits pour ce
projet, sous la même licence (`GPL-3.0-only`, voir `LICENSE.txt` à la racine).

## Ce que montrent les deux versions

Les deux bundles n'ont pas la même taille : la 1.0.6 embarquait 9 478 modules
et 846 Mo, dont deux seulement étaient l'application ; la 2.1.0 en embarque
270 pour 107 Mo. Le moteur, lui, n'a changé que dans `_extract_page_struct`
(drapeaux d'extraction du texte, portée de la déduction de ligature) ; conteneur,
préréglages, optimisation des images, détection de mode et couche TNC/VARA sont
le bytecode 1.0.6 inchangé. `NOTES-EXTRACTION.md` donne le détail,
`bench/pdf_trans-1.0.6_vs_psdi_lib-2.1.0.diff` la preuve.

## Lire les sources récupérés

Ce sont des spécifications à lire, pas des modules importables. Toutes les
instructions y figurent, dans l'ordre, identifiants intacts, mais la structure
des `try`/`except` est faussée : CPython 3.11+ a déplacé la gestion d'exceptions
dans des tables hors-ligne que pycdc ne reconstruit qu'en partie, si bien que
les gestionnaires ne se referment pas et que les instructions suivantes
s'imbriquent d'un niveau de trop. La 2.1.0 a de plus perdu ses docstrings et
certaines compréhensions sortent brouillées.

Pour toute question de flot de contrôle, consulter `disasm/` plutôt que se fier
à l'indentation. Pour le moteur, la lecture de référence reste `pdf_trans.py`
plus les deux changements notés dans `NOTES-EXTRACTION.md`.

## Refaire l'extraction

`tools/pycdc-python313.patch` s'applique sur `zrax/pycdc` master (b428976) et
ajoute les opcodes 3.13 qui bloquaient entièrement la décompilation —
`MAKE_FUNCTION` sans oparg, `SET_FUNCTION_ATTRIBUTE`, les nouveaux opcodes de
f-strings, `CALL_KW`, les compréhensions inlinées, `DICT_MERGE` /
`CALL_FUNCTION_EX`, `LOAD_SUPER_ATTR`, et plusieurs autres. Sans lui, le moteur
se décompile en dix-huit lignes.

```
git clone https://github.com/zrax/pycdc && cd pycdc
git apply pycdc-python313.patch
cmake -DCMAKE_BUILD_TYPE=Release . && make -j4

pip install pyinstxtractor-ng
pyinstxtractor-ng PDFteleporter.exe
pycdc PDFteleporter.exe_extracted/PDFteleporter.pyc
pycdc PDFteleporter.exe_extracted/PYZ.pyz_extracted/psdi_lib.pyc
```

## Rejouer le banc

Un `.pyc` ne se charge que dans la version de Python qui l'a produit : le banc
demande un CPython **3.13**, avec PyMuPDF **1.28.2** (le même MuPDF que la DLL
du binaire) et les dépendances de l'application. Depuis ce dossier :

```
python3.13 tools/bench.py disasm/psdi_lib.pyc ../application <dossier de sortie>
```

Le banc construit les documents de test de `application/tests/fixtures.py`,
compresse chacun aux quatre préréglages par les deux moteurs, puis fait
recomposer chaque archive par chaque décodeur : tailles, temps de compression et
de recomposition, nombre de mots retrouvés et écart de rendu à l'original. Il
écrit une ligne JSON par cas à l'écran et le tout dans
`<dossier de sortie>/bench.json`. `bench/bench.json` est cette sortie pour
psditool 2.4.0 ; la 2.4.1 et la 2.5.0 émettent les mêmes archives `.psdi` octet
pour octet, ses tailles et ses recompositions valent donc aussi pour elles. Les
archives compactes `.psdz` de la 2.5.0, que `psdi_lib` refuse par leur numéro
de version, n'y figurent pas.

Pour comparer deux bytecodes, docstrings et numéros de ligne ignorés :

```
python3.13 tools/cmp_pyc2.py ancien.pyc nouveau.pyc
```

La disposition du conteneur `.psdi` et les clés du manifeste sont documentées
dans `application/README.md`, section « Format d'archive ».
