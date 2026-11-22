# Setup and maintenance

[Project overview](README.md)

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

Open [the game](http://127.0.0.1:8000/) or [the API docs](http://127.0.0.1:8000/docs). A link such as `?p=3` opens a specific puzzle. Click **Aliases**, then click a highlighted tile to view its other names. The button is disabled when no visible tile has aliases. The footer shows the total number of substances in the catalogue.

If port 8000 is occupied, set `COMPOUNDS_PORT` before starting. Use `$env:COMPOUNDS_PORT = '8001'` in PowerShell or `export COMPOUNDS_PORT=8001` in Bash or Zsh, then open [port 8001](http://127.0.0.1:8001/).

Compose builds Svelte, starts PostgreSQL, applies migrations, and publishes the mounted puzzle batch before starting FastAPI. Use `docker compose logs -f app` to inspect startup and `docker compose down` to stop the stack. The named database volume keeps attempts and puzzle versions across restarts and container replacement.

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

## Configure Discord sign-in

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

## Generate and publish puzzles

Puzzle batches contain answers and structure SVGs. Generate them in a private checkout and keep them off public remotes.

```sh
python scripts/generate.py --start YYYY-MM-DD --days 60 --out /path/to/private/puzzles.json
python -m backend.publish /path/to/private/puzzles.json
```

For Docker, set `COMPOUNDS_PUZZLE_BATCH` to the private file before starting the stack. Outside Docker, run the publisher with the target database connection configured. It replaces the active schedule in a single transaction and retains historical versions used by attempts.

## Self-host

Run the Compose stack on your server. Point the domain at its public IP and configure a public HTTPS listener to serve the app. The default Compose port is loopback-only. A host reverse proxy can forward HTTPS traffic to `127.0.0.1:8000`, or the application server can handle TLS directly with a certificate and key. PostgreSQL stays on the Docker network. Back up the database volume or take PostgreSQL dumps if player progress matters.

## Local debug navigation

Debug navigation is off by default. For native development only, set
`COMPOUNDS_LOCAL_DEBUG=1` before starting Uvicorn bound to localhost and open
`/?debug=1`. The API requires a loopback client and hostname. Keep the setting
unset on public deployments. Public routes reject future puzzles by UTC date.

## Verify the catalogue

Run these commands from the repository root. Verification writes its report before the audit reads it.

```sh
python -m pip install rdkit
python scripts/verify.py
python scripts/audit.py
```

`verify.py` checks PubChem records by CID. A `MATCH` requires the exact InChIKey, a compatible formula, and a name confirmed by PubChem or a sourced entry in `data/catalogue/aliases.csv`. That file records evidence for identity review. The game draws its playable synonyms from `compounds.csv`.

PubChem responses are cached locally. Use `python scripts/verify.py --offline` to reuse cached responses. A CID without a cached response can still be checked in an online run.

`audit.py` checks the catalogue against reference masses, verifier results, and documented checks in `data/review/hand_checked.csv`. With RDKit installed it also compares catalogue formulas with cached SMILES. Eight records currently have independently documented checks and blank PubChem CIDs. They can pass the formula audit while still reporting `NO PUBCHEM DATA`.

