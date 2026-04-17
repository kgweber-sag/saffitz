#!/usr/bin/env python3
"""
Stage 4: Extract structured recipe data from photographed/scanned recipe pages.

For each PDF in data/book_images/{book}/{book}-pages/, sends the page to Claude
and extracts ingredients, sub-recipe dependencies, header fields, do-ahead notes,
and yield. Results are cached per-file; only re-extracted if --force is given.

After extraction, merges results into data/derived/recipes_db.json:
  - Adds: ingredients, dependencies, do_ahead, yield_text, season,
          active_time_label, special_equipment
  - Overwrites difficulty / total_time_label from matrix when page confidence is high

Usage:
  # Test on 5 specific known-good files
  conda run -n saffitz python extract_recipe_pages.py --test

  # Test on N random files
  conda run -n saffitz python extract_recipe_pages.py --test --sample 10

  # Run everything (all cached + uncached)
  conda run -n saffitz python extract_recipe_pages.py

  # Force re-extraction of all files
  conda run -n saffitz python extract_recipe_pages.py --force

  # Only extract, don't merge into recipes_db yet
  conda run -n saffitz python extract_recipe_pages.py --no-merge
"""

import argparse
import base64
import json
import random
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

import anthropic
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).parent))
from persist import load_recipes_db, save_recipes_db, DATA

IMAGES = Path(__file__).parent / "data" / "book_images"
CACHE_DIR = DATA / "page_extractions"

# Files to use for --test mode (chosen to cover a range of cases)
TEST_FILES = [
    "dp/dp-pages/dp00050.pdf",   # Spiced Honey and Rye Cake (p.37)    — simple, no deps
    "dp/dp-pages/dp00030.pdf",   # Marcona Almond Cookies (p.127)       — Dairy-Free flag
    "dp/dp-pages/dp00001.pdf",   # Strawberry Cornmeal Layer Cake (p.177) — multi-section
    "dp/dp-pages/dp00075.pdf",   # Strawberry-Almond Bostock (p.232)    — 3 sub-recipe deps
    "dp/dp-pages/dp00107.pdf",   # Silkiest Chocolate Buttercream (p.359) — foundation recipe
]


# ---------------------------------------------------------------------------
# Pydantic schema
# ---------------------------------------------------------------------------

class Ingredient(BaseModel):
    name: str = Field(
        description="Ingredient name, normalized. For sub-recipes use the recipe title as printed."
    )
    quantity: float | None = Field(
        description="Numeric amount. Convert Unicode fractions: ½→0.5, ¼→0.25, ¾→0.75, ⅔→0.667, "
                    "⅓→0.333, 1½→1.5, 2¼→2.25, etc. Null if unquantified (e.g. 'Butter for the pan')."
    )
    quantity_unit: str | None = Field(
        description="Measurement unit: 'cup', 'tablespoon', 'teaspoon', 'ounce', 'pound', 'gram', "
                    "'recipe', 'loaf', 'stick', 'sprig', etc. Null for plain counts (eggs, items)."
    )
    weight_oz: float | None = Field(
        description="Weight in oz from a parenthetical like '(2.6 oz / 75g)'. Null if not present."
    )
    weight_g: float | None = Field(
        description="Weight in grams from a parenthetical like '(2.6 oz / 75g)' or '(454g)'. "
                    "Null if not present."
    )
    notes: str | None = Field(
        description="Inline preparation note or qualifier from the ingredient line itself: "
                    "'at room temperature', 'melted and cooled', 'hulled and thinly sliced', "
                    "'plus more for drizzling on top', etc. Do NOT include footnote text here."
    )
    footnote_number: int | None = Field(
        description="The circled footnote marker on this ingredient line (①→1, ②→2, ③→3, etc.). "
                    "Null if no marker is present."
    )
    is_sub_recipe: bool = Field(
        description="True when this ingredient is a reference to another recipe in the book "
                    "(bold text with a page reference like '(page 352)')."
    )
    sub_recipe_page: int | None = Field(
        description="The page number from the sub-recipe reference, e.g. 352 from '(page 352)'. "
                    "Null unless is_sub_recipe is true."
    )
    sub_recipe_fraction: float | None = Field(
        description="Only set when the quantity is expressed as a recipe fraction: "
                    "'½ recipe'→0.5, '1 recipe'→1.0, '1 loaf'→1.0 (whole recipe). "
                    "Null when quantity is by volume/weight (e.g. '1 cup Frangipane')."
    )


class IngredientSection(BaseModel):
    section_name: str | None = Field(
        description="Uppercase section header if present (e.g. 'CORNMEAL CAKE', 'ASSEMBLY', "
                    "'FOR THE FILLING'). Null if all ingredients are ungrouped."
    )
    ingredients: list[Ingredient]


class RecipePageExtraction(BaseModel):
    page_number: int = Field(
        description="The page number printed at the bottom of the page (e.g. 37, 177, 359)."
    )
    recipe_name: str = Field(description="Recipe title exactly as printed.")
    yield_text: str | None = Field(
        description="The yield line directly below the title, e.g. 'Serves 8', "
                    "'Makes 1 standard loaf', 'Makes about 24 small cookies'."
    )
    season: str | None = Field(
        description="From the header line. E.g. 'Spring / Summer', 'All', 'Fall / Winter'. "
                    "Null if obscured by glare."
    )
    active_time_label: str | None = Field(
        description="Active Time from header, e.g. '1 hour', '30 minutes'. "
                    "Use the base time if there are parenthetical qualifiers."
    )
    total_time_label: str | None = Field(
        description="Total Time from header, e.g. '2 hours 45 minutes'. "
                    "Use the primary value if conditional."
    )
    difficulty: int | None = Field(
        description="Difficulty as an integer (1–5). Use the primary/higher value if conditional."
    )
    difficulty_label: str | None = Field(
        description="Full difficulty label as printed, e.g. 'Very Easy', "
                    "'Moderate, but only because you have to split a cake layer'."
    )
    dietary_flags: list[str] = Field(
        description="Dietary labels from the header line, e.g. ['Dairy-Free', 'Gluten-Free', 'Vegan']."
    )
    special_equipment: str | None = Field(
        description="Contents of the Special Equipment field if present, else null."
    )
    ingredient_sections: list[IngredientSection] = Field(
        description="All ingredient sections. If there are no section headers, use a single "
                    "section with section_name: null."
    )
    footnotes: list[dict] = Field(
        description="Footnote entries from the bottom of the page. Each entry: "
                    "{\"number\": <int>, \"text\": <str>}. "
                    "Number corresponds to the circled markers (①=1, ②=2, etc.) on ingredients."
    )
    do_ahead: str | None = Field(
        description="The full DO AHEAD text from the bottom-left of the page."
    )
    confidence: str = Field(
        description="Overall extraction confidence: 'high' (everything readable), "
                    "'medium' (minor issues), 'low' (significant glare or cropping)."
    )
    issues: list[str] = Field(
        description="Specific problems encountered, e.g. ['header partially obscured by glare', "
                    "'ingredient list cropped at bottom']. Empty list if none."
    )


# ---------------------------------------------------------------------------
# Extraction prompt
# ---------------------------------------------------------------------------

SYSTEM = (
    "You are extracting structured data from a scanned cookbook recipe page. "
    "Return ONLY valid JSON matching the schema described. No markdown, no explanation."
)

EXTRACTION_PROMPT = f"""
Extract all structured recipe data from this page. Return a single JSON object matching
this schema exactly (field names, types, and nesting):

{RecipePageExtraction.model_json_schema()}

Key rules:
- page_number: read from the bold number at the very bottom center of the page.
- Convert Unicode fraction characters to decimals: ½→0.5, ¼→0.25, ¾→0.75, ⅔→0.667,
  ⅓→0.333, and combined forms like 1½→1.5, 2¼→2.25.
- Sub-recipe ingredients appear in BOLD with a "(page N)" reference in the ingredient list.
  Mark is_sub_recipe: true and set sub_recipe_page to that page number.
- If the header line is partially obscured, set affected fields to null and note the issue.
- difficulty: extract the integer only (e.g. "Difficulty: 3 (Moderate...)" → difficulty: 3).
- If ingredient sections have uppercase headers (CAKE, ASSEMBLY, FILLING, etc.), preserve them.
  Otherwise use a single section with section_name: null.
- footnote_number on an ingredient: look for a circled numeral (①②③…) on the ingredient line.
  Set footnote_number to the integer (①→1, ②→2, etc.); null if absent.
  The inline notes field is for preparation text on the same line ("at room temperature",
  "plus more for drizzling on top") — keep those in notes, not footnote_number.
- footnotes (top-level): collect all the numbered footnote entries from the bottom of the page
  (they appear in small text, distinct from DO AHEAD). Return as
  [{{"number": 1, "text": "..."}}, {{"number": 2, "text": "..."}}].
  Footnotes may be laid out in 2–3 columns; collect all of them.
- do_ahead: copy the full DO AHEAD text verbatim (it appears in small text at the bottom left).
- dietary_flags: extract labels that appear after "Difficulty: N" in the header
  (e.g. Dairy-Free, Gluten-Free, Vegan).

Return JSON only.
"""


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def cache_path(pdf_path: Path) -> Path:
    rel = pdf_path.relative_to(IMAGES)
    out = CACHE_DIR / rel.with_suffix(".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def extract_single(pdf_path: Path, client: anthropic.Anthropic) -> RecipePageExtraction:
    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()
    b64 = base64.standard_b64encode(pdf_bytes).decode()

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=3000,
        system=SYSTEM,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": b64,
                    },
                },
                {"type": "text", "text": EXTRACTION_PROMPT},
            ],
        }],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]

    return RecipePageExtraction.model_validate_json(raw)


def book_from_path(pdf_path: Path) -> str:
    """Derive book ID ('DP' or 'WFD') from the file path."""
    return pdf_path.relative_to(IMAGES).parts[0].upper()


def run_extractions(
    pdf_paths: list[Path],
    force: bool,
    client: anthropic.Anthropic,
) -> tuple[list[tuple[Path, RecipePageExtraction]], list[Path]]:
    results: list[tuple[Path, RecipePageExtraction]] = []
    failed: list[Path] = []

    for i, path in enumerate(pdf_paths):
        cp = cache_path(path)
        tag = f"[{i+1}/{len(pdf_paths)}] {path.name}"

        if not force and cp.exists():
            print(f"  {tag}  (cached)")
            result = RecipePageExtraction.model_validate_json(cp.read_text())
            results.append((path, result))
            continue

        print(f"  {tag}  extracting...", end=" ", flush=True)
        try:
            result = extract_single(path, client)
            cp.write_text(result.model_dump_json(indent=2))
            confidence_note = f"confidence={result.confidence}"
            if result.issues:
                confidence_note += f" issues={result.issues}"
            print(f"p.{result.page_number} {result.recipe_name!r}  {confidence_note}")
            results.append((path, result))
        except Exception as e:
            print(f"FAILED: {e}")
            failed.append(path)

    return results, failed


# ---------------------------------------------------------------------------
# Merge into recipes_db.json
# ---------------------------------------------------------------------------

def merge_into_db(extractions: list[tuple[Path, RecipePageExtraction]]) -> None:
    recipes = load_recipes_db()
    # Composite key: (book, page) — page numbers are NOT unique across books
    book_page_to_recipe = {
        (r["book"], r["page"]): r
        for r in recipes
        if r.get("book") and r.get("page")
    }
    # For resolving dependency names: prefer same-book match
    book_page_to_name = {
        (r["book"], r["page"]): r["canonical_name"]
        for r in recipes
        if r.get("book") and r.get("page")
    }

    matched = 0
    unmatched: list[tuple[str, int]] = []

    for pdf_path, ext in extractions:
        book = book_from_path(pdf_path)
        key = (book, ext.page_number)
        recipe = book_page_to_recipe.get(key)
        if recipe is None:
            unmatched.append(key)
            continue

        # Always-overwrite fields from page
        recipe["yield_text"]        = ext.yield_text
        recipe["season"]            = ext.season
        recipe["active_time_label"] = ext.active_time_label
        recipe["special_equipment"] = ext.special_equipment
        recipe["do_ahead"]          = ext.do_ahead
        recipe["dietary_flags"]     = ext.dietary_flags
        recipe["footnotes"]         = ext.footnotes

        # Ingredients (nested structure)
        recipe["ingredient_sections"] = [
            {
                "section_name": sec.section_name,
                "ingredients": [
                    {
                        "name":                ing.name,
                        "quantity":            ing.quantity,
                        "quantity_unit":       ing.quantity_unit,
                        "weight_oz":           ing.weight_oz,
                        "weight_g":            ing.weight_g,
                        "notes":               ing.notes,
                        "footnote_number":     ing.footnote_number,
                        "is_sub_recipe":       ing.is_sub_recipe,
                        "sub_recipe_page":     ing.sub_recipe_page,
                        "sub_recipe_fraction": ing.sub_recipe_fraction,
                    }
                    for ing in sec.ingredients
                ],
            }
            for sec in ext.ingredient_sections
        ]

        # Build dependencies list — resolve page → canonical_name within the same book
        deps = []
        for sec in ext.ingredient_sections:
            for ing in sec.ingredients:
                if ing.is_sub_recipe and ing.sub_recipe_page:
                    dep_name = book_page_to_name.get((book, ing.sub_recipe_page))
                    deps.append({
                        "recipe_page":         ing.sub_recipe_page,
                        "canonical_name":      dep_name,
                        "quantity":            ing.quantity,
                        "quantity_unit":       ing.quantity_unit,
                        "sub_recipe_fraction": ing.sub_recipe_fraction,
                    })
        recipe["dependencies"] = deps

        # Conditionally overwrite difficulty / total_time if page confidence is high
        if ext.confidence == "high":
            if ext.difficulty is not None and ext.difficulty != recipe.get("difficulty"):
                print(f"  [{book}] p.{ext.page_number}: difficulty "
                      f"{recipe.get('difficulty')} → {ext.difficulty} (page overrides matrix)")
                recipe["difficulty"]       = ext.difficulty
                recipe["difficulty_label"] = ext.difficulty_label
            if (ext.total_time_label is not None
                    and ext.total_time_label != recipe.get("total_time_label")):
                print(f"  [{book}] p.{ext.page_number}: total_time "
                      f"'{recipe.get('total_time_label')}' → '{ext.total_time_label}' (page overrides matrix)")
                recipe["total_time_label"] = ext.total_time_label

        matched += 1

    save_recipes_db(recipes)
    print(f"\nMerged {matched}/{len(extractions)} extractions into recipes_db.json")
    if unmatched:
        print(f"WARNING: {len(unmatched)} pages not matched in DB: {unmatched}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--test", action="store_true",
        help=f"Run on {len(TEST_FILES)} pre-selected test files",
    )
    parser.add_argument(
        "--sample", type=int, metavar="N",
        help="With --test: run on N random files instead of the pre-selected set",
    )
    parser.add_argument(
        "--book", choices=["dp", "wfd"],
        help="Only process pages for one book (default: all books found)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-extract even if cache exists",
    )
    parser.add_argument(
        "--no-merge", action="store_true",
        help="Skip merging into recipes_db.json (extraction only)",
    )
    args = parser.parse_args()

    client = anthropic.Anthropic()

    if args.test:
        if args.sample:
            books = [args.book] if args.book else ["dp", "wfd"]
            all_pdfs = []
            for book in books:
                pages_dir = IMAGES / book / f"{book}-pages"
                if pages_dir.exists():
                    all_pdfs.extend(sorted(pages_dir.glob("*.pdf")))
            pdf_paths = [random.choice(all_pdfs) for _ in range(min(args.sample, len(all_pdfs)))]
        else:
            pdf_paths = [IMAGES / rel for rel in TEST_FILES]
    else:
        books = [args.book] if args.book else ["dp", "wfd"]
        pdf_paths = []
        for book in books:
            pages_dir = IMAGES / book / f"{book}-pages"
            if pages_dir.exists():
                pdf_paths.extend(sorted(pages_dir.glob("*.pdf")))

    if not pdf_paths:
        print("No PDF files found.")
        return

    print(f"Processing {len(pdf_paths)} file(s)...")
    results, failed = run_extractions(pdf_paths, force=args.force, client=client)

    if failed:
        print(f"\n{len(failed)} file(s) failed extraction:")
        for p in failed:
            print(f"  {p.name}")

    low_confidence = [(p, r) for p, r in results if r.confidence != "high"]
    if low_confidence:
        print(f"\n{len(low_confidence)} result(s) with non-high confidence:")
        for p, r in low_confidence:
            print(f"  [{book_from_path(p)}] p.{r.page_number} {r.recipe_name!r}  "
                  f"confidence={r.confidence}  issues={r.issues}")

    if not args.no_merge and results:
        print("\nMerging into recipes_db.json...")
        merge_into_db(results)


if __name__ == "__main__":
    main()
