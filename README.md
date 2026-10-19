# PDF Teleporter — archive complète

Deux moitiés. `application/` est ce qui se compile et se diffuse ;
`reference/` est le matériel dont elle a été reconstituée, conservé parce que le
format doit rester interopérable et que la seule description faisant autorité de
ce format est ce bytecode.

```
application/     réécriture PyQt6, build et installateur Windows
reference/       sources récupérés de l'implémentation originale, 1.0.6 et 2.1.0,
                 et le banc qui compare les deux moteurs
```

Cette application est dérivée du travail de **F1GBD (ADRASEC 77 – FNRASEC)**,
auteur du PDFteleporter original et du format `.psdi`, distribué sous GNU GPL v3.
Voir `application/README.md` pour l'attribution détaillée et la liste des
changements. Le format `.psdi` est conservé à l'identique ; la version 2.5.0
ajoute, au choix de l'opérateur, une archive compacte `.psdz` que seule cette
application lit.

## application/

```
psditool/format.py          conteneurs .psdi et .psdz, disposition binaire
psditool/engine.py          compression et recomposition, deux modes
psditool/presets.py         préréglages de qualité, estimations de transfert
psditool/cli.py             CLI headless, aussi le point d'entrée du menu contextuel
psditool/shell_windows.py   intégration Explorateur (registre)
psditool/resources.py       résolution des ressources embarquées
psditool/gui/               interface PyQt6, style Fusion
tests/                      243 tests : conteneur, format compact, moteur,
                            interopérabilité, menu contextuel, mentions
tests/vectors/golden.json   empreintes des archives de référence
tests/vectors/*.psdz        archive compacte conservée, garde du décodeur
pdfteleporter.py            point d'entrée unique : IHM / menu contextuel / CLI
pdfteleporter.spec          PyInstaller, deux exécutables depuis une analyse
build/version_info*.txt     ressources de version Windows
installer/pdfteleporter.iss script Inno Setup 7
build.cmd                   environnement virtuel, tests, application, installateur
assets/                     icône de l'application
```

Démarrage rapide sous Windows :

```
build.cmd full
```

Le script crée son propre environnement virtuel sous `.venv-build` et y installe
ce qu'il faut. Depuis les sources, aucune compilation n'est nécessaire :
`pip install -r requirements.txt` puis `python pdfteleporter.py`.

## reference/

L'original est distribué en bundle PyInstaller sans source, sous GNU GPL v3,
qui accorde le droit de le récupérer et de le modifier. Deux versions ont été
reconstituées : la 1.0.6, point de départ de la réécriture, et la 2.1.0 d'août
2026, la plus récente publiée.

```
sources/pdf_trans.py         moteur 1.0.6, 37 fonctions + PDFTransferManager
sources/psdi_lib.py          moteur 2.1.0 — le même bytecode à une fonction près
sources/PDFteleporter.py     IHM 2.1.0, PyQt6 (la 1.0.6 était en Tkinter)
disasm/*.dis                 désassemblages complets, le recours faisant foi
disasm/*.pyc                 le bytecode extrait lui-même
bench/bench.json             banc psdi_lib 2.1.0 contre psditool 2.4.0 : tailles,
                             temps, et les 128 recompositions croisées
bench/*.diff                 différence instruction par instruction des deux moteurs
tools/bench.py               le banc — charge psdi_lib depuis son .pyc
tools/cmp_pyc2.py            comparaison de deux .pyc objet de code par objet de code
tools/pycdc-python313.patch  support Python 3.13 pour Decompyle++
NOTES-EXTRACTION.md          notes d'extraction de la 2.1.0 et comparaison avec la 1.0.6
```

Les deux bundles n'ont pas la même taille : la 1.0.6 embarquait 9 478 modules
et 846 Mo, dont deux seulement étaient l'application ; la 2.1.0 en embarque
270 pour 107 Mo. Le moteur, lui, n'a changé que dans `_extract_page_struct`
(drapeaux d'extraction du texte, portée de la déduction de ligature) ; conteneur,
préréglages, optimisation des images, détection de mode et couche TNC/VARA sont
le bytecode 1.0.6 inchangé. `NOTES-EXTRACTION.md` donne le détail, `bench/` la
preuve.

Les sources récupérés sont une spécification à lire, pas des modules
importables. Toutes les instructions y figurent, dans l'ordre, identifiants
intacts, mais la structure des `try`/`except` est faussée : CPython 3.11+ a
déplacé la gestion d'exceptions dans des tables hors-ligne que pycdc ne
reconstruit qu'en partie, si bien que les gestionnaires ne se referment pas et
que les instructions suivantes s'imbriquent d'un niveau de trop. La 2.1.0 a de
plus perdu ses docstrings et certaines compréhensions sortent brouillées. Pour
toute question de flot de contrôle, consulter `disasm/` plutôt que se fier à
l'indentation ; pour le moteur, la lecture de référence reste `pdf_trans.py`
plus les deux changements notés.

`tools/pycdc-python313.patch` s'applique sur `zrax/pycdc` master et ajoute les
opcodes 3.13 qui bloquaient entièrement la décompilation — `MAKE_FUNCTION` sans
oparg, `SET_FUNCTION_ATTRIBUTE`, les nouveaux opcodes de f-strings, `CALL_KW`,
les compréhensions inlinées, `DICT_MERGE` / `CALL_FUNCTION_EX`,
`LOAD_SUPER_ATTR`, et plusieurs autres. Sans lui, le moteur se décompile en
dix-huit lignes.

La disposition du conteneur `.psdi` et les clés du manifeste sont documentées
dans `application/README.md`, section « Format d'archive ».
