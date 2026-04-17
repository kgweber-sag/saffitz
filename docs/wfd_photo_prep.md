# WFD — Photo Preparation Guide

## Directory layout and naming

Place single-page PDFs in:

```
data/book_images/wfd/wfd-pages/wfd00001.pdf
data/book_images/wfd/wfd-pages/wfd00002.pdf
...
```

Naming is sequential, zero-padded to 5 digits. Order doesn't matter — the page number
is read from the page itself. The book identity (`WFD`) is derived from the directory name.

To convert a JPEG to a single-page PDF:

```bash
convert photo.jpg -quality 95 wfd00001.pdf   # ImageMagick
```

## Running extraction

```bash
conda run -n saffitz python extract_recipe_pages.py --book wfd
```

Results are cached per-file in `data/derived/page_extractions/wfd/wfd-pages/` and merged
into `data/derived/recipes_db.json`. Safe to interrupt and resume — only uncached files
are sent to the API.

## Photography tips (from DP experience)

**The most common issue in DP was right-side header glare.** The header band (Active Time /
Total Time / Difficulty / Season) runs across the top of the page, and overhead or angled
light frequently washes out the right portion.

- Shoot from **directly above**, not at an angle.
- Use **diffuse or indirect light**: an overcast window, a white reflector card, or a bounce
  flash. Avoid a single overhead lamp pointing straight down.
- If you see glare, shift the book slightly or tilt your phone slightly and take a second
  shot — pick the cleaner one before converting to PDF.

**Page curl at the spine** is the second most common problem. The inner margin tends to
lean away from the camera, which can obscure a few characters.
- Press the page flat with a finger outside the ingredient area while shooting.
- A book cradle or a heavy object on the adjacent page also helps.
- Mild curl is handled well by the model; steep curl at the spine is harder to recover from.

**Two-page recipes**: you only need the first page (the one with the header and ingredient
list). If footnote text or the end of the ingredient list runs onto the second page, the
model flags it as an issue but still captures the ingredient markers. The DP batch showed
this is cosmetically imperfect but not a data-integrity problem.

**Resolution**: a standard phone camera shot (12MP+) at normal distance is fine. No scanner
required unless glare is severe. Avoid digital zoom.

## WFD-specific notes

- WFD difficulty tops out at **1–3** (by design, not a gap in the data).
- There are **102 WFD recipes** in the TOC.
- **6 are foundation recipes** (in the "Essential Recipes & Techniques" section) — photograph
  these too; they are the most-used dependency targets.
- One recipe is missing from the matrix extraction: **"Baked Semolina Pudding with
  Clementines & Bay Leaves" (p. 283)**. After extraction, add `difficulty` and
  `total_time_min` manually in `data/derived/recipes_db.json`.

## Quality check after extraction

Run this snippet to assess coverage:

```python
import json

recipes = json.load(open("data/derived/recipes_db.json"))
wfd = [r for r in recipes if r["book"] == "WFD"]
with_ing = [r for r in wfd if r.get("ingredient_sections")]
missing = [r for r in wfd if not r.get("ingredient_sections")]

print(f"{len(with_ing)}/{len(wfd)} WFD recipes have ingredients")
for r in missing:
    print(f"  p.{r['page']}  {r['canonical_name']}")
```

Then check confidence:

```python
from pathlib import Path
import json

cache = Path("data/derived/page_extractions/wfd/wfd-pages")
for f in sorted(cache.glob("*.json")):
    d = json.load(open(f))
    if d.get("confidence") != "high":
        print(f"{f.stem}  p.{d['page_number']}  [{d['confidence']}]  {d['recipe_name']}")
        for iss in d.get("issues", []):
            print(f"  - {iss}")
```
