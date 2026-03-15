# Health Supplement Search — Setup

One-time data loader for the [health-supplement-search](https://github.com/megz2020/abilities/tree/feat/health-supplement-search/community/health-supplement-search) OpenHome ability.

Loads 100 curated supplement products into your vector database so the ability can perform semantic search.

## Files

| File | Description |
|------|-------------|
| `setup_vectordb.py` | Main setup script |
| `dataset_100_supplements.json` | 100 supplement products from iHerb |
| `.env.example` | Template for your API keys |

## Prerequisites

```bash
pip install httpx tqdm
```

## Step 1 — Get API Keys

### Weaviate Cloud (recommended — built-in free embeddings, no Jina needed)
1. Go to [console.weaviate.cloud](https://console.weaviate.cloud)
2. Create a free sandbox cluster
3. Copy your Cluster URL and API key

> Note: Weaviate free sandboxes expire after 14 days. For long-term use, switch to Qdrant.

### Qdrant Cloud (recommended for long-term — free forever)
1. Go to [cloud.qdrant.io](https://cloud.qdrant.io)
2. Create a free cluster (1GB RAM, forever free)
3. Copy your Cluster URL and API key

### Jina AI (only needed for Qdrant path)
1. Go to [jina.ai/embeddings](https://jina.ai/embeddings/)
2. Create a free account — API key is instant, no credit card

## Step 2 — Configure Keys

```bash
cp .env.example .env
# Edit .env and fill in your keys
```

## Step 3 — Run Setup

```bash
# Weaviate (no Jina key needed)
python setup_vectordb.py --provider weaviate --data dataset_100_supplements.json

# Qdrant (requires JINA_API_KEY in .env)
python setup_vectordb.py --provider qdrant --data dataset_100_supplements.json
```

## Step 4 — Configure the Ability

Save `health_supplement_config.json` via the OpenHome file storage:

```json
{
  "vector_db_provider": "weaviate",
  "weaviate_url": "https://your-cluster.weaviate.cloud",
  "weaviate_api_key": "your_key",
  "weaviate_class": "Supplement",
  "serper_api_key": "",
  "distance_threshold": 0.7
}
```

For Qdrant:
```json
{
  "vector_db_provider": "qdrant",
  "jina_api_key": "jina_xxxx",
  "qdrant_url": "https://your-cluster.qdrant.io:6333",
  "qdrant_api_key": "your_key",
  "qdrant_collection": "supplements",
  "serper_api_key": "",
  "distance_threshold": 0.7
}
```

## Optional: Serper Web Fallback

Get a free [Serper API](https://serper.dev) key (2,500 searches/month free) and add it to the config. When a supplement is not found in the local DB, the ability will search the web as a fallback.
