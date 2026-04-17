#!/usr/bin/env python3
"""
Stage 3: Attach Reddit data to the recipe database.

For each recipe, fuzzy-matches its canonical name against Reddit submission titles
to find related posts, then collects top-scored comments from those threads.
Also mines "opinion" threads (best-of / recommendation posts) for recipe mentions.

Updates data/derived/recipes_db.json in-place with:
  submission_count  -- number of matched submission threads
  thread_mentions   -- mentions across opinion/recommendation threads
  submission_ids    -- list of matched submission node IDs
  top_comments      -- up to TOP_COMMENTS_PER_RECIPE highest-scored comments

Usage:
  conda run -n saffitz python attach_reddit.py
  conda run -n saffitz python attach_reddit.py --force   # re-run even if already done
"""

import argparse
import re
import sys
from pathlib import Path

from rapidfuzz import fuzz, process

sys.path.insert(0, str(Path(__file__).parent))
from persist import load_graph, load_recipes_db, save_recipes_db

TOP_COMMENTS_PER_RECIPE = 20
TITLE_MATCH_THRESHOLD   = 80   # rapidfuzz token_sort_ratio for submission title matching
OPINION_MATCH_THRESHOLD = 80   # for recipe name in opinion thread comment text
MIN_OPINION_COMMENTS    = 10   # ignore low-traffic opinion threads

OPINION_PATTERNS = re.compile(
    r"\b(favourite|favorite|best|recommend|must.?(try|make|bake)|suggest"
    r"|which recipe|what.*first|what.*next|what.*bake|what.*make"
    r"|effort.to.payoff|avoid|underrated|overrated|never.*again"
    r"|excited to make|what.*try|popular|top \d|starter)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Submission matching
# ---------------------------------------------------------------------------

def _name_variants(canonical_name: str) -> list[str]:
    """
    Generate matching forms for a recipe name.
    Includes: full name, name before "with"/"&", last significant word group.
    All returned as lowercase.
    """
    variants = [canonical_name.lower()]
    # portion before "with" or "&" connector
    for sep in (" with ", " & ", ": "):
        if sep.lower() in canonical_name.lower():
            prefix = canonical_name.lower().split(sep.lower())[0].strip()
            if len(prefix.split()) >= 2:
                variants.append(prefix)
            break
    return list(dict.fromkeys(variants))  # dedupe, preserve order


def _make_matcher(canonical_name: str):
    """Compile a regex matching any variant of the recipe name."""
    variants = _name_variants(canonical_name)
    # sort longest first to avoid substring shadowing
    patterns = sorted(variants, key=len, reverse=True)
    combined = "|".join(re.escape(p) for p in patterns if len(p) >= 5)
    return re.compile(combined, re.IGNORECASE) if combined else None


def find_submission_ids(recipe: dict, G) -> list[str]:
    """Return IDs of submissions whose title mentions this recipe."""
    matcher = _make_matcher(recipe["canonical_name"])
    if not matcher:
        return []
    return [
        nid
        for nid, data in G.nodes(data=True)
        if data.get("type") == "submission"
        and data.get("removed") is None
        and matcher.search(data.get("title", ""))
    ]


# ---------------------------------------------------------------------------
# Comment collection
# ---------------------------------------------------------------------------

def thread_comments(G, submission_id: str) -> list[dict]:
    """BFS from a submission, collect all non-deleted comment nodes."""
    comments = []
    queue = [submission_id]
    while queue:
        node = queue.pop()
        for child in G.successors(node):
            data = G.nodes[child]
            body = data.get("body", "")
            if body and body not in ("[deleted]", "[removed]"):
                comments.append({
                    "id":     child,
                    "score":  data.get("score") or 0,
                    "body":   body,
                    "author": data.get("author", ""),
                })
            queue.append(child)
    return comments


def top_comments_for_recipe(G, sub_ids: list[str]) -> list[dict]:
    all_comments: list[dict] = []
    for sid in sub_ids:
        all_comments.extend(thread_comments(G, sid))
    seen: set[str] = set()
    unique: list[dict] = []
    for c in sorted(all_comments, key=lambda x: -x["score"]):
        if c["id"] not in seen:
            seen.add(c["id"])
            unique.append(c)
    return unique[:TOP_COMMENTS_PER_RECIPE]


# ---------------------------------------------------------------------------
# Opinion thread mining
# ---------------------------------------------------------------------------

def find_opinion_threads(G) -> list[dict]:
    return sorted(
        [
            {"id": nid, "title": d["title"], "num_comments": d.get("num_comments", 0)}
            for nid, d in G.nodes(data=True)
            if d.get("type") == "submission"
            and d.get("removed") is None
            and OPINION_PATTERNS.search(d.get("title", ""))
            and (d.get("num_comments") or 0) >= MIN_OPINION_COMMENTS
        ],
        key=lambda t: -t["num_comments"],
    )


def get_thread_comment_bodies(G, submission_id: str) -> list[str]:
    bodies, queue = [], [submission_id]
    while queue:
        node = queue.pop()
        for child in G.successors(node):
            data = G.nodes[child]
            body = data.get("body", "")
            if body and body not in ("[deleted]", "[removed]"):
                bodies.append(body)
            queue.append(child)
    return bodies


def mine_thread_mentions(recipes: list[dict], G) -> dict[str, int]:
    """
    For each opinion thread, count how often each recipe name appears
    (using fuzzy sliding-window matching). Returns {canonical_name: count}.
    """
    threads = find_opinion_threads(G)
    print(f"  {len(threads)} opinion threads found")

    totals: dict[str, int] = {}
    for thread in threads:
        bodies = get_thread_comment_bodies(G, thread["id"])
        full_text = "\n".join(bodies).lower()
        words = full_text.split()

        for recipe in recipes:
            for variant in _name_variants(recipe["canonical_name"]):
                if len(variant) < 5:
                    continue
                window_size = len(variant.split())
                count = 0
                for i in range(len(words) - window_size + 1):
                    window = " ".join(words[i : i + window_size])
                    if fuzz.ratio(window, variant) >= OPINION_MATCH_THRESHOLD:
                        count += 1
                if count:
                    key = recipe["canonical_name"]
                    totals[key] = totals.get(key, 0) + count

    return totals


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force", action="store_true",
        help="Re-run even if Reddit fields are already populated",
    )
    args = parser.parse_args()

    recipes = load_recipes_db()

    already_done = any(r.get("submission_ids") for r in recipes)
    if already_done and not args.force:
        print("Reddit data already attached. Use --force to re-run.")
        return

    print("Loading Reddit graph...")
    G = load_graph()

    print("Matching submissions to recipes...")
    for i, recipe in enumerate(recipes):
        sub_ids = find_submission_ids(recipe, G)
        recipe["submission_ids"]   = sub_ids
        recipe["submission_count"] = len(sub_ids)
        recipe["top_comments"]     = top_comments_for_recipe(G, sub_ids)
        if (i + 1) % 25 == 0:
            print(f"  {i + 1}/{len(recipes)}...")
    print(f"  Done.")

    print("Mining opinion thread mentions...")
    thread_mentions = mine_thread_mentions(recipes, G)
    for recipe in recipes:
        recipe["thread_mentions"] = thread_mentions.get(recipe["canonical_name"], 0)

    save_recipes_db(recipes)

    # Summary
    print("\nTop 20 by Reddit signal (thread_mentions × 3 + submission_count):")
    ranked = sorted(
        recipes,
        key=lambda r: r.get("thread_mentions", 0) * 3 + r.get("submission_count", 0),
        reverse=True,
    )
    for r in ranked[:20]:
        print(
            f"  [{r['book']}] {r['canonical_name']:<50} "
            f"posts:{r['submission_count']:3}  mentions:{r['thread_mentions']:3}"
        )


if __name__ == "__main__":
    main()
