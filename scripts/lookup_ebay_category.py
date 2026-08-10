"""One-off helper: looks up eBay's current category ID for "Sports Trading
Cards" (and related child categories) using YOUR real credentials, since
eBay periodically reorganizes its category tree and a hardcoded ID can go
stale. Run this after setting up .env, before your first real cron run, to
confirm config/settings.json's ebay.category_id is still correct.

Usage:
    python -m scripts.lookup_ebay_category
    python -m scripts.lookup_ebay_category "Football Cards"
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from src.config import load_config
from src.ebay_client import get_app_token

DEFAULT_TREE_URL = "https://api.ebay.com/commerce/taxonomy/v1/get_default_category_tree_id"
SUGGESTIONS_URL_TMPL = "https://api.ebay.com/commerce/taxonomy/v1/category_tree/{tree_id}/get_category_suggestions"


def main() -> None:
    query = sys.argv[1] if len(sys.argv) > 1 else "Sports Trading Cards"

    cfg = load_config()
    token = get_app_token(cfg.ebay_client_id, cfg.ebay_client_secret)
    headers = {"Authorization": f"Bearer {token}"}

    tree_resp = requests.get(
        DEFAULT_TREE_URL, headers=headers, params={"marketplace_id": cfg.ebay_marketplace_id}, timeout=30
    )
    tree_resp.raise_for_status()
    tree_id = tree_resp.json()["categoryTreeId"]

    sugg_resp = requests.get(
        SUGGESTIONS_URL_TMPL.format(tree_id=tree_id), headers=headers, params={"q": query}, timeout=30
    )
    sugg_resp.raise_for_status()

    print(f"Category tree: {tree_id} ({cfg.ebay_marketplace_id})")
    print(f"Suggestions for {query!r}:\n")
    for suggestion in sugg_resp.json().get("categorySuggestions", []):
        category = suggestion["category"]
        ancestors = " > ".join(
            a["categoryName"] for a in reversed(suggestion.get("categoryTreeNodeAncestors", []))
        )
        print(f"  id={category['categoryId']:<10} {category['categoryName']}")
        if ancestors:
            print(f"      path: {ancestors}")

    print(f"\nconfig/settings.json currently has ebay.category_id = {cfg.ebay_category_id!r}")


if __name__ == "__main__":
    main()
