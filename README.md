# Compounds

Compounds is a daily chemistry puzzle. Sort 16 compounds into four groups of four by linking their names, structures, origins, or pharmacology. You get four mistakes and four distinct skeletal-structure inspections. Revisiting an inspected structure is free.

## Run locally

Put a puzzle batch at `data/puzzles/puzzles.json`, or set `COMPOUNDS_PUZZLE_BATCH` to its path. The batch is mounted read-only and stays outside Git and the Docker image. From the repository root with Docker running:

```sh
docker compose up --build -d --wait
```

Open [the game](http://127.0.0.1:8000/) or [the API docs](http://127.0.0.1:8000/docs). A link such as `?p=3` opens a specific puzzle.

If port 8000 is occupied, set `COMPOUNDS_PORT` before starting. In PowerShell, use `$env:COMPOUNDS_PORT = '8001'` and open [port 8001](http://127.0.0.1:8001/).

Compose builds Svelte, starts PostgreSQL, applies migrations, and publishes the mounted puzzle batch before starting FastAPI. Use `docker compose logs -f app` to inspect startup and `docker compose down` to stop the stack. The named database volume keeps attempts and puzzle versions across restarts and container replacement.

## How it works

- **Svelte + TypeScript** renders the board, tracks selection and shuffle order, and remembers the anonymous attempt ID in browser storage.
- **FastAPI** serves the frontend and API on one origin. It checks guesses and spends structure inspections on the server.
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
past results. Progress is tied to your browser and the published puzzle version.
The archive lists days before today in UTC.

## Puzzle stats and leaderboard

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
The leaderboard uses browser identity and has no account or anti-cheat system.

After finishing, **Share result** shares the puzzle number, result, and
color-coded guesses without revealing the answers. It uses the native share
dialog when available and falls back to copying the result.

Debug navigation is off by default. For native development only, set
`COMPOUNDS_LOCAL_DEBUG=1` before starting Uvicorn bound to localhost and open
`/?debug=1`. The API requires a loopback client and hostname. Keep the setting
unset on public deployments. Public routes reject future puzzles by UTC date.

## Generate and publish puzzles

Puzzle batches contain answers and structure SVGs. Generate them in a private checkout and keep them off public remotes.

```sh
python scripts/generate.py --start YYYY-MM-DD --days 60 --out /path/to/private/puzzles.json
python -m backend.publish /path/to/private/puzzles.json
```

For Docker, set `COMPOUNDS_PUZZLE_BATCH` to the private file before starting the stack. Outside Docker, run the publisher with the target database connection configured. It replaces the active schedule in a single transaction and retains historical versions used by attempts.

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

`scripts/audit.py` checks formula-derived masses against reference values. `scripts/verify.py` compares formulas with PubChem, and `scripts/scrape.py` collects candidate records. The SMILES merge script applies reviewed additions while preserving existing entries.

## License and data sources

Original project code is released under the [MIT License](LICENSE). The MIT grant applies to the code. Third-party data and assets retain their applicable terms.

The catalogue incorporates substance names and classifications from [PsychonautWiki](https://psychonautwiki.org/) contributors under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). Compounds selects and normalizes records, combines them with additional sources, and adds its own categorization. The maintenance tools also consult [PubChem](https://pubchem.ncbi.nlm.nih.gov/) and [NCI/CADD Chemical Identifier Resolver](https://cactus.nci.nih.gov/chemical/structure).
