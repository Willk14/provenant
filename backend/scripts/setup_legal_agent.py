"""Create the contract review agent and its environment once. It makes billable resources, so run it deliberately.

    python scripts/setup_legal_agent.py

Credentials come from ANTHROPIC_API_KEY or an `ant auth login` profile. Set the two printed ids in the worker's
environment with PROVENANT_LEGAL_AGENT=claude. The agent has only Provenant's custom tools: the built-in shell, file
and web tools are off, and the environment has no outbound network.
"""
import anthropic

from provenant.legal.agent import MODEL, SYSTEM
from provenant.legal.toolbox import TOOLS


def main() -> None:
    client = anthropic.Anthropic()
    environment = client.beta.environments.create(
        name="provenant-contract-review",
        config={"type": "cloud", "networking": {"type": "limited", "allow_package_managers": False,
                                                "allow_mcp_servers": False}},
    )
    agent = client.beta.agents.create(
        name="Provenant contract review",
        model=MODEL,
        system=SYSTEM,
        tools=[{"type": "custom", **tool} for tool in TOOLS],
    )
    print(f"PROVENANT_LEGAL_AGENT_ID={agent.id}")
    print(f"PROVENANT_LEGAL_ENVIRONMENT_ID={environment.id}")


if __name__ == "__main__":
    main()
