"""AWS Bedrock: embeddings via Titan, reasoning via a pluggable chat model.

Two clients on purpose:
  * boto3 bedrock-runtime for Titan embeddings and for the Converse API, which
    covers Nova and every other Bedrock chat model;
  * AnthropicBedrockMantle for Claude, so that path uses the ordinary Messages
    API surface instead of hand-rolling InvokeModel payloads.

Which one runs is `config.CHAT_BACKEND`. Nothing in the memory layer depends on
the answer — the guarantees this project makes are enforced by CockroachDB, and
the model is only ever a consumer of what retrieval already decided to return.
"""

import json

import boto3

from . import config

_runtime = None
_anthropic = None


def runtime():
    global _runtime
    if _runtime is None:
        _runtime = boto3.client("bedrock-runtime", region_name=config.AWS_REGION)
    return _runtime


def anthropic_client():
    global _anthropic
    if _anthropic is None:
        from anthropic import AnthropicBedrockMantle

        _anthropic = AnthropicBedrockMantle(aws_region=config.AWS_REGION)
    return _anthropic


def embed(text: str) -> list[float]:
    """Embed one string with Titan Text Embeddings V2 (1024-dim, normalized).

    Normalized vectors mean L2 distance (`<->`) and cosine distance rank
    identically, so the vector index can use the default operator.
    """
    body = json.dumps(
        {"inputText": text, "dimensions": config.EMBED_DIM, "normalize": True}
    )
    resp = runtime().invoke_model(modelId=config.BEDROCK_EMBED_MODEL, body=body)
    payload = json.loads(resp["body"].read())
    vector = payload["embedding"]
    if len(vector) != config.EMBED_DIM:
        raise RuntimeError(
            f"expected {config.EMBED_DIM}-dim embedding, got {len(vector)}"
        )
    return vector


def complete(
    system: str,
    messages: list[dict],
    max_tokens: int = 4096,
    effort: str = "medium",
) -> str:
    """One-shot completion. Routes by `config.CHAT_BACKEND`.

    `effort` applies only to the Claude path and is ignored by Converse. It
    matters there because on Claude Opus 5 thinking is on by default and
    `max_tokens` bounds thinking AND the reply together — a tight `max_tokens`
    does not produce a short answer, it produces an empty one. Keeping thinking
    enabled and turning `effort` down is the safe way to control latency.
    """
    if config.CHAT_BACKEND == "anthropic":
        resp = anthropic_client().messages.create(
            model=config.BEDROCK_CHAT_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            output_config={"effort": effort},
        )
        if resp.stop_reason == "refusal":
            raise RuntimeError("Bedrock declined the request (stop_reason=refusal)")
        return "".join(b.text for b in resp.content if b.type == "text")

    # boto3 Converse fallback — works for Nova and any other Bedrock chat model.
    resp = runtime().converse(
        modelId=config.BEDROCK_CHAT_MODEL,
        system=[{"text": system}],
        messages=[
            {"role": m["role"], "content": [{"text": m["content"]}]} for m in messages
        ],
        inferenceConfig={"maxTokens": max_tokens},
    )
    return "".join(
        block.get("text", "") for block in resp["output"]["message"]["content"]
    )
