"""
ingress/memory/semantic_cache.py - SRAM L1 Vector Cache

Implements an ultra-fast (<50ms) semantic cache using RediSearch.
Short-circuits the ADC (Ingress) if an incoming news item is >95% similar to one
previously processed by the LLM. Uses a local ONNX encoder (fastembed).
"""

import json
import logging

import numpy as np
from redis import Redis
from redis.commands.search.field import TextField, VectorField
from redis.commands.search.index_definition import IndexDefinition, IndexType
from redis.commands.search.query import Query
from redis.exceptions import ConnectionError, TimeoutError

from ingress.core.config import Settings, get_settings

try:
    from fastembed import TextEmbedding

    HAS_FASTEMBED = True
except ImportError:
    HAS_FASTEMBED = False

logger = logging.getLogger("ingress.memory.semantic_cache")


class SemanticCache:
    """
    L1 Semantic Cache Bank.
    """

    def __init__(self, settings: Settings, index_name="news_cache_idx"):
        self.index_name = index_name
        self._enabled = False
        self._settings = settings

        try:
            self.redis = Redis(
                host=self._settings.redis_host,
                port=self._settings.redis_port,
                decode_responses=False,
                socket_timeout=2.0,  # Short timeout to fail fast
            )
            self.redis.ping()  # Verify the real connection

            if HAS_FASTEMBED:
                self.encoder = TextEmbedding(model_name="sentence-transformers/all-MiniLM-L6-v2")
                self.dim = 384
                self._enabled = True
                self._setup_index()
                logger.info(
                    "[SemanticCache] 🟢 Successfully connected to Redis Stack and FastEmbed ONNX."
                )
            else:
                logger.warning("[SemanticCache] 🟡 fastembed not detected. Passive bypass mode.")

        except Exception as e:
            logger.error("[SemanticCache] 🔴 Redis/Encoder initialization error: %s", e)

    def index_exists(self) -> bool:
        """Checks whether the RediSearch index exists in the SRAM bank."""
        try:
            self.redis.ft(self.index_name).info()
            return True
        except Exception:
            return False

    def _setup_index(self):
        """Prepares the RediSearch topology if it doesn't exist."""
        if self.index_exists():
            logger.info("[SemanticCache] SRAM index %s already exists.", self.index_name)
            return

        try:
            logger.info("[SemanticCache] Formatting RediSearch index (SRAM L1)...")
            schema = (
                TextField("news_text"),
                TextField("signal_json"),
                VectorField(
                    "embedding",
                    "FLAT",
                    {"TYPE": "FLOAT32", "DIM": self.dim, "DISTANCE_METRIC": "COSINE"},
                ),
            )
            definition = IndexDefinition(prefix=["news: dramas"], index_type=IndexType.HASH)
            self.redis.ft(self.index_name).create_index(fields=schema, definition=definition)
            logger.info("[SemanticCache] Index created and ready for vector search.")
        except Exception as e:
            logger.error("[SemanticCache] 🔴 CRITICAL error creating index: %s", e)
            self._enabled = False

    def search(self, text: str, threshold: float = 0.95) -> dict | None:
        """Compares the incoming vector's phase and returns the signal if it matches."""
        if not self._enabled:
            return None

        try:
            if not self.index_exists():
                self._setup_index()
                if not self._enabled:
                    return None

            # Fast local inference (C++) -> 1x384 float32 vector
            vec_gen = self.encoder.embed([text])
            vector = list(vec_gen)[0].astype(np.float32).tobytes()

            # KNN = 1. Returns the nearest neighbor if there is one
            q = (
                Query("*=>[KNN 1 @embedding $vec_param AS vector_score]")
                .return_fields("signal_json", "vector_score")
                .sort_by("vector_score")
                .dialect(2)
            )

            res = self.redis.ft(self.index_name).search(q, query_params={"vec_param": vector})

            if res.docs:
                doc = res.docs[0]
                # 🔧 SRE FIX: Safe read of RediSearch attributes (Dict or Attribute)
                distance = float(getattr(doc, "vector_score", 1.0))
                similarity = 1.0 - distance

                if similarity >= threshold:
                    logger.info(
                        "[SemanticCache] ⚡ CACHE HIT! Similarity: %.2f%%", similarity * 100
                    )
                    signal_raw = getattr(doc, "signal_json", "{}")
                    return json.loads(signal_raw)

                logger.debug(
                    "[SemanticCache] 🐢 CACHE MISS. Max diff: %.2f%% < %.2f%%",
                    similarity * 100,
                    threshold * 100,
                )
                return None

            logger.debug("[SemanticCache] ⚪ Empty SRAM record. Proceeding to LLM.")
            return None

        except (ConnectionError, TimeoutError) as e:
            logger.error("[SemanticCache] 🔴 CRITICAL error contacting the Redis L1 bank: %s", e)
            return None  # 🔧 SRE FIX: Graceful bypass instead of Raise to avoid taking down the ADC

        except Exception as e:
            logger.error("[SemanticCache] ⚠️ LLM inference failure validating the L1 bank: %s", e)
            return None

    def store(self, text: str, signal_dict: dict):
        """Persists the Judge LLM results in the SRAM bank."""
        if not self._enabled:
            return

        try:
            if not self.index_exists():
                self._setup_index()
                if not self._enabled:
                    return

            import hashlib

            vec_gen = self.encoder.embed([text])
            vector = list(vec_gen)[0].astype(np.float32).tobytes()
            stable_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
            key = f"news:{stable_hash}"

            mapping = {
                "news_text": text,
                "signal_json": json.dumps(signal_dict),
                "embedding": vector,
            }

            self.redis.hset(key, mapping=mapping)
            self.redis.expire(key, 86400)
            logger.info(
                "[SemanticCache] 💾 New semantic fingerprint persisted in RediSearch (TTL: 24h)."
            )
        except Exception as e:
            logger.error("[SemanticCache] ⚠️ Error writing SRAM to Redis: %s", e)


_instance = None


def get_semantic_cache() -> SemanticCache:
    """Injects the official Semantic Cache dependency."""
    global _instance
    if _instance is None:
        _instance = SemanticCache(settings=get_settings())
    return _instance
