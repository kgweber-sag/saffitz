# Saffitz Recipe Browser

A personal "what shall I bake?" tool built around Claire Saffitz's two books:
**Dessert Person (DP)** and **What's for Dessert (WFD)**.

Recipes are enriched with difficulty/time data extracted from the books via Claude vision,
structured ingredient data extracted from recipe page photos, and community discussion
from r/DessertPerson.

---

## Prerequisites

- **conda** (Miniconda or Anaconda)
- **Anthropic API key** (for pipeline stages 2 and 4)
- Reddit `.zst` dump files for r/DessertPerson (see [Stage 1](#stage-1-reddit-graph) below)
- Photos of recipe pages (see [Photographing pages](#photographing-recipe-pages))

---

## Setup

```bash
# Create and activate the conda environment
conda create -n saffitz python=3.11
conda activate saffitz
pip install -r requirements.txt

# Add your Anthropic API key
echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env
```

---

## Pipeline overview

```
Stage 1  build_dessertperson_graph.py  →  data/derived/dessertperson_graph.pkl
Stage 2  build_recipe_db.py            →  data/derived/matrix_data.json
                                           data/derived/recipes_db.json
Stage 3  attach_reddit.py              →  updates recipes_db.json (Reddit enrichment)
Stage 4  extract_recipe_pages.py       →  updates recipes_db.json (ingredient data)
```

Each stage checkpoints its output. Re-running skips completed stages unless `--force` is passed.

```bash
# Run all stages (skips completed ones)
conda run -n saffitz python run_pipeline.py

# Force-rerun everything
conda run -n saffitz python run_pipeline.py --force

# Run a single stage
conda run -n saffitz python run_pipeline.py --stage 2

# Re-extract matrix data only (stage 2)
conda run -n saffitz python run_pipeline.py --force-matrix
```

Stage 4 (page extraction) is not yet integrated into `run_pipeline.py` and is run directly:

```bash
conda run -n saffitz python extract_recipe_pages.py          # all books
conda run -n saffitz python extract_recipe_pages.py --book dp
conda run -n saffitz python extract_recipe_pages.py --book wfd
conda run -n saffitz python extract_recipe_pages.py --force  # re-extract even if cached
```

---

## Stage 1: Reddit graph

Download the r/DessertPerson dump from the Arctic Shift project and place the files at:

```
data/reddit/subreddits25/DessertPerson_submissions.zst
data/reddit/subreddits25/DessertPerson_comments.zst
```

Then run stage 1 to parse them into a NetworkX graph:

```bash
conda run -n saffitz python run_pipeline.py --stage 1
```

---

## Stage 2: Recipe database

Requires photos of the difficulty/time matrix from each book placed at:

```
data/book_images/dp/dp_matrix.jpeg
data/book_images/wfd/wfd_matrix.jpeg
```

The matrix image will be rotated 90° counter-clockwise by the script
— the axes are X = Total Time, Y = Difficulty after rotation.

Run:

```bash
conda run -n saffitz python run_pipeline.py --stage 2
```

This uses Claude vision to extract difficulty and time for all 207 recipes (105 DP + 102 WFD)
from the matrix image, then combines with the hard-coded table of contents to produce
`recipes_db.json`.

---

## Stage 3: Reddit enrichment

Fuzzy-matches recipe names against Reddit submission titles and mines "opinion" threads
(best-of, recommendation posts) for recipe mentions.

```bash
conda run -n saffitz python run_pipeline.py --stage 3
```

---

## Stage 4: Recipe page extraction

Extracts structured ingredient data (quantities, weights, sub-recipe links, footnotes,
do-ahead notes, yield, season) from photos of recipe pages using Claude.

### Photographing recipe pages

Place photos in:

```
data/book_images/dp/dp-pages/dp00001.pdf
data/book_images/dp/dp-pages/dp00002.pdf
...
data/book_images/wfd/wfd-pages/wfd00001.pdf
...
```

Naming is sequential within each book (zero-padded to 5 digits). Order doesn't matter —
the page number is read from the page itself. JPEGs must be converted to single-page PDFs
before processing:

```bash
# Convert a JPEG to PDF (ImageMagick)
convert photo.jpg -quality 95 dp00001.pdf
```

See `docs/wfd_photo_prep.md` for detailed photography tips.

Run extraction (cached per-file, safe to interrupt and resume):

```bash
conda run -n saffitz python extract_recipe_pages.py --book dp
conda run -n saffitz python extract_recipe_pages.py --book wfd
```

---

## Running the app

```bash
conda run -n saffitz streamlit run app.py
```

Open http://localhost:8501 in your browser.

Personal data (ratings and "made it" flags) is stored in `data/personal.json` and is
never overwritten by the pipeline.

---

## Adding a new book

1. Add the book's TOC and recipe list to the `TOC` dict in `build_recipe_db.py`
2. Add a matrix photo at `data/book_images/{book_id}/{book_id}_matrix.jpeg`
3. Add page photos at `data/book_images/{book_id}/{book_id}-pages/`
4. Re-run stage 2 with `--force-matrix` and stage 4 with `--book {book_id}`

The book ID must be a short uppercase string (e.g. `DP`, `WFD`). It is derived from the
directory name and used as the composite key alongside page number throughout the pipeline.

---

## Project structure

```
app.py                        Streamlit UI
run_pipeline.py               Orchestrates stages 1–3
build_dessertperson_graph.py  Stage 1: parse Reddit .zst dumps
build_recipe_db.py            Stage 2: TOC + matrix → recipes_db.json
attach_reddit.py              Stage 3: Reddit enrichment
extract_recipe_pages.py       Stage 4: ingredient extraction from page photos
persist.py                    Shared load/save helpers

data/
  reddit/                     Raw Reddit dump files (.zst) — not in git
  book_images/                Book matrix and TOC images; recipe page PDFs
  derived/                    Generated artifacts (checkpointed)
    recipes_db.json           Main artifact — 207 recipes with all metadata
    personal.json             Your ratings and "made it" flags
    dessertperson_graph.pkl   Reddit graph (expensive, ~minutes to rebuild)
    matrix_data.json          Cached Claude vision output for matrix
    page_extractions/         Per-page extraction cache (one JSON per PDF)

docs/                         Additional documentation
_retired/                     Obsolete scripts from the Reddit-first approach
arctic_shift/                 Submodule: Arctic Shift Reddit parsing utilities
```

---

## Data model

Each recipe in `recipes_db.json` has:

| Field | Source | Notes |
|---|---|---|
| `canonical_name` | TOC | Ground truth name |
| `book` | TOC | `"DP"` or `"WFD"` |
| `book_section` | TOC | Chapter/section name |
| `page` | TOC | Used as part of composite key with `book` |
| `difficulty` | Matrix | 1–5 (DP) or 1–3 (WFD) |
| `total_time_min` | Matrix | Minutes, **excludes sub-recipe time** |
| `total_time_label` | Matrix / page | Human-readable |
| `is_foundation` | TOC section | True for 22 recipes |
| `ingredient_sections` | Page extraction | List of sections with ingredients |
| `dependencies` | Page extraction | Sub-recipes with page references |
| `footnotes` | Page extraction | Numbered footnote texts |
| `do_ahead` | Page extraction | Do-ahead note if present |
| `season` | Page extraction | e.g. `"Fall / Winter"` |
| `submission_count` | Reddit | Matched submission threads |
| `thread_mentions` | Reddit | Mentions in opinion/recommendation threads |
| `top_comments` | Reddit | Up to 20 highest-scored comments |
