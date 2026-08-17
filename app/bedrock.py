"""AWS Bedrock: embeddings via Titan, reasoning via Claude.

Two clients on purpose:
  * boto3 bedrock-runtime for Titan embeddings (no SDK wrapper needed)
  * AnthropicBedrockMantle for Claude, so the agent code uses the ordinary
    Messages API surface instead of hand-rolling InvokeModel payloads.
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
    """One-shot completion. Routes to Claude, or Nova if Claude access is pending.

    On Claude Opus 5 thinking is on by default and `max_tokens` bounds thinking
    AND the reply together, so the budget has to cover both — a tight
    `max_tokens` here does not produce a short answer, it produces an empty one.
    `effort` is the dial that keeps latency sane for the short calls this app
    makes; leaving thinking enabled avoids the disabled-thinking failure modes.
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
