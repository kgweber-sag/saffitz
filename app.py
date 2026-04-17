#!/usr/bin/env python3
"""
Saffitz Recipe Browser
Run: conda run -n saffitz streamlit run app.py
"""

import json
from pathlib import Path

import streamlit as st

DATA_PATH     = Path(__file__).parent / "data" / "derived" / "recipes_db.json"
PERSONAL_PATH = Path(__file__).parent / "data" / "personal.json"

DIFFICULTY_LABELS = {1: "Easy", 2: "Easy+", 3: "Moderate", 4: "Hard", 5: "Very Hard"}
STAR_OPTIONS = ["—", "★", "★★", "★★★", "★★★★", "★★★★★"]
SUPERSCRIPTS = {1: "¹", 2: "²", 3: "³", 4: "⁴", 5: "⁵", 6: "⁶"}
FRACTION_MAP = [(0.125, "⅛"), (0.25, "¼"), (0.333, "⅓"), (0.5, "½"), (0.667, "⅔"), (0.75, "¾")]


# ── Data ───────────────────────────────────────────────────────────────────────

@st.cache_data
def load_data():
    with open(DATA_PATH) as f:
        recipes = json.load(f)

    recipe_index = {}   # (book, page) → recipe
    name_index = {}     # canonical_name → recipe

    for r in recipes:
        recipe_index[(r["book"], r["page"])] = r
        name_index[r["canonical_name"]] = r

        # Build searchable text blob
        parts = [
            r["canonical_name"],
            r.get("book_section") or "",
            r.get("season") or "",
        ]
        parts += r.get("dietary_flags", [])
        for sec in r.get("ingredient_sections", []):
            if sec.get("section_name"):
                parts.append(sec["section_name"])
            for ing in sec.get("ingredients", []):
                parts.append(ing["name"])
        for dep in r.get("dependencies", []):
            if dep.get("canonical_name"):
                parts.append(dep["canonical_name"])
        r["_search"] = " ".join(parts).lower()

    # Compute critical-path elapsed time for every recipe
    for r in recipes:
        r["_min_elapsed"] = _min_elapsed(r, recipe_index, set())

    return recipes, recipe_index, name_index


def _min_elapsed(recipe, recipe_index, visiting):
    """
    Critical path through the dependency DAG.
    min_elapsed = own total_time + max(min_elapsed of each dependency).
    total_time_min already excludes sub-recipe time, so this is additive.
    visiting guards against cycles in malformed data.
    """
    key = (recipe["book"], recipe["page"])
    if key in visiting:
        return recipe.get("total_time_min") or 0
    visiting = visiting | {key}

    own = recipe.get("total_time_min") or 0

    dep_times = []
    for dep in recipe.get("dependencies", []):
        dep_r = recipe_index.get((recipe["book"], dep.get("recipe_page")))
        if dep_r:
            dep_times.append(_min_elapsed(dep_r, recipe_index, visiting))

    return own + (max(dep_times) if dep_times else 0)


# ── Personal data (ratings + made) ────────────────────────────────────────────

def _pkey(recipe):
    return f"{recipe['book']}_{recipe['page']}"


def load_personal():
    if PERSONAL_PATH.exists():
        with open(PERSONAL_PATH) as f:
            return json.load(f)
    return {}


def save_personal(data):
    with open(PERSONAL_PATH, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def init_personal_state(recipes):
    """Seed session state from personal.json once per browser session."""
    if st.session_state.get("_personal_loaded"):
        return
    personal = load_personal()
    for r in recipes:
        pk = _pkey(r)
        entry = personal.get(pk, {})
        rating = entry.get("rating", 0)
        st.session_state[f"rating_{pk}"] = STAR_OPTIONS[rating]
        st.session_state[f"made_{pk}"] = bool(entry.get("made", False))
    st.session_state["_personal_loaded"] = True


def _save_rating(pk):
    p = load_personal()
    val = st.session_state[f"rating_{pk}"]
    entry = p.setdefault(pk, {})
    if val == "—":
        entry.pop("rating", None)
    else:
        entry["rating"] = val.count("★")
    if not entry:
        p.pop(pk, None)
    save_personal(p)


def _save_made(pk):
    p = load_personal()
    val = st.session_state[f"made_{pk}"]
    entry = p.setdefault(pk, {})
    if val:
        entry["made"] = True
    else:
        entry.pop("made", None)
    if not entry:
        p.pop(pk, None)
    save_personal(p)


# ── Formatting ─────────────────────────────────────────────────────────────────

def fmt_qty(q):
    if q is None:
        return ""
    whole = int(q)
    frac = round(q - whole, 3)
    frac_sym = next((sym for val, sym in FRACTION_MAP if abs(frac - val) < 0.02), "")
    if whole == 0:
        return frac_sym or str(q)
    return f"{whole}{frac_sym}" if frac_sym else (str(int(q)) if q == int(q) else str(q))


def fmt_time(minutes):
    if not minutes:
        return "—"
    h, m = divmod(int(minutes), 60)
    if h and m:
        return f"{h} hr {m} min"
    return f"{h} hr" if h else f"{m} min"


def fmt_time_with_elapsed(recipe):
    """
    For display in the results list.
    When min_elapsed differs materially from own total_time, append it.
    e.g.  "45 min*"  where * means sub-recipes add to elapsed wall time.
    """
    own = recipe.get("total_time_min") or 0
    elapsed = recipe.get("_min_elapsed") or own
    own_str = fmt_time(own)
    if elapsed > own and own > 0:
        return f"{own_str} ›› {fmt_time(elapsed)}"
    return own_str


def fmt_difficulty(recipe):
    d = recipe.get("difficulty")
    if not d:
        return "—"
    max_d = 3 if recipe["book"] == "WFD" else 5
    return "●" * d + "○" * (max_d - d)


def reddit_score(r):
    return r.get("thread_mentions", 0) * 3 + r.get("submission_count", 0)


def fmt_ingredient_line(ing):
    """Return the plain text of one ingredient (no sub-recipe handling)."""
    qty = fmt_qty(ing.get("quantity"))
    unit = ing.get("quantity_unit") or ""
    name = ing["name"]
    notes = ing.get("notes") or ""
    fn = ing.get("footnote_number")

    parts = []

    # Quantity + unit
    if qty and unit:
        parts.append(f"{qty} {unit}")
    elif qty:
        parts.append(qty)
    elif notes.lower() in ("pinch", "to taste", "as needed"):
        parts.append(notes)
        notes = ""

    # Weight (only when present)
    if ing.get("weight_g"):
        parts.append(f"({int(ing['weight_g'])}g)")

    # Name + optional superscript footnote marker
    parts.append(name + (SUPERSCRIPTS.get(fn, "") if fn else ""))

    # Inline prep note
    if notes and notes.lower() not in ("pinch", "to taste", "as needed"):
        parts.append(f", {notes}")

    return " ".join(parts)


# ── Search ─────────────────────────────────────────────────────────────────────

def run_search(recipes, query, filters):
    terms = query.lower().split() if query.strip() else []

    results = []
    for r in recipes:
        if terms and not all(t in r["_search"] for t in terms):
            continue
        if filters["book"] != "Both" and r["book"] != filters["book"]:
            continue
        d = r.get("difficulty") or 0
        if d and not (filters["diff_min"] <= d <= filters["diff_max"]):
            continue
        t = r.get("_min_elapsed") or r.get("total_time_min") or 0
        if t and t > filters["time_max"]:
            continue
        if filters["seasons"]:
            recipe_seasons = {s.strip() for s in (r.get("season") or "").split("/")}
            if not recipe_seasons & filters["seasons"]:
                continue
        if filters["dietary"]:
            if not set(filters["dietary"]).issubset(set(r.get("dietary_flags", []))):
                continue
        if filters["foundation_only"] and not r.get("is_foundation"):
            continue
        if filters["has_reddit"] and not r.get("submission_count"):
            continue
        if filters["uses_sub"]:
            dep_names = {dep["canonical_name"] for dep in r.get("dependencies", []) if dep.get("canonical_name")}
            if not set(filters["uses_sub"]) & dep_names:
                continue
        pk = _pkey(r)
        if filters["made_only"] and not st.session_state.get(f"made_{pk}"):
            continue
        if filters["rated_only"] and st.session_state.get(f"rating_{pk}", "—") == "—":
            continue
        results.append(r)

    sort = filters["sort"]
    if sort == "Name":
        results.sort(key=lambda r: r["canonical_name"])
    elif sort == "Difficulty":
        results.sort(key=lambda r: r.get("difficulty") or 99)
    elif sort == "Time":
        results.sort(key=lambda r: r.get("_min_elapsed") or r.get("total_time_min") or 9999)
    elif sort == "Reddit":
        results.sort(key=reddit_score, reverse=True)
    elif sort == "My Rating":
        results.sort(key=lambda r: -st.session_state.get(f"rating_{_pkey(r)}", "—").count("★"))

    return results


# ── Sub-recipe modal ───────────────────────────────────────────────────────────

@st.dialog("Sub-recipe", width="large")
def show_sub_recipe(sub_recipe, recipe_index):
    _render_recipe_header(sub_recipe, compact=True)
    st.divider()
    _render_ingredients(sub_recipe, recipe_index, in_dialog=True)
    fns = sub_recipe.get("footnotes", [])
    if fns:
        with st.expander("Footnotes"):
            for fn in fns:
                st.markdown(f"**{fn['number']}.** {fn['text']}")


# ── Recipe rendering helpers ───────────────────────────────────────────────────

def _render_recipe_header(recipe, compact=False):
    book_name = "Dessert Person" if recipe["book"] == "DP" else "What's for Dessert"
    st.caption(f"{book_name} · {recipe.get('book_section', '')} · p. {recipe['page']}")

    if compact:
        st.subheader(recipe["canonical_name"])
    else:
        st.title(recipe["canonical_name"])

    d = recipe.get("difficulty")
    max_d = 3 if recipe["book"] == "WFD" else 5
    diff_str = f"{DIFFICULTY_LABELS.get(d, '—')} ({d}/{max_d})" if d else "—"

    own_min = recipe.get("total_time_min") or 0
    elapsed_min = recipe.get("_min_elapsed") or own_min
    has_sub_time = elapsed_min > own_min and own_min > 0

    if has_sub_time:
        cols = st.columns(5)
        cols[0].metric("Difficulty", diff_str)
        cols[1].metric("Recipe Time", recipe.get("total_time_label") or fmt_time(own_min),
                       help="Time for this recipe only, not counting sub-recipes")
        cols[2].metric(
            "Min. Elapsed",
            fmt_time(elapsed_min),
            help="Critical-path estimate: assumes you prep all sub-recipes in parallel, "
                 "starting the longest one first. Doesn't account for active-time overlap.",
        )
        cols[3].metric("Active Time", recipe.get("active_time_label") or "—")
        cols[4].metric("Yield", recipe.get("yield_text") or "—")
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Difficulty", diff_str)
        c2.metric("Total Time", recipe.get("total_time_label") or fmt_time(own_min))
        c3.metric("Active Time", recipe.get("active_time_label") or "—")
        c4.metric("Yield", recipe.get("yield_text") or "—")

    badges = []
    if recipe.get("season"):
        badges.append(f"🌸 {recipe['season']}")
    if recipe.get("dietary_flags"):
        badges += recipe["dietary_flags"]
    if recipe.get("special_equipment"):
        badges.append(f"🔧 {recipe['special_equipment']}")
    if badges:
        st.caption("  ·  ".join(badges))

    if recipe.get("is_foundation"):
        st.info("Foundation recipe", icon="⭐")


def _render_ingredients(recipe, recipe_index, in_dialog=False):
    sections = recipe.get("ingredient_sections", [])
    footnotes_by_num = {fn["number"]: fn["text"] for fn in recipe.get("footnotes", [])}

    for sec in sections:
        sname = sec.get("section_name")
        if sname:
            st.markdown(f"**{sname}**")

        for ing in sec.get("ingredients", []):
            if not ing.get("is_sub_recipe"):
                st.markdown(f"- {fmt_ingredient_line(ing)}")
                continue

            # ── Sub-recipe row ──────────────────────────────────────────────
            frac = ing.get("sub_recipe_fraction")
            qty = ing.get("quantity")
            unit = ing.get("quantity_unit") or ""

            if frac is not None and frac < 1:
                qty_str = f"{fmt_qty(frac)} recipe"
            elif qty and unit:
                qty_str = f"{fmt_qty(qty)} {unit}"
            else:
                qty_str = fmt_qty(qty) if qty else ""

            fn_num = ing.get("footnote_number")
            name_text = ing["name"] + (SUPERSCRIPTS.get(fn_num, "") if fn_num else "")

            # Look up sub-recipe for inline preview
            sub_page = ing.get("sub_recipe_page")
            sub_recipe = recipe_index.get((recipe["book"], sub_page)) if sub_page else None

            preview = ""
            if sub_recipe:
                sub_d = sub_recipe.get("difficulty")
                sub_t = sub_recipe.get("total_time_label") or fmt_time(sub_recipe.get("total_time_min"))
                sub_diff = DIFFICULTY_LABELS.get(sub_d, "—") if sub_d else "—"
                preview = f"  `{sub_diff} · {sub_t}`"

            label = (f"{qty_str} " if qty_str else "") + f"**→ {name_text}**" + preview

            if sub_recipe and not in_dialog:
                col1, col2 = st.columns([5, 1])
                col1.markdown(f"- {label}")
                if col2.button(
                    "View →",
                    key=f"sub_{recipe['book']}_{recipe['page']}_{sub_page}_{ing['name'][:8]}",
                ):
                    show_sub_recipe(sub_recipe, recipe_index)
            else:
                page_hint = f"p. {sub_page}" if sub_page else ""
                st.markdown(f"- {label}  {page_hint}")


def render_recipe_detail(recipe, recipe_index):
    if st.button("← Back to results"):
        st.session_state.selected = None
        st.rerun()

    st.divider()
    _render_recipe_header(recipe)
    st.divider()

    ing_col, info_col = st.columns([3, 2])

    with ing_col:
        st.subheader("Ingredients")
        _render_ingredients(recipe, recipe_index)
        fns = recipe.get("footnotes", [])
        if fns:
            with st.expander("Footnotes"):
                for fn in fns:
                    st.markdown(f"**{fn['number']}.** {fn['text']}")

    with info_col:
        pk = _pkey(recipe)
        rc1, rc2 = st.columns([1, 2])
        rc1.checkbox("Made it", key=f"made_{pk}", on_change=_save_made, args=(pk,))
        rc2.selectbox("My rating", options=STAR_OPTIONS, key=f"rating_{pk}",
                      on_change=_save_rating, args=(pk,))
        st.divider()

        if recipe.get("do_ahead"):
            st.info(f"**Do Ahead:** {recipe['do_ahead']}", icon="⏰")

        deps = recipe.get("dependencies", [])
        if deps:
            st.subheader("Sub-recipes needed")
            for dep in deps:
                dep_name = dep.get("canonical_name") or f"p. {dep.get('recipe_page', '?')}"
                dep_r = recipe_index.get((recipe["book"], dep.get("recipe_page")))
                if dep_r:
                    d_diff = DIFFICULTY_LABELS.get(dep_r.get("difficulty"), "—")
                    d_time = dep_r.get("total_time_label") or fmt_time(dep_r.get("total_time_min"))
                    st.markdown(f"- **{dep_name}** — {d_diff} · {d_time}")
                else:
                    st.markdown(f"- {dep_name}")

        comments = recipe.get("top_comments", [])
        if comments:
            score = reddit_score(recipe)
            with st.expander(f"Reddit ({recipe.get('submission_count', 0)} posts, {recipe.get('thread_mentions', 0)} mentions)"):
                for c in comments[:10]:
                    st.markdown(f"**{c['author']}** &nbsp; ↑{c['score']}")
                    body = c["body"]
                    st.caption(body[:400] + ("…" if len(body) > 400 else ""))
                    st.divider()


# ── Sidebar ────────────────────────────────────────────────────────────────────

def build_sidebar(recipes):
    st.sidebar.title("Filters")

    all_seasons, all_dietary, all_dep_names = set(), set(), set()
    max_time = 30
    for r in recipes:
        for s in (r.get("season") or "").split("/"):
            s = s.strip()
            if s:
                all_seasons.add(s)
        all_dietary.update(r.get("dietary_flags", []))
        for dep in r.get("dependencies", []):
            if dep.get("canonical_name"):
                all_dep_names.add(dep["canonical_name"])
        if r.get("_min_elapsed"):
            max_time = max(max_time, r["_min_elapsed"])

    book = st.sidebar.radio("Book", ["Both", "DP", "WFD"], horizontal=True)

    diff_min, diff_max = st.sidebar.slider("Difficulty", 1, 5, (1, 5))

    time_max = st.sidebar.slider(
        "Max elapsed time (min)", 15, max_time, max_time, step=15,
        help="Critical-path elapsed time: includes sub-recipes, assuming parallel prep",
    )

    seasons = st.sidebar.multiselect("Season", sorted(all_seasons))
    dietary = st.sidebar.multiselect("Dietary", sorted(all_dietary))
    uses_sub = st.sidebar.multiselect(
        "Uses sub-recipe",
        sorted(all_dep_names),
        help="Show only recipes that call for this component",
    )

    st.sidebar.markdown("---")
    foundation_only = st.sidebar.checkbox("Foundation recipes only")
    has_reddit = st.sidebar.checkbox("Has Reddit posts")
    made_only = st.sidebar.checkbox("Made it only")
    rated_only = st.sidebar.checkbox("Rated only")
    sort = st.sidebar.selectbox("Sort by", ["Name", "Difficulty", "Time", "Reddit", "My Rating"],
                                help="Time sorts by min. elapsed (critical path), not recipe-only time")

    return {
        "book": book,
        "diff_min": diff_min, "diff_max": diff_max,
        "time_max": time_max,
        "seasons": set(seasons),
        "dietary": dietary,
        "uses_sub": uses_sub,
        "foundation_only": foundation_only,
        "has_reddit": has_reddit,
        "made_only": made_only,
        "rated_only": rated_only,
        "sort": sort,
    }


# ── Results list ───────────────────────────────────────────────────────────────

def render_results(results, query):
    n = len(results)
    label = f'"{query}"' if query.strip() else "all recipes"
    st.caption(f"{n} recipe{'s' if n != 1 else ''} · {label}")

    if not results:
        st.info("No recipes match. Try relaxing a filter or broadening your search.")
        return

    h = st.columns([4, 1, 2, 3, 1, 1, 2])
    for col, txt in zip(h, ["Recipe", "Book", "Difficulty", "Time (recipe › elapsed)", "Reddit", "Made", "Rating"]):
        col.caption(f"**{txt}**")
    st.divider()

    for i, r in enumerate(results):
        pk = _pkey(r)
        cols = st.columns([4, 1, 2, 3, 1, 1, 2])
        if cols[0].button(r["canonical_name"], key=f"r_{i}", use_container_width=True):
            st.session_state.selected = r["canonical_name"]
            st.rerun()
        cols[1].caption(r["book"])
        cols[2].caption(fmt_difficulty(r))
        cols[3].caption(fmt_time_with_elapsed(r))
        score = reddit_score(r)
        cols[4].caption(f"↑{score}" if score else "—")
        cols[5].checkbox("✓", key=f"made_{pk}", on_change=_save_made, args=(pk,),
                         label_visibility="collapsed")
        cols[6].selectbox("★", options=STAR_OPTIONS, key=f"rating_{pk}",
                          on_change=_save_rating, args=(pk,),
                          label_visibility="collapsed")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    st.set_page_config(
        page_title="What shall I bake?",
        page_icon="🍰",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    recipes, recipe_index, name_index = load_data()
    init_personal_state(recipes)

    if "selected" not in st.session_state:
        st.session_state.selected = None

    filters = build_sidebar(recipes)

    if st.session_state.selected:
        recipe = name_index.get(st.session_state.selected)
        if recipe:
            render_recipe_detail(recipe, recipe_index)
        else:
            st.session_state.selected = None
            st.rerun()
        return

    st.title("🍰 What shall I bake?")
    query = st.text_input(
        "search",
        placeholder="Search ingredients, recipe names, or sections — e.g.  blueberries   or   chocolate tahini",
        label_visibility="collapsed",
    )

    results = run_search(recipes, query, filters)
    render_results(results, query)


if __name__ == "__main__":
    main()
