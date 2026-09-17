"""Deploy versioned Foundry Prompt Agents from code-managed definitions."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from azure.ai.projects import AIProjectClient
from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential


ROOT = Path(__file__).resolve().parents[1]
DEFINITIONS_PATH = ROOT / "prompt-agents" / "definitions.py"
HASH_METADATA_KEY = "definition_sha256"


def _load_definitions() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "travel_prompt_agent_definitions",
        DEFINITIONS_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load definitions from {DEFINITIONS_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _definition_hash(definition: Any, description: str) -> str:
    payload = {
        "definition": definition.as_dict(),
        "description": description,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _matching_version(
    client: AIProjectClient,
    agent_name: str,
    definition_hash: str,
) -> Any | None:
    try:
        versions = client.agents.list_versions(
            agent_name,
            limit=100,
            order="desc",
        )
        return next(
            (
                version
                for version in versions
                if version.metadata.get(HASH_METADATA_KEY) == definition_hash
            ),
            None,
        )
    except ResourceNotFoundError:
        return None


def deploy(
    client: AIProjectClient,
    *,
    model: str,
    mcp_connection_id: str,
    mcp_server_url: str,
    web_iq_connection_id: str,
    web_iq_server_url: str,
) -> dict[str, str]:
    definitions = _load_definitions()
    deployed: dict[str, str] = {}
    for agent_spec in definitions.PROMPT_AGENT_SPECS:
        definition = agent_spec.build_definition(
            model,
            mcp_connection_id=mcp_connection_id,
            mcp_server_url=mcp_server_url,
            web_iq_connection_id=web_iq_connection_id,
            web_iq_server_url=web_iq_server_url,
        )
        definition_hash = _definition_hash(definition, agent_spec.description)
        existing = _matching_version(
            client,
            agent_spec.name,
            definition_hash,
        )
        if existing is not None:
            version = str(existing.version)
            print(
                f"[reuse] {agent_spec.name} version={version} "
                f"hash={definition_hash}",
                file=sys.stderr,
            )
        else:
            print(
                f"[create] {agent_spec.name} hash={definition_hash}",
                file=sys.stderr,
            )
            created = client.agents.create_version(
                agent_name=agent_spec.name,
                definition=definition,
                metadata={HASH_METADATA_KEY: definition_hash},
                description=agent_spec.description,
            )
            version = str(created.version)
        deployed[agent_spec.name] = version
    return dict(sorted(deployed.items()))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-endpoint",
        default=(
            os.getenv("FOUNDRY_PROJECT_ENDPOINT")
            or os.getenv("AZURE_AI_PROJECT_ENDPOINT")
        ),
    )
    parser.add_argument(
        "--model",
        default=os.getenv(
            "AZURE_AI_MODEL_DEPLOYMENT_NAME",
            "gpt-5.6-luna",
        ),
    )
    parser.add_argument(
        "--mcp-connection-id",
        default=os.getenv("FOUNDRY_MCP_CONNECTION_ID"),
    )
    parser.add_argument(
        "--mcp-server-url",
        default=os.getenv("MCP_TOOL_ENDPOINT"),
    )
    parser.add_argument(
        "--web-iq-connection-id",
        default=os.getenv("WEB_IQ_PROJECT_CONNECTION_ID"),
    )
    parser.add_argument(
        "--web-iq-server-url",
        default=os.getenv(
            "WEB_IQ_MCP_ENDPOINT",
            "https://api.microsoft.ai/v3/mcp",
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Also write the name-to-version JSON map to this path.",
    )
    args = parser.parse_args()
    if not args.project_endpoint:
        parser.error(
            "--project-endpoint or FOUNDRY_PROJECT_ENDPOINT is required"
        )
    if not args.mcp_connection_id:
        parser.error(
            "--mcp-connection-id or FOUNDRY_MCP_CONNECTION_ID is required"
        )
    if not args.mcp_server_url:
        parser.error("--mcp-server-url or MCP_TOOL_ENDPOINT is required")
    if not args.web_iq_connection_id:
        parser.error(
            "--web-iq-connection-id or "
            "WEB_IQ_PROJECT_CONNECTION_ID is required"
        )
    if not args.web_iq_server_url:
        parser.error(
            "--web-iq-server-url or WEB_IQ_MCP_ENDPOINT is required"
        )
    return args


def main() -> None:
    args = _parse_args()
    credential = DefaultAzureCredential(process_timeout=60)
    with AIProjectClient(
        endpoint=args.project_endpoint,
        credential=credential,
    ) as client:
        versions = deploy(
            client,
            model=args.model,
            mcp_connection_id=args.mcp_connection_id,
            mcp_server_url=args.mcp_server_url,
            web_iq_connection_id=args.web_iq_connection_id,
            web_iq_server_url=args.web_iq_server_url,
        )

    output = json.dumps(
        versions,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
