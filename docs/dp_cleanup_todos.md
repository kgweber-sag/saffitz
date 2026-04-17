# Dessert Person — Page Extraction Cleanup

Status after batch run of all 107 DP recipe PDFs (2026-04-17).

---

## Must-do: missing pages

Two DP recipes have no page photo and therefore no ingredient data:

| Page | Recipe |
|------|--------|
| p. 102 | Apricot and Cream Brioche Tart |
| p. 320 | Honey Almond Syrup |

Photograph these pages, convert to PDF, add to `data/book_images/dp/dp-pages/`, and re-run:

```bash
conda run -n saffitz python extract_recipe_pages.py --book dp
```

Only the new files will be processed (cached files are skipped).

---

## Must-do: duplicate PDF

`dp00016` and `dp00017` both extracted as **p. 79 Cranberry-Pomegranate Mousse Pie**.
One of these files should be a photo of a different page.

Steps:
1. Open `data/book_images/dp/dp-pages/dp00016.pdf` and `dp00017.pdf` and identify which is
   the correct p. 79 and which is the wrong one.
2. Delete or replace the wrong file with the correct page photo.
3. Delete the stale cache entry for the wrong file:
   `data/derived/page_extractions/dp/dp-pages/{filename}.json`
4. Re-run extraction.

---

## Orphan PDFs (not in DP TOC — fine to leave as-is)

These were photographed but are not main TOC entries. The pipeline ignores them during
merge (no TOC match), so they cause no harm.

| File | Page | What it is |
|------|------|-----------|
| `dp00045` | p. 165 | Concord Grape Jam — a component recipe embedded mid-book |
| `dp00093` | p. 251 | Bialys — a variation page with no header |
| `dp00086` | p. 334 | Flaky All-Butter Pie Dough — **continuation page** (low confidence, no ingredients) |

`dp00086` (p. 334) is the second page of the Flaky All-Butter Pie Dough recipe
(p. 333 is the main entry and was extracted correctly). The continuation page adds no
ingredient data and can be deleted if desired.

---

## Medium-confidence extractions (review optional)

36 extractions came back at `confidence=medium`, almost all due to glare on the right side
of the header band (time, difficulty, and season fields). The ingredient lists themselves
are high quality.

Where difficulty or time is missing from the extraction, the matrix data (Stage 2) fills
in the gap — these fields are only overwritten from page extraction when confidence is
`"high"`. So functionally, these recipes are complete.

To review or manually correct any entry, edit `data/derived/recipes_db.json` directly
(it's human-readable JSON). The pipeline will not overwrite manual edits unless you
re-run stage 4 with `--force`.

Key medium-confidence recipes to spot-check if you're curious:

- `dp00055` p. 193 — **Fruitcake**: ingredient list says "(ingredients continue)" — second
  page was not photographed.
- `dp00086` p. 334 — **Flaky All-Butter Pie Dough** (continuation): low confidence,
  no ingredient list on this page (see Orphan PDFs above).

---

## Optional: fix thread_mentions overcounting

Short/generic recipe names inflate `thread_mentions`. Example:
"Hot Chocolate with Marshmallows" matches on "hot chocolate" generically and gets 83
mentions, which is far too high.

Fix in `attach_reddit.py`: require a minimum word count for the matched variant, or
increase the fuzzy ratio threshold for short names. Then re-run stage 3:

```bash
conda run -n saffitz python run_pipeline.py --stage 3 --force
```

---

## Optional: add missing matrix entry

WFD recipe "Baked Semolina Pudding with Clementines & Bay Leaves" (WFD p. 283) was
missed by the Claude vision matrix extraction. It has no `difficulty` or `total_time_min`.

To add manually, edit `data/derived/recipes_db.json` and find the entry for this recipe,
then set `difficulty` and `total_time_min` from the book.

---

## WFD page extraction (not yet started)

See `docs/wfd_photo_prep.md` for photography tips and the full workflow.

Quick summary:
1. Photograph all 102 WFD recipe pages
2. Place PDFs in `data/book_images/wfd/wfd-pages/wfd00001.pdf` etc.
3. Run: `conda run -n saffitz python extract_recipe_pages.py --book wfd`
4. Run the quality check snippet in `docs/wfd_photo_prep.md` to assess coverage
