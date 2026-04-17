#!/usr/bin/env python3
"""
Stage 2: Build the canonical recipe database from book metadata.

Sources:
  - TOC data: hard-coded from photographed book contents pages
  - Matrix data: extracted via Claude vision from matrix images
    (cached in data/derived/matrix_data.json to avoid repeated API calls)

Outputs:
  data/derived/matrix_data.json  -- raw LLM extraction per book (cached)
  data/derived/recipes_db.json   -- canonical recipe list with book metadata

Each recipe record:
  canonical_name    -- exact title from book TOC
  book              -- "DP" or "WFD"
  book_section      -- chapter name
  page              -- page number
  difficulty        -- integer 1-5 (DP) or 1-3 (WFD); None if not in matrix
  total_time_min    -- total time in minutes (not counting sub-recipes); None if missing
  total_time_label  -- raw label from matrix (e.g. "2 HOURS")
  is_foundation     -- True if this is a foundation/building-block recipe

Reddit fields (submission_count, thread_mentions, submission_ids, top_comments)
are added later by attach_reddit.py.

Usage:
  conda run -n saffitz python build_recipe_db.py
  conda run -n saffitz python build_recipe_db.py --force-matrix
"""

import argparse
import base64
import io
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

import anthropic
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from persist import (
    DATA, MATRIX_DATA_PATH, RECIPES_DB_PATH,
    save_matrix_data, load_matrix_data, save_recipes_db,
)

IMAGES = Path(__file__).parent / "data" / "book_images"

# ---------------------------------------------------------------------------
# TOC data — hard-coded from book images
# {book: {section: [(recipe_name, page)]}}
# ---------------------------------------------------------------------------

TOC: dict[str, dict[str, list[tuple[str, int]]]] = {
    "DP": {
        "Loaf Cakes and Single-Layer Cakes": [
            ("Spiced Honey and Rye Cake", 37),
            ("Almond Butter Banana Bread", 38),
            ("Poppy Seed Almond Cake", 41),
            ("Kabocha Turmeric Tea Cake", 42),
            ("Spiced Persimmon Cake", 45),
            ("Mascarpone Cake with Red Wine Prunes", 47),
            ("Pear Chestnut Cake", 51),
            ("Double-Apple Crumble Cake", 53),
            ("Rhubarb Cake", 56),
            ("Rice Pudding Cake with Mango Caramel", 59),
            ("Ricotta Cake with Kumquat Marmalade", 61),
            ("Flourless Chocolate Wave Cake", 65),
            ("Blood Orange and Olive Oil Upside-Down Cake", 67),
            ("Goat Cheese Cake with Honey and Figs", 71),
            ("Pineapple and Pecan Upside-Down Cake", 73),
        ],
        "Pies and Tarts": [
            ("Cranberry-Pomegranate Mousse Pie", 79),
            ("Plum Galette with Polenta and Pistachios", 81),
            ("Pistachio Linzer Tart", 85),
            ("Salty Nut Tart with Rosemary", 87),
            ("Apple Tart", 91),
            ("Caramelized Honey Pumpkin Pie", 93),
            ("Apple and Concord Grape Crumble Pie", 97),
            ("Blackberry Caramel Tart", 99),
            ("Apricot and Cream Brioche Tart", 102),
            ("Meyer Lemon Tart", 104),
            ("Foolproof Tarte Tatin", 107),
            ("Sour Cherry Pie", 111),
            ("Quince and Almond Tart with Rosé", 115),
            ("Blueberry Slab Pie", 119),
            ("Peach Melba Tart", 121),
        ],
        "Bars and Cookies": [
            ("Marcona Almond Cookies", 127),
            ("Salted Halvah Blondies", 128),
            ("Brown Butter and Sage Sablés", 131),
            ("Chocolate Chip Cookies", 133),
            ("Cinnamon Sugar Palmiers", 136),
            ('Malted "Forever" Brownies', 139),
            ("Pistachio Pinwheels", 141),
            ("Chewy Molasses Spice Cookies", 144),
            ("Aunt Rose's Mondel Bread", 147),
            ("Coconut Thumbprints", 149),
            ("Oat and Pecan Brittle Cookies", 151),
            ("Minty Lime Bars", 155),
            ("Thrice-Baked Rye Cookies", 157),
            ("Earl Grey and Apricot Hamantaschen", 159),
            ("Peanut Butter and Concord Grape Sandwich Cookies", 163),
        ],
        "Layer Cakes and Fancy Desserts": [
            ("Classic Birthday Cake", 169),
            ("Confetti Cake", 171),
            ("Carrot and Pecan Cake", 175),
            ("Strawberry Cornmeal Layer Cake", 177),
            ("Chocolate Buttermilk Cake", 181),
            ("Chocolate-Hazelnut Galette des Rois", 183),
            ("Strawberry-Rhubarb Pavlovas with Rosé", 187),
            ("Tarte Tropézienne", 189),
            ("Fruitcake", 193),
            ("Gâteau Basque", 197),
            ("All Coconut Cake", 201),
            ("Black Sesame Paris-Brest", 203),
            ("Preserved Lemon Meringue Cake", 206),
            ("Croquembouche", 211),
        ],
        "Breakfast and Brunch": [
            ("Seedy Maple Breakfast Muffins", 216),
            ("Coffee Coffee Cake", 219),
            ("Buckwheat Blueberry Skillet Pancake", 221),
            ("Brown Butter Corn Muffins", 224),
            ("Classic English Muffins", 227),
            ("Brioche Twists with Coriander Sugar", 229),
            ("Strawberry-Almond Bostock", 232),
            ("Babkallah", 235),
            ("Speculoos Babka", 239),
            ("St. Louis Gooey Butter Cake", 243),
            ("Walnut-Maple Buns", 245),
            ("A Little Bit of Everything Bagels", 249),
            ("Spelt Croissants", 253),
            ("Kouign-amann", 257),
            ("Cherry Cream Cheese Danishes", 263),
        ],
        "Breads and Savory Baking": [
            ("Loaded Corn Bread", 268),
            ("Miso Buttermilk Biscuits", 271),
            ("Tomato Tart with Spices and Herby Feta", 273),
            ("Gougères", 277),
            ("Caramelized Endive Galette", 278),
            ("Crispy Mushroom Galette", 280),
            ("Creamy Greens Pie with Baked Eggs", 282),
            ("Clam and Fennel Pizza with Gremolata", 285),
            ("Soft and Crispy Focaccia", 289),
            ("Honey Tahini Challah", 295),
            ("Feta-Za'atar Flatbread with Charred Eggplant Dip", 299),
            ("Pigs in a Brioche Blanket", 303),
            ("Ricotta and Broccoli Rabe Pie", 305),
            ("All Allium Deep-Dish Quiche", 309),
            ("Pull-Apart Sour Cream and Chive Rolls", 313),
        ],
        "Foundation Recipes": [
            ("All-Purpose Crumble Topping", 319),
            ("Honey Almond Syrup", 320),
            ("Pastry Cream", 321),
            ("Classic Cream Cheese Frosting", 324),
            ("Graham Cracker Crust", 326),
            ("Frangipane", 329),
            ("Lemon Curd", 330),
            ("Flaky All-Butter Pie Dough", 333),
            ("Sweet Tart Dough", 338),
            ("Flaky Olive Oil Dough", 341),
            ("Sweet Yeast Dough", 344),
            ("Pâte à Choux", 346),
            ("Soft and Pillowy Flatbread", 349),
            ("Brioche Dough", 352),
            ("Rough Puff Pastry", 355),
            ("Silkiest Chocolate Buttercream", 359),
        ],
    },
    "WFD": {
        "Chilled & Frozen Desserts": [
            ("Roasted Red Plum & Biscoff Icebox Cake", 39),
            ("French 75 Jelly with Grapefruit", 42),
            ("Pineapple & Coconut-Rum Sundaes", 45),
            ("Melon Parfaits", 47),
            ("Persimmon Panna Cotta", 51),
            ("Goat Milk Panna Cotta with Guava Sauce", 52),
            ("Classic Sundae Bombe", 55),
            ("Salty Brownie Ice Cream Sandwiches", 57),
            ("No-Bake Lime-Coconut Custards with Coconut Crumble", 61),
            ("Tiramisu-y Icebox Cake", 63),
            ("No-Bake Strawberry Ricotta Cheesecake", 67),
            ("Grape Semifreddo", 71),
            ("No-Bake Grapefruit Bars", 75),
            ("Marbled Mint Chocolate Mousse", 77),
            ("Coffee Stracciatella Semifreddo", 80),
            ("Mango-Yogurt Mousse", 83),
        ],
        "Stovetop Desserts": [
            ("Hot Chocolate with Marshmallows", 87),
            ("Coconut-Jasmine Rice Pudding with Lychee", 89),
            ("Tapioca Pudding with Saffron & Pomegranate", 90),
            ("Creamy Rice Pudding with Candied Kumquats", 93),
            ("Toasted Farro Pudding with Red Wine Cherries", 94),
            ("Banoffee Pudding", 97),
            ("Burnt Maple Pain Perdu", 99),
            ("Chocolate Coupes", 103),
            ("Old-Fashioned Cherries Jubilee", 105),
            ("Bananas Flambé", 110),
            ("Floating Islands", 113),
            ("Sweet Cheese Blintzes with Lemony Apricot Compote", 115),
            ("Malted & Salted Caramel Pudding", 119),
            ("Frosted Sour Cream Cake Donuts", 121),
            ("Buckwheat & Lemon Crepes Suzette", 125),
            ("Pillowy Beignets", 127),
        ],
        "Easy Cakes": [
            ("Fennel & Olive Oil Cake with Blackberries", 133),
            ("Rhubarb & Oat Crumb Cakes", 137),
            ("Honey-Roasted Apple Cake", 138),
            ("Blueberry Buckle with Cornflake Streusel", 141),
            ("Crunchy Almond Cake", 143),
            ("Peach, Bourbon & Pecan Cake", 147),
            ("Morning Glorious Loaf Cake", 151),
            ("Cranberry Anadama Cake", 153),
            ("Molten Chocolate Olive Oil Cakes", 157),
            ("Sticky Pumpkin-Chestnut Gingerbread", 158),
            ("Crystallized Meyer Lemon Bundt Cake", 161),
            ("Polenta Pistachio Pound Cake", 164),
            ("Flourless Chocolate Meringue Cake", 167),
            ("Malted Banana Upside-Down Cake with Malted Cream", 171),
            ("Whipped Cream Tres Leches Cake with Hazelnuts", 173),
            ("Marbled Sheet Cake", 176),
        ],
        "Bars, Cookies & Candied Things": [
            ("Raspberry Almond Thumbprints", 183),
            ("Phyllo Cardamom Pinwheels", 186),
            ("Seedy Whole Wheat Chocolate Chip Skillet Cookie", 189),
            ("Cocoa-Chestnut Brownies", 192),
            ("Honey & Tahini Toffee Matzo", 195),
            ("Salty Cashew Blondies", 196),
            ("Glazed Spelt Graham Crackers", 199),
            ("Toasted Rice Sablés", 203),
            ("Lime Squiggles", 204),
            ("All-In Shortbreads", 207),
            ("Coconut Macaroon Bars", 209),
            ("Caramel Peanut Popcorn Bars", 213),
            ("Free-Form Hazelnut Florentines", 214),
            ("Sugar Cookies", 217),
            ("Blue & White Cookies", 220),
            ("Prune & Almond Rugelach", 223),
        ],
        "Pies, Tarts, Cobblers & Crisps": [
            ("Cherry & Brown Butter Buckwheat Crisp", 229),
            ("Pastry Bianco with Slow-Roasted Plums", 233),
            ("Berry Crisp with Seedy Granola Topping", 234),
            ("Easy Apple Galette", 237),
            ("Apricot & Strawberry Galette", 238),
            ("Honeyed Nut & Phyllo Pie", 241),
            ("Banana-Sesame Cream Tart", 245),
            ("Peach Drop Biscuit Cobbler", 248),
            ("Rhubarb & Raspberry Shortcakes with Poppy Seeds", 251),
            ("Roasted Lemon Tart", 253),
            ("S'mores Tart", 257),
            ("Caramelized Pear Turnover with Sage", 261),
            ("Quince & Pineapple Jam Tart", 265),
            ("Cinnamon-&-Sugar Apple Pie", 267),
            ("Fried Sour Cherry Pies", 271),
            ("Walnut & Oat Slab Pie", 273),
        ],
        "More Desserts from the Oven": [
            ("Cajeta Pots de Crème", 279),
            ("Baked Semolina Pudding with Clementines & Bay Leaves", 283),
            ("Baked Frangipane Apples", 284),
            ("Spiced Pear Charlotte with Brioche", 287),
            ("Choose-Your-Own-Ending Custards: Crème Brûlée or Crème Caramel", 291),
            ("Inverted Affogatos", 294),
            ("Blood Orange Pudding Cake", 297),
            ("Grand Marnier Soufflés", 300),
            ("Chocolate Soufflés", 303),
            ("Rye Bread Pudding with Rye Whiskey Caramel Sauce", 307),
            ("Cherry Pavlova with Hibiscus", 309),
            ("Profiterole Bar with Berry, Hot Fudge, and Salted Caramel Sauces", 312),
            ("Kabocha & Ginger Soufflés", 317),
            ("Eton Mess Two Ways", 319),
            ("Black Sesame Merveilleux", 323),
            ("Souffléed Lemon Bread Pudding", 325),
        ],
        "Essential Recipes & Techniques": [
            ("All-Purpose Flaky Pastry Dough", 331),
            ("All-Purpose Drop Biscuit or Shortcake Dough", 336),
            ("Easy Marshmallows", 341),
            ("All-Purpose Meringue", 344),
            ("Salted Caramel Sauce", 346),
            ("Crème Anglaise", 348),
        ],
    },
}

FOUNDATION_SECTIONS = {"Foundation Recipes", "Essential Recipes & Techniques"}

TIME_LABEL_TO_MIN: dict[str, int] = {
    "5 MIN": 5,
    "1 HOUR": 60,
    "1.5 HOURS": 90,
    "2 HOURS": 120,
    "2.5 HOURS": 150,
    "3 HOURS": 180,
    "3.5 HOURS": 210,
    "4 HOURS": 240,
    "6 HOURS": 360,
    "12 HOURS+": 720,
}

# ---------------------------------------------------------------------------
# Matrix extraction via Claude vision
# ---------------------------------------------------------------------------

# After rotating the matrix image 90° CCW to read it normally:
#   X axis (columns, left→right) = TOTAL TIME  (5 MIN → 12 HOURS+)
#   Y axis (rows, top→bottom)    = DIFFICULTY  (1 = easiest → 5 or 3)
# Each cell contains bullet-pointed entries: "• Recipe Name (page_number)"

MATRIX_PROMPT = """\
This image shows a recipe matrix from a baking cookbook, rotated to read normally.

Layout after rotation:
- X axis (columns, left to right): TOTAL TIME — columns are labeled 5 MIN, 1 HOUR, \
1.5 HOURS, 2 HOURS, 2.5 HOURS, 3 HOURS, 3.5 HOURS, 4 HOURS, 6 HOURS, 12 HOURS+
- Y axis (rows, top to bottom): DIFFICULTY — rows are labeled 1 (easiest) through \
5 (or 3 for some editions)

Each grid cell contains bullet-pointed recipe entries in the form:
  • Recipe Name (page_number)

Extract every recipe entry visible. Return a JSON array only, no other text:
[
  {
    "recipe_name": "...",
    "page": <integer>,
    "difficulty": <integer>,
    "total_time_label": "<one of the column labels above, exactly as printed>"
  },
  ...
]
"""


def rotate_ccw(path: Path) -> bytes:
    img = Image.open(path)
    rotated = img.rotate(90, expand=True)
    buf = io.BytesIO()
    rotated.save(buf, format="JPEG")
    return buf.getvalue()


def extract_matrix_for_book(book: str) -> list[dict]:
    path = IMAGES / book.lower() / f"{book.lower()}_matrix.jpeg"
    print(f"  Extracting {book} matrix from {path.name} via Claude vision...")
    img_bytes = rotate_ccw(path)
    b64 = base64.standard_b64encode(img_bytes).decode()

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=8192,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
                },
                {"type": "text", "text": MATRIX_PROMPT},
            ],
        }],
    )
    raw = msg.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
    entries = json.loads(raw)
    print(f"    → {len(entries)} entries")
    return entries


def get_matrix_data(force: bool = False) -> dict[str, list[dict]]:
    if not force and MATRIX_DATA_PATH.exists():
        print("Loading cached matrix data...")
        return load_matrix_data()

    print("Extracting matrix data via Claude vision...")
    data = {}
    for book in ["DP", "WFD"]:
        data[book] = extract_matrix_for_book(book)
    save_matrix_data(data)
    return data


# ---------------------------------------------------------------------------
# Build recipe list
# ---------------------------------------------------------------------------

def build_recipes_db(matrix_data: dict[str, list[dict]]) -> list[dict]:
    recipes: list[dict] = []

    for book, sections in TOC.items():
        # index matrix entries by page for O(1) lookup
        matrix_by_page: dict[int, dict] = {
            e["page"]: e
            for e in matrix_data.get(book, [])
            if e.get("page")
        }

        for section, entries in sections.items():
            is_foundation = section in FOUNDATION_SECTIONS
            for name, page in entries:
                matrix = matrix_by_page.get(page, {})
                time_label = (matrix.get("total_time_label") or "").upper().strip()

                recipes.append({
                    "canonical_name":   name,
                    "book":             book,
                    "book_section":     section,
                    "page":             page,
                    "difficulty":       matrix.get("difficulty"),
                    "total_time_min":   TIME_LABEL_TO_MIN.get(time_label),
                    "total_time_label": matrix.get("total_time_label"),
                    "is_foundation":    is_foundation,
                    # Reddit fields populated by attach_reddit.py
                    "submission_count": 0,
                    "thread_mentions":  0,
                    "submission_ids":   [],
                    "top_comments":     [],
                })

    return recipes


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force-matrix", action="store_true",
        help="Re-extract matrix data from images even if cache exists",
    )
    args = parser.parse_args()

    matrix_data = get_matrix_data(force=args.force_matrix)

    print("Building recipe database...")
    recipes = build_recipes_db(matrix_data)

    # Coverage summary
    for book in ["DP", "WFD"]:
        book_recipes = [r for r in recipes if r["book"] == book]
        with_matrix = sum(1 for r in book_recipes if r["difficulty"] is not None)
        print(f"  {book}: {len(book_recipes)} recipes, {with_matrix} with matrix data")

    save_recipes_db(recipes)

    # Warn about any TOC entries missing from matrix
    for book, sections in TOC.items():
        matrix_pages = {e["page"] for e in matrix_data.get(book, []) if e.get("page")}
        for section, entries in sections.items():
            for name, page in entries:
                if page not in matrix_pages and section not in FOUNDATION_SECTIONS:
                    print(f"  WARNING: no matrix entry for [{book}] {name} (p.{page})")


if __name__ == "__main__":
    main()
