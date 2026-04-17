#!/usr/bin/env python3
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "arctic_shift", "scripts"))

from fileStreams import getFileJsonStream
from utils import FileProgressLog
import networkx as nx

SUBMISSIONS_PATH = os.path.join(os.path.dirname(__file__), "data/reddit/subreddits25/DessertPerson_submissions.zst")
COMMENTS_PATH    = os.path.join(os.path.dirname(__file__), "data/reddit/subreddits25/DessertPerson_comments.zst")


def _removed(row: dict) -> str | None:
    meta = row.get("_meta") or {}
    return meta.get("removal_type")


def extract_submission(row: dict) -> dict:
    return {
        "type":            "submission",
        "title":           row.get("title", ""),
        "selftext":        row.get("selftext", ""),
        "url":             row.get("url", ""),
        "author":          row.get("author", ""),
        "created_utc":     row.get("created_utc"),
        "score":           row.get("score"),
        "num_comments":    row.get("num_comments"),
        "link_flair_text": row.get("link_flair_text"),
        "is_self":         row.get("is_self"),
        "removed":         _removed(row),
    }


def extract_comment(row: dict) -> dict:
    return {
        "type":         "comment",
        "body":         row.get("body", ""),
        "author":       row.get("author", ""),
        "created_utc":  row.get("created_utc"),
        "score":        row.get("score"),
        "parent_id":    row.get("parent_id"),
        "link_id":      row.get("link_id"),
        "is_submitter": row.get("is_submitter"),
        "removed":      _removed(row),
    }


def build_graph() -> nx.DiGraph:
    G = nx.DiGraph()

    print("Loading submissions...")
    with open(SUBMISSIONS_PATH, "rb") as f:
        stream = getFileJsonStream(SUBMISSIONS_PATH, f)
        log = FileProgressLog(SUBMISSIONS_PATH, f)
        for row in stream:
            G.add_node("t3_" + row["id"], **extract_submission(row))
            log.onRow()
        log.logProgress("\n")

    print("Loading comments...")
    with open(COMMENTS_PATH, "rb") as f:
        stream = getFileJsonStream(COMMENTS_PATH, f)
        log = FileProgressLog(COMMENTS_PATH, f)
        for row in stream:
            node_id = "t1_" + row["id"]
            G.add_node(node_id, **extract_comment(row))
            parent_id = row.get("parent_id")
            if parent_id:
                G.add_edge(parent_id, node_id)
            log.onRow()
        log.logProgress("\n")

    return G


def graph_summary(G: nx.DiGraph):
    submissions = sum(1 for _, d in G.nodes(data=True) if d.get("type") == "submission")
    comments    = sum(1 for _, d in G.nodes(data=True) if d.get("type") == "comment")
    orphans     = sum(1 for n, d in G.nodes(data=True)
                      if d.get("type") == "comment" and G.in_degree(n) == 0)
    print(f"Nodes : {G.number_of_nodes():,}  ({submissions:,} submissions, {comments:,} comments)")
    print(f"Edges : {G.number_of_edges():,}")
    print(f"Orphan comments (parent not in graph): {orphans:,}")


if __name__ == "__main__":
    G = build_graph()
    graph_summary(G)
    print("Done :>")
