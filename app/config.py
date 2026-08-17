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

# Bedrock model IDs. Amazon's own models take no prefix; Claude on Bedrock
# carries an `anthropic.` prefix.
#
# The default reasoning model is Nova Pro, which is what the deployed demo and
# the video actually run. Anthropic models on Bedrock are gated on the AWS
# account's registered country, and this submission was built from an account
# that gate rejects — the failure is a ValidationException at invoke time, not
# a permissions problem, so it cannot be worked around in code.
#
# Where Claude IS available it is a strictly better reasoning model here, and
# switching costs two lines in .env and nothing in code:
#     BEDROCK_CHAT_MODEL=anthropic.claude-opus-5
#     CHAT_BACKEND=anthropic
BEDROCK_EMBED_MODEL = os.environ.get("BEDROCK_EMBED_MODEL", "amazon.titan-embed-text-v2:0")
BEDROCK_CHAT_MODEL = os.environ.get("BEDROCK_CHAT_MODEL", "amazon.nova-pro-v1:0")
CHAT_BACKEND = os.environ.get("CHAT_BACKEND", "boto3")  # boto3 | anthropic

EMBED_DIM = 1024

# Screening gate. A verdict score at or above this admits nothing.
SCREEN_THRESHOLD = float(os.environ.get("SCREEN_THRESHOLD", "0.5"))

# Trust tiers, mirrored from schema.sql for readability at call sites.
TIER_OPERATOR = 3
TIER_USER = 2
TIER_TOOL = 1
TIER_UNTRUSTED = 0
