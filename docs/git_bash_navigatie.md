# Git Bash — navigatie spiekbriefje

Snelle referentie voor het navigeren tussen mappen in Git Bash op Windows. Bedoeld als naslag tijdens het werk aan het energy dashboard project.

---

## De drie kerncommando's

**`pwd`** — *print working directory*. Toont waar je nu staat. Handig als je even de weg kwijt bent.

**`ls`** — *list*. Toont wat er in de huidige map zit.
- `ls` — gewone inhoud
- `ls -la` — uitgebreid, inclusief verborgen bestanden (alles dat met `.` begint, zoals `.gitignore`, `.env`, `.venv`)
- `ls scripts/` — de inhoud van een specifieke submap zonder erheen te navigeren

**`cd`** — *change directory*. De daadwerkelijke navigatie.

```bash
cd scripts                          # naar submap "scripts"
cd ..                               # één niveau omhoog (parent map)
cd ../..                            # twee niveaus omhoog
cd /d/Prive/ha-energy-dashboard     # absoluut pad — direct ergens heen
cd ~                                # naar je home directory
cd -                                # terug naar de vorige map waar je was
cd                                  # alleen "cd" → ook home directory
```

---

## Windows-paden in Git Bash

Je Windows-pad `D:\Prive\ha-energy-dashboard` wordt in Git Bash `/d/Prive/ha-energy-dashboard`. Drie regels:

1. Drive letter wordt klein en met `/` ervoor: `D:\` → `/d/`
2. Backslashes worden forward slashes: `\` → `/`
3. Geen dubbele punt achter de drive letter

Voorbeeld:

```
Windows:  D:\Prive\ha-energy-dashboard\scripts
Bash:     /d/Prive/ha-energy-dashboard/scripts
```

---

## Twee handige trucs

**Tab-completion.** Typ een paar letters van een mapnaam, druk Tab, Bash maakt het af.

```bash
cd ha<Tab>      # wordt → cd ha-energy-dashboard/
```

Bij meerdere matches: dubbel-tab toont opties.

**Spaties in paden.** Zet het pad tussen quotes:

```bash
cd "/c/Program Files/..."
```

Of escape de spatie met backslash: `cd /c/Program\ Files/...`. Quotes is makkelijker.

---

## Standaard workflow voor dit project

Open Git Bash. Je begint in `~` (home directory). Dan:

```bash
cd /d/Prive/ha-energy-dashboard
source .venv/Scripts/activate
```

Prompt eindigt nu op `(main)` (git branch) plus `(.venv)` ervoor (active environment). Klaar om te werken.

**Snelle ingang via Verkenner.** Klik in Verkenner met rechtermuisknop in een lege ruimte van een map → **Open Git Bash here**. Je bent meteen in die map zonder te navigeren.

---

## Verschil Git Bash vs cmd — kort

Voor 90% van wat je doet maakt het niet uit; `python`, `git`, `pip` werken identiek. Het verschil zit in shell-commando's eromheen.

| Wat | Git Bash | cmd |
|---|---|---|
| Map listen | `ls` | `dir` |
| Bestand bekijken | `cat file.txt` | `type file.txt` |
| Map wijzigen | `cd /d/Prive/...` | `cd D:\Prive\...` |
| venv activeren | `source .venv/Scripts/activate` | `.venv\Scripts\activate.bat` |
| Bestand verplaatsen | `mv` | `move` of `ren` |

**Default voor dit project: Git Bash.** Online tutorials, mijn instructies en Linux (later op de Pi) gebruiken allemaal Bash-syntax. Eén shell leren is makkelijker dan twee.
