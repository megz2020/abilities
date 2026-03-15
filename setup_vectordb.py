"""
setup_vectordb.py — One-time data loader for health-supplement-search ability.

Weaviate path:  Uses Weaviate's FREE built-in Snowflake Arctic embeddings.
                No Jina API key needed. Just send text — Weaviate embeds it.

Qdrant path:    Uses Jina AI for embeddings (free tier).
                Requires JINA_API_KEY in .env.

Usage:
    python setup_vectordb.py --provider weaviate --data dataset_100_supplements.json
    python setup_vectordb.py --provider qdrant   --data dataset_100_supplements.json

Requirements:
    pip install httpx tqdm
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx
from tqdm import tqdm


def _load_dotenv():
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

WEAVIATE_URL = os.environ.get("WEAVIATE_URL", "")
WEAVIATE_API_KEY = os.environ.get("WEAVIATE_API_KEY", "")
WEAVIATE_CLASS = "Supplement"
WEAVIATE_EMBED_MODEL = "Snowflake/snowflake-arctic-embed-l-v2.0"

JINA_API_KEY = os.environ.get("JINA_API_KEY", "")
JINA_URL = "https://api.jina.ai/v1/embeddings"
JINA_MODEL = "jina-embeddings-v3"
JINA_DIMENSIONS = 1536

QDRANT_URL = os.environ.get("QDRANT_URL", "")
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "")
QDRANT_COLLECTION = "supplements"

BATCH_SIZE = 10


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------

def load_dataset(path: str) -> list:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    products = []
    for idx, (_, val) in enumerate(raw.items()):
        products.append({
            "id": idx + 1,
            "name": val.get("name", ""),
            "brand": val.get("brand", ""),
            "rating": float(val.get("rating") or 0.0),
            "description": val.get("description", ""),
            "ingredients": val.get("ingredients", ""),
            "reviews": val.get("reviews", []),
            "summary": val.get("summary", ""),
            "effects": str(val.get("effects", "")),
            "image": val.get("img", val.get("image", "")),
        })
    return products


def make_search_text(product: dict) -> str:
    parts = [product.get("description", ""), product.get("summary", ""),
             product.get("effects", ""), product.get("ingredients", ""), product.get("name", "")]
    return " ".join(p for p in parts if p).strip()


# ---------------------------------------------------------------------------
# Weaviate — built-in Snowflake Arctic embeddings (no Jina needed)
# ---------------------------------------------------------------------------

def weaviate_headers() -> dict:
    return {
        "Authorization": f"Bearer {WEAVIATE_API_KEY}",
        "Content-Type": "application/json",
        "X-Weaviate-Cluster-Url": WEAVIATE_URL,
    }


def weaviate_create_schema(client: httpx.Client):
    schema_url = f"{WEAVIATE_URL}/v1/schema"
    headers = weaviate_headers()
    existing = client.get(schema_url, headers=headers, timeout=15)
    existing.raise_for_status()
    if WEAVIATE_CLASS in [c["class"] for c in existing.json().get("classes", [])]:
        print(f"Class '{WEAVIATE_CLASS}' already exists — deleting...")
        client.delete(f"{schema_url}/{WEAVIATE_CLASS}", headers=headers, timeout=15).raise_for_status()
        time.sleep(2)
    schema = {
        "class": WEAVIATE_CLASS,
        "description": "Health supplement products",
        "vectorizer": "text2vec-weaviate",
        "moduleConfig": {"text2vec-weaviate": {"model": WEAVIATE_EMBED_MODEL}},
        "properties": [
            {"name": "description", "dataType": ["text"]},
            {"name": "summary",     "dataType": ["text"]},
            {"name": "effects",     "dataType": ["text"]},
            {"name": "ingredients", "dataType": ["text"]},
            {"name": "name",    "dataType": ["text"],   "moduleConfig": {"text2vec-weaviate": {"skip": True}}},
            {"name": "brand",   "dataType": ["text"],   "moduleConfig": {"text2vec-weaviate": {"skip": True}}},
            {"name": "rating",  "dataType": ["number"], "moduleConfig": {"text2vec-weaviate": {"skip": True}}},
            {"name": "image",   "dataType": ["text"],   "moduleConfig": {"text2vec-weaviate": {"skip": True}}},
            {"name": "reviews", "dataType": ["text[]"], "moduleConfig": {"text2vec-weaviate": {"skip": True}}},
        ],
    }
    client.post(schema_url, headers=headers, json=schema, timeout=30).raise_for_status()
    print(f"Created Weaviate class '{WEAVIATE_CLASS}' ({WEAVIATE_EMBED_MODEL})")


def weaviate_import_batch(client: httpx.Client, products: list):
    headers = weaviate_headers()
    objects = [
        {
            "class": WEAVIATE_CLASS,
            "properties": {k: p[k] for k in ("name","brand","rating","description","ingredients","reviews","summary","effects","image")},
        }
        for p in products
    ]
    resp = client.post(f"{WEAVIATE_URL}/v1/batch/objects", headers=headers, json={"objects": objects}, timeout=60)
    resp.raise_for_status()
    errors = [r for r in resp.json() if r.get("result", {}).get("errors")]
    if errors:
        print(f"  Warning: {len(errors)} objects had errors")


def upload_to_weaviate(products: list):
    print(f"\nUploading {len(products)} products to Weaviate...")
    with httpx.Client() as client:
        weaviate_create_schema(client)
        for i in tqdm(range(0, len(products), BATCH_SIZE), desc="Importing"):
            weaviate_import_batch(client, products[i:i + BATCH_SIZE])
            time.sleep(0.5)
    print(f"Done. Wait ~30 seconds for Weaviate to generate embeddings before querying.")


# ---------------------------------------------------------------------------
# Qdrant — Jina AI embeddings
# ---------------------------------------------------------------------------

def embed_batch_jina(texts: list) -> list:
    resp = httpx.post(
        JINA_URL,
        headers={"Authorization": f"Bearer {JINA_API_KEY}", "Content-Type": "application/json"},
        json={"model": JINA_MODEL, "input": texts, "dimensions": JINA_DIMENSIONS},
        timeout=30,
    )
    resp.raise_for_status()
    return [item["embedding"] for item in resp.json()["data"]]


def qdrant_create_collection(client: httpx.Client):
    url = f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}"
    headers = {"api-key": QDRANT_API_KEY, "Content-Type": "application/json"}
    if client.get(url, headers=headers, timeout=15).status_code == 200:
        print(f"Collection '{QDRANT_COLLECTION}' exists — deleting...")
        client.delete(url, headers=headers, timeout=15).raise_for_status()
        time.sleep(1)
    client.put(url, headers=headers, json={"vectors": {"size": JINA_DIMENSIONS, "distance": "Cosine"}}, timeout=30).raise_for_status()
    print(f"Created Qdrant collection '{QDRANT_COLLECTION}'")


def qdrant_upsert_batch(client: httpx.Client, products: list, vectors: list):
    headers = {"api-key": QDRANT_API_KEY, "Content-Type": "application/json"}
    points = [
        {"id": p["id"], "vector": v, "payload": {k: p[k] for k in ("name","brand","rating","description","ingredients","reviews","summary","effects","image")}}
        for p, v in zip(products, vectors)
    ]
    client.put(f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}/points", headers=headers, json={"points": points}, timeout=30).raise_for_status()


def upload_to_qdrant(products: list):
    print(f"\nUploading {len(products)} products to Qdrant...")
    with httpx.Client() as client:
        qdrant_create_collection(client)
        for i in tqdm(range(0, len(products), BATCH_SIZE), desc="Embedding + uploading"):
            batch = products[i:i + BATCH_SIZE]
            qdrant_upsert_batch(client, batch, embed_batch_jina([make_search_text(p) for p in batch]))
            time.sleep(0.3)
    print("Done.")


# ---------------------------------------------------------------------------
# Validation & main
# ---------------------------------------------------------------------------

def validate_config(provider: str):
    missing = []
    if provider == "weaviate":
        if not WEAVIATE_URL: missing.append("WEAVIATE_URL")
        if not WEAVIATE_API_KEY: missing.append("WEAVIATE_API_KEY")
    else:
        if not JINA_API_KEY: missing.append("JINA_API_KEY")
        if not QDRANT_URL: missing.append("QDRANT_URL")
        if not QDRANT_API_KEY: missing.append("QDRANT_API_KEY")
    if missing:
        print(f"ERROR: Missing keys in .env: {', '.join(missing)}")
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=["qdrant", "weaviate"], required=True)
    parser.add_argument("--data", required=True)
    args = parser.parse_args()

    validate_config(args.provider)
    products = load_dataset(args.data)
    print(f"Loaded {len(products)} products.")

    if args.provider == "weaviate":
        upload_to_weaviate(products)
    else:
        upload_to_qdrant(products)
