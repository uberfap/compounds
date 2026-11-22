# Compounds

Compounds is a daily chemistry puzzle. Sort 16 compounds into four groups of four by linking their names, structures, origins, or pharmacology. You get four mistakes and four distinct skeletal-structure inspections. You can view the aliases of any unsolved tile that has them.

> For installation, local development, and deployment, see [Setup and maintenance](SETUP.md).

## How it works

- **Svelte + TypeScript** renders the board, tracks selection and shuffle order, and remembers the anonymous attempt ID in browser storage.
- **FastAPI** serves the frontend and API on one origin. It checks guesses and tracks structure inspections and alias reveals on the server.
- **PostgreSQL + Alembic** store attempts and versioned boards. A changed board starts a separate playthrough, and existing attempts remain tied to their original versions.
- **Python** generates dated puzzles from the compound catalogue. Optional RDKit support renders skeletal structures during generation.

The browser uses its local date to pick a puzzle. Dates outside the published schedule show the nearest endpoint.

## Puzzle archive

Open **Archive** to browse earlier published puzzles by date and resume or view
past results. Progress follows the published puzzle version and your browser
or Discord account.
The archive lists days before today in UTC.

## Theme, help, and Discord sign-in

The theme icon cycles through System, Light, and Dark and remembers the setting
in the browser. **How to play** covers guessing, difficulty,
and structure inspections with an example.

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

A puzzle counts as played after your first guess, structure inspection, or alias reveal.
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

## Puzzle generation and clue balance

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

undefined## Repository layout

| Path | Contents |
| --- | --- |
| `frontend/src/` | API-backed Svelte game |
| `backend/` | FastAPI, PostgreSQL access, batch publisher, and migrations |
| `scripts/` | Generator and catalogue maintenance tools |
| `data/catalogue/` | Compound catalogue and SMILES cache |
| `data/review/` | Reference values and curated review data |
| `data/puzzles/` | Local puzzle batches, excluded from Git |

## Repository layout

- `frontend/` contains the Svelte interface.
- `backend/` contains the FastAPI API, migrations, and publisher.
- `scripts/` contains generation and audit tools.
- `data/catalogue/` contains compound data and source-backed clues.

### Compound identifiers and aliases

`data/catalogue/identities.csv` has one row per name in `compounds.csv`. It uses the same pipe delimiter. The `inchi_key` column holds the Standard InChIKey of the cached parent structure, with counterions removed and assigned stereochemistry retained. The `pubchem_cid` column holds the numeric PubChem Compound ID when the published InChIKey exactly matches the local key.

`aliases` is a semicolon-separated list of reviewed alternative names. `alias_source` records the reference for those names. A blank field means the identifier or aliases have yet to be established. The generator uses `compounds.csv` for display names and puzzle rules. The API reads aliases from that file for synonym hints, including on older boards whose tile names now appear as aliases. Alias reveals are unlimited.

### Target curation

The `target_detail` column records supplied pharmacology reference leads, including assay endpoints, reported ranges, inference labels, and notes where available. The generator reads the canonical `target` tokens for predicates. Label-backed clinical uses are in `documented_uses.csv`.


The target column uses distinct tags for established molecular actions. Keep broader tags where they remain accurate and useful for puzzle grouping. Mark inferred fields with the catalogue's existing annotations so the generator excludes them from clue rules.

The current target review uses sources for [caffeine](https://pmc.ncbi.nlm.nih.gov/articles/PMC6247400/), [varenicline](https://www.accessdata.fda.gov/drugsatfda_docs/label/2025/021928Orig1s051lbl.pdf), [galantamine](https://www.dailymed.nlm.nih.gov/dailymed/fda/fdaDrugXsl.cfm?setid=10497273-d907-0514-e063-6294a90a1362&type=display), and [gabapentin and pregabalin](https://pmc.ncbi.nlm.nih.gov/articles/PMC7265598/).

The follow-up review cites [buspirone](https://www.medsafe.govt.nz/profs/datasheet/b/buspironeviatristab.pdf), [mirtazapine](https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=979eec8b-1bf4-466b-812e-85a111ce1cb5), [ketamine](https://www.accessdata.fda.gov/drugsatfda_docs/label/2026/016812s057lbl.pdf), and [memantine](https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=b9f27baf-aa2a-443a-9ef5-e002d23407ba). Documented uses apply to the cited formulation. The anesthesia clue includes labeled use during induction, including as an adjunct. The narcolepsy clue includes sodium oxybate under its parent catalogue entry, GHB. Use entries require a source before they can enter the puzzle generator.

The NMDA receptor channel-blocker clue uses [documented pore blockade](https://pmc.ncbi.nlm.nih.gov/articles/PMC6080888/) for Ketamine, PCP, Dizocilpine, and Memantine. Smoking-cessation entries use product labeling from the US and UK.

### Assay-specific clues

The generator includes three evidence-backed groups: DAT versus SERT uptake inhibition in rat synaptosomes, partial 5-HT2A agonism in a human receptor calcium-response assay, and potentiation of the alpha1-beta2-gamma1 GABA-A receptor in oocytes. Each target token names its assay context. Individual observations and citations are recorded in `target_detail`.

## License and data sources

The game footer opens the archive, puzzle statistics, a privacy summary, and
license and data credits.

Original project code is released under the [MIT License](LICENSE). The MIT grant applies to the code. Third-party data and assets retain their applicable terms.

The catalogue incorporates substance names and classifications from [PsychonautWiki](https://psychonautwiki.org/) contributors under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). Compounds selects and normalizes records, combines them with additional sources, and adds its own categorization. The maintenance tools also consult [PubChem](https://pubchem.ncbi.nlm.nih.gov/) and [NCI/CADD Chemical Identifier Resolver](https://cactus.nci.nih.gov/chemical/structure).
