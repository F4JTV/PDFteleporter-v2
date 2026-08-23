# PDF Teleporter — archive complète

Deux moitiés. `application/` est ce qui se compile et se diffuse ;
`reference/` est le matériel dont elle a été reconstituée, conservé parce que le
format doit rester interopérable et que la seule description faisant autorité de
ce format est ce bytecode.

```
application/     réécriture PyQt6, build et installateur Windows
reference/       sources récupérés de l'implémentation originale
```

Cette application est dérivée du travail de **F1GBD (ADRASEC 77 – FNRASEC)**,
auteur du PDFteleporter original et du format `.psdi`, distribué sous GNU GPL v3.
Voir `application/README.md` pour l'attribution détaillée et la liste des
changements.

## application/

```
psditool/format.py          conteneur .psdi, disposition binaire
psditool/engine.py          compression et recomposition, deux modes
psditool/presets.py         préréglages de qualité, estimations de transfert
psditool/cli.py             CLI headless, aussi le point d'entrée du menu contextuel
psditool/shell_windows.py   intégration Explorateur (registre)
psditool/resources.py       résolution des ressources embarquées
psditool/gui/               interface PyQt6, style Fusion
pdfteleporter.py            point d'entrée unique : IHM / menu contextuel / CLI
pdfteleporter.spec          PyInstaller, deux exécutables depuis une analyse
build/version_info*.txt     ressources de version Windows
installer/pdfteleporter.iss script Inno Setup 7
build.cmd                   construit l'application, puis l'installateur
assets/                     icône de l'application
```

Démarrage rapide sous Windows :

```
pip install -r requirements.txt pyinstaller
build.cmd full
```

Depuis les sources, aucune compilation n'est nécessaire :
`python pdfteleporter.py`.

## reference/

L'original était distribué en bundle PyInstaller sans source, sous GNU GPL v3,
qui accorde le droit de le récupérer et de le modifier. Seuls deux des 9478
modules du bundle étaient l'application ; le reste était des bibliothèques
tierces, la plupart inatteignables depuis le code.

```
sources/pdf_trans.py         moteur, 37 fonctions + PDFTransferManager
sources/PDFteleporter.py     IHM Tkinter originale
disasm/*.dis                 désassemblage complet, le recours faisant foi
disasm/*.pyc                 le bytecode extrait lui-même
tools/pycdc-python313.patch  support Python 3.13 pour Decompyle++
```

Les sources récupérés sont une spécification à lire, pas des modules
importables. Toutes les instructions y figurent, dans l'ordre, docstrings et
identifiants intacts, mais la structure des `try`/`except` est faussée :
CPython 3.11+ a déplacé la gestion d'exceptions dans des tables hors-ligne que
pycdc ne reconstruit qu'en partie, si bien que les gestionnaires ne se referment
pas et que les instructions suivantes s'imbriquent d'un niveau de trop. Pour
toute question de flot de contrôle, consulter `disasm/` plutôt que se fier à
l'indentation.

`tools/pycdc-python313.patch` s'applique sur `zrax/pycdc` master et ajoute les
opcodes 3.13 qui bloquaient entièrement la décompilation — `MAKE_FUNCTION` sans
oparg, `SET_FUNCTION_ATTRIBUTE`, les nouveaux opcodes de f-strings, `CALL_KW`,
les compréhensions inlinées, les compréhensions de dictionnaire et d'ensemble,
et plusieurs autres. Sans lui, le moteur se décompile en dix-huit lignes.

`NOTES-EXTRACTION.md` consigne la disposition `.psdi` reconstituée et les
constantes, indépendamment des fichiers source.
