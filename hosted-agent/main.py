"""Microsoft Foundry Hosted Agent entry point."""

from travel_agent.host import create_server


if __name__ == "__main__":
    create_server().run()

