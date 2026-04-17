"""Thin helpers for loading/saving project artifacts."""
import pickle
import json
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data" / "derived"


def _ensure(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


# --- Reddit graph ---

GRAPH_PATH = DATA / "dessertperson_graph.pkl"

def save_graph(G):
    with open(_ensure(GRAPH_PATH), "wb") as f:
        pickle.dump(G, f)
    print(f"Saved graph → {GRAPH_PATH}")

def load_graph():
    with open(GRAPH_PATH, "rb") as f:
        return pickle.load(f)


# --- Matrix extraction cache ---

MATRIX_DATA_PATH = DATA / "matrix_data.json"

def save_matrix_data(data: dict):
    with open(_ensure(MATRIX_DATA_PATH), "w") as f:
        json.dump(data, f, indent=2)
    print(f"Saved matrix data → {MATRIX_DATA_PATH}")

def load_matrix_data() -> dict:
    with open(MATRIX_DATA_PATH) as f:
        return json.load(f)


# --- Recipe database ---

RECIPES_DB_PATH = DATA / "recipes_db.json"

def save_recipes_db(recipes: list[dict]):
    with open(_ensure(RECIPES_DB_PATH), "w") as f:
        json.dump(recipes, f, indent=2)
    print(f"Saved {len(recipes)} recipes → {RECIPES_DB_PATH}")

def load_recipes_db() -> list[dict]:
    with open(RECIPES_DB_PATH) as f:
        return json.load(f)

