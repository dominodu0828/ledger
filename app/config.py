"""Configuration, loaded once from the environment."""

import os
import pathlib

from dotenv import load_dotenv

# Anchor the .env to the project root rather than the working directory. An MCP
# client spawns the server with a cwd we do not control, and a container starts
# somewhere else again — a bare load_dotenv() silently finds nothing in both
# cases and the process dies claiming the DSN was never set. Real environment
# variables still win, which is what App Runner and ECS inject.
load_dotenv(pathlib.Path(__file__).resolve().parent.parent / ".env")


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set — copy .env.example to .env and fill it in")
    return value


COCKROACH_DSN = _require("COCKROACH_DSN")

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

# Bedrock model IDs. Claude on Bedrock carries an `anthropic.` prefix; Amazon's
# own models do not. If Claude model access is still pending, set
# BEDROCK_CHAT_MODEL=amazon.nova-pro-v1:0 and CHAT_BACKEND=boto3 — that is the
# only change needed to keep the demo alive.
BEDROCK_EMBED_MODEL = os.environ.get("BEDROCK_EMBED_MODEL", "amazon.titan-embed-text-v2:0")
BEDROCK_CHAT_MODEL = os.environ.get("BEDROCK_CHAT_MODEL", "anthropic.claude-opus-5")
CHAT_BACKEND = os.environ.get("CHAT_BACKEND", "anthropic")  # anthropic | boto3

EMBED_DIM = 1024

# Screening gate. A verdict score at or above this admits nothing.
SCREEN_THRESHOLD = float(os.environ.get("SCREEN_THRESHOLD", "0.5"))

# Trust tiers, mirrored from schema.sql for readability at call sites.
TIER_OPERATOR = 3
TIER_USER = 2
TIER_TOOL = 1
TIER_UNTRUSTED = 0
