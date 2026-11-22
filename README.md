# Compounds

Compounds is a daily chemistry puzzle. Sort 16 compounds into four groups of four by linking their names, structures, origins, or pharmacology. You get four mistakes and four distinct skeletal-structure inspections. A separate allowance unlocks synonyms for up to four tiles that have them. Revisiting an unlocked hint is free.

## Run locally

Put a puzzle batch at `data/puzzles/puzzles.json`, or set `COMPOUNDS_PUZZLE_BATCH` to its path. The batch is mounted read-only and stays outside Git and the Docker image. From the repository root with Docker running:

```sh
docker compose up --build -d --wait
```

For a fresh checkout without a private batch, generate a small local schedule first. Install Python 3 and make sure Docker Compose is available. Start Docker Desktop on Windows or macOS, or start the Docker Engine on Linux.

### Windows (PowerShell)

```powershell
python -m pip install rdkit
New-Item -ItemType Directory -Force data/puzzles | Out-Null
python scripts/generate.py --start ((Get-Date).ToUniversalTime().ToString('yyyy-MM-dd')) --days 3 --out data/puzzles/puzzles.json
docker compose up --build -d --wait
```

### Linux and macOS (Bash or Zsh)

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install rdkit
mkdir -p data/puzzles
python scripts/generate.py --start "$(date -u +%F)" --days 3 --out data/puzzles/puzzles.json
docker compose up --build -d --wait
```

Use an existing private batch to replay its published boards. Generate a new batch to try the updated catalogue names and structures in new puzzles.

Open [the game](http://127.0.0.1:8000/) or [the API docs](http://127.0.0.1:8000/docs). A link such as `?p=3` opens a specific puzzle. To try synonym hints, click **Synonyms**, then click a tile with aliases. The first reveal spends one of four synonym points. Reopening the same hint is free.

If port 8000 is occupied, set `COMPOUNDS_PORT` before starting. Use `$env:COMPOUNDS_PORT = '8001'` in PowerShell or `export COMPOUNDS_PORT=8001` in Bash or Zsh, then open [port 8001](http://127.0.0.1:8001/).

Compose builds Svelte, starts PostgreSQL, applies migrations, and publishes the mounted puzzle batch before starting FastAPI. Use `docker compose logs -f app` to inspect startup and `docker compose down` to stop the stack. The named database volume keeps attempts and puzzle versions across restarts and container replacement.

## How it works

- **Svelte + TypeScript** renders the board, tracks selection and shuffle order, and remembers the anonymous attempt ID in browser storage.
- **FastAPI** serves the frontend and API on one origin. It checks guesses and tracks structure and synonym hint allowances on the server.
- **PostgreSQL + Alembic** store attempts and versioned boards. A changed board starts a separate playthrough, and existing attempts remain tied to their original versions.
- **Python** generates dated puzzles from the compound catalogue. Optional RDKit support renders skeletal structures during generation.

The browser uses its local date to pick a puzzle. Dates outside the published schedule show the nearest endpoint.

## Develop

For API development outside Docker, create a PostgreSQL database and set `COMPOUNDS_DATABASE_URL` to its connection URL. From the repository root, in an active Python environment:

```sh
python -m pip install -r backend/requirements.txt
python -m alembic upgrade head
python -m backend.publish
python -m uvicorn backend.app:app --reload
```

In a second terminal:

```sh
cd frontend
npm ci
npm run dev
```

[Vite](http://localhost:5173/) proxies API requests to port 8000. Run `npm run check` for type checking and `npm run build` for a production bundle. FastAPI serves that bundle when `frontend/dist/index.html` exists at startup.

## Puzzle archive

Open **Archive** to browse earlier published puzzles by date and resume or view
past results. Progress follows the published puzzle version and your browser
or Discord account.
The archive lists days before today in UTC.

## Theme, help, and Discord sign-in

The theme icon cycles through System, Light, and Dark and remembers the setting
in the browser. **How to play** covers guessing, difficulty,
and structure inspections with an example.

Guest play works by default. To enable **Sign in with Discord**, create an
application in the [Discord Developer Portal](https://discord.com/developers/applications)
and add your exact callback URL under **OAuth2 → Redirects**. For Compose,
set these values in your local `.env` or pass them through your deployment
environment. For a native backend, export them before starting Uvicorn.

```dotenv
COMPOUNDS_DISCORD_CLIENT_ID=your-application-id
COMPOUNDS_DISCORD_CLIENT_SECRET=your-client-secret
COMPOUNDS_DISCORD_REDIRECT_URI=https://compounds.example.com/api/auth/discord/callback
```

Use the app's public HTTPS origin for production. For local testing, use the
exact loopback origin and port you open in your browser, such as
`http://127.0.0.1:8002/api/auth/discord/callback`. Recreate the app after changing
the environment. Compose applies the account migration automatically.

Discord sign-in requests only `identify`. Compounds stores the Discord user
ID and display name and keeps its own 30-day session. Discord tokens are used
only during sign-in. The browser's guest progress linked by its anonymous ID
moves into the account on sign-in. If the account already has an attempt for
that puzzle version, it keeps its existing attempt. Leaderboard names stay anonymous.

## Puzzle stats and leaderboard

Open **My stats** in the header to see your totals, mistake distribution,
daily streaks, and 30 recent published puzzles. Stats use the currently
published version of each puzzle. Signed-in players see the same submitted
progress across devices. Guest stats follow the browser identity.

A puzzle counts as played after your first guess, structure inspection, or synonym unlock.
Win rate uses finished puzzles. Archive solves count toward the overall
totals, while streaks require consecutive release days solved on the same
UTC date. A streak through yesterday stays current until you lose today's
puzzle or today's UTC day ends.

Open **Puzzle statistics and leaderboard** below the board while playing or after
finishing. It shows how many attempts started, finished, and solved the puzzle.
Statistics refresh after each submitted guess. Your ranked entry is marked “you.”

Each puzzle version has its own metrics. An attempt counts as started after its
first guess. Solve rate, average mistakes, average inspections, and per-group
solve rates use finished attempts. The leaderboard displays up to ten anonymous
solves, sorted by fewest mistakes, then fewest inspections, then earliest finish.

The app saves selected tiles and shuffle order in the browser and records
submitted progress in PostgreSQL. A browser ID lets the API reuse an attempt
for the same puzzle version if the browser loses its saved attempt reference.
The leaderboard uses anonymous labels for guest and signed-in players.

After finishing, **Share result** shares the puzzle number, result, and
color-coded guesses without revealing the answers. It uses the native share
dialog when available and falls back to copying the result.

Debug navigation is off by default. For native development only, set
`COMPOUNDS_LOCAL_DEBUG=1` before starting Uvicorn bound to localhost and open
`/?debug=1`. The API requires a loopback client and hostname. Keep the setting
unset on public deployments. Public routes reject future puzzles by UTC date.

## Generate and publish puzzles

The generator skips placeholder catalogue tags such as `unknown` and
`unclassified` when building groups. With RDKit installed, it also matches
functional groups and ring motifs in the cached molecular structures. Without
RDKit, generation still uses names, formulas, and catalogue tags.

Generated batches include private balance diagnostics. Each group records its
rule ID and raw score before clamping. Each puzzle records its relaxation level,
near-miss count, and the number of distinct unintended quartets matched by rules
that hit exactly four tiles on that board. Selected rule pairs include their
catalogue support sizes and intersection size, which give directional
containment and Jaccard overlap.

The near-miss cap counts qualifying unchosen mass bands together. The solution
check still considers each band independently. New boards allow at most two
distinct unintended quartets matched by rules that hit exactly four tiles.

Puzzle batches contain answers and structure SVGs. Generate them in a private checkout and keep them off public remotes.

```sh
python scripts/generate.py --start YYYY-MM-DD --days 60 --out /path/to/private/puzzles.json
python -m backend.publish /path/to/private/puzzles.json
```

For Docker, set `COMPOUNDS_PUZZLE_BATCH` to the private file before starting the stack. Outside Docker, run the publisher with the target database connection configured. It replaces the active schedule in a single transaction and retains historical versions used by attempts.

### Purple clue rotation

The generator alternates requested Purple families across the batch: pharmacology, formula, documented use, structure, prodrug, pharmacology, and active metabolite. Each request gets a bounded search. If the search cannot build a valid board, the generator falls back to the normal search and records the fallback in diagnostics. The final Purple score band, unique-solution check, and two-decoy cap still apply.

Dual-target clues require both catalogue tags, at least five matching compounds across at least three scaffold classes, and a meaningful difference from each individual target. The existing target loader excludes records whose target fields are marked as inferred. Documented uses, prodrugs, and active metabolites share `documented_uses.csv` beside the catalogue. Each row lists the catalogue name, player-facing clue, source URL, and family. A missing or blank family defaults to `documented_use`. Recognized families are `documented_use`, `prodrug`, and `metabolite`. Use the Indications section of a DailyMed label for documented uses, and cite a prescribing label or peer-reviewed study for metabolic relationships between catalogue compounds. The four-member minimum applies to all three families.

Origin and pharmacological class conjunctions use natural and semisynthetic compounds. Action-type clues use the curated target tags and distinguish reuptake inhibition from other inhibition. These two derived families are capped at Blue. Deduplication drops the broad positive allosteric modulator clue because its catalogue matches overlap the existing GABA-A PAM clue above the deduplication threshold.

A private 30-board test used seeds 1 and 2, with 15 boards per seed. Purple contained 14 formula, 3 structure, 7 dual-target, and 6 documented-use clues. The reserved search succeeded on 21 boards. All boards had four difficulty tiers, one solution, at most two exact-four decoys, and zero constraint relaxation. The documented-use clues used the same four compounds, and the κ-opioid agonist clue did not appear.

#### Documented-use cooldown and rule tracing

Each sourced quartet can appear once per generated batch across documented-use, prodrug, and active-metabolite clues. If the rotation has exhausted the available quartets, that slot falls back to the normal search.

Use `--trace-rule "target:κ-opioid agonist"` when generating a private batch to print counters for predicate draws, board variants, scores, and acceptance checks. Draw counts refer to sets of rules. Later counts refer to board variants. The near-miss, partition, and decoy checks run on variants that improve the current difficulty cost for their rule set. An omitted counter means zero.

The cooldown test generated 40 boards across seeds 1 and 2. For a matched 30-board comparison, documented-use Purple clues fell from 6 to 2, dual-target clues rose from 7 to 10, formula clues fell from 14 to 13, and structure clues rose from 3 to 5. Rotation requests matched on 22 of 30 boards, up from 21. Across all 40 boards the generator used 21 distinct Purple rules and matched 27 rotation requests. Every board had one solution, all four tiers, at most two exact-four decoys, and no relaxation.

The κ-opioid rule appeared in 2,111 draws. It scored Purple on 1,906 scored variants, and 364 also passed the full board difficulty check. Fourteen reached the partition solver and each failed its uniqueness check. No generated Purple group used the rule.

#### Seed 1 comparison

The 60-board run starting 2026-11-22 produced 34 formula, 8 structure, 7 dual-target, 4 documented-use, 4 prodrug, 2 metabolite, and 1 single-target Purple clue. Blue used six origin/class and six action-type clues. The five documented-use appearances used distinct quartets. All boards passed the solution and decoy checks without relaxing constraints. The unrestricted search now tries up to 16,000 rule sets before it relaxes constraints.

### Purple clue balance benchmark

A private 60-board sample used seed 1 and a start date of 2026-11-22. The trial multiplied formula-rule sampling weights in the Purple candidate pool by 0.4. Final difficulty bands and solution checks were the same in both runs.

| Purple clues | Current generator | Trial |
| --- | ---: | ---: |
| Formula | 46 | 44 |
| Structure | 12 | 13 |
| Pharmacology targets | 2 | 3 |
| κ-opioid agonists | 0 | 0 |
| Distinct Purple rules | 22 | 24 |
| Boards requiring relaxation | 0 | 1 |

Every board had one solution and at most two exact-four decoys. The trial produced 79 exact-four decoys across the schedule, up from 67. The generator retains the current weights.

## Self-host

Run the Compose stack on your server. Point the domain at its public IP and configure a public HTTPS listener to serve the app. The default Compose port is loopback-only. A host reverse proxy can forward HTTPS traffic to `127.0.0.1:8000`, or the application server can handle TLS directly with a certificate and key. PostgreSQL stays on the Docker network. Back up the database volume or take PostgreSQL dumps if player progress matters.

## Repository layout

| Path | Contents |
| --- | --- |
| `frontend/src/` | API-backed Svelte game |
| `backend/` | FastAPI, PostgreSQL access, batch publisher, and migrations |
| `scripts/` | Generator and catalogue maintenance tools |
| `data/catalogue/` | Compound catalogue and SMILES cache |
| `data/review/` | Reference values and curated review data |
| `data/puzzles/` | Local puzzle batches, excluded from Git |

### Compound identifiers and aliases

`data/catalogue/identities.csv` has one row per name in `compounds.csv`. It uses the same pipe delimiter. The `inchi_key` column holds the Standard InChIKey of the cached parent structure, with counterions removed and assigned stereochemistry retained. The `pubchem_cid` column holds the numeric PubChem Compound ID when the published InChIKey exactly matches the local key.

`aliases` is a semicolon-separated list of reviewed alternative names. `alias_source` records the reference for those names. A blank field means the identifier or aliases have yet to be established. The generator uses `compounds.csv` for display names and puzzle rules. The API reads aliases from that file for synonym hints, including on older boards whose tile names now appear as aliases. Existing attempt records start with four synonym points. Deploy the new application build to enable hints on published puzzles.

### Verify the catalogue

Run these commands from the repository root. Verification writes its report before the audit reads it.

```sh
python -m pip install rdkit
python scripts/verify.py
python scripts/audit.py
```

`verify.py` checks PubChem records by CID. A `MATCH` requires the exact InChIKey, a compatible formula, and a name confirmed by PubChem or a sourced entry in `data/catalogue/aliases.csv`. That file records evidence for identity review. The game draws its playable synonyms from `compounds.csv`.

PubChem responses are cached locally. Use `python scripts/verify.py --offline` to reuse cached responses. A CID without a cached response can still be checked in an online run.

`audit.py` checks the catalogue against reference masses, verifier results, and documented checks in `data/review/hand_checked.csv`. With RDKit installed it also compares catalogue formulas with cached SMILES. Eight records currently have independently documented checks and blank PubChem CIDs. They can pass the formula audit while still reporting `NO PUBCHEM DATA`.

## License and data sources

The game footer opens the archive, puzzle statistics, a privacy summary, and
license and data credits.

Original project code is released under the [MIT License](LICENSE). The MIT grant applies to the code. Third-party data and assets retain their applicable terms.

The catalogue incorporates substance names and classifications from [PsychonautWiki](https://psychonautwiki.org/) contributors under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). Compounds selects and normalizes records, combines them with additional sources, and adds its own categorization. The maintenance tools also consult [PubChem](https://pubchem.ncbi.nlm.nih.gov/) and [NCI/CADD Chemical Identifier Resolver](https://cactus.nci.nih.gov/chemical/structure).
