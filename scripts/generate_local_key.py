import argparse
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from fintech_agent.application.run_key_generation import KeyGenerationFailedError
from fintech_agent.composition.local_enclaves import generate_key_with_enclaves

DEFAULT_ROOT = REPOSITORY_ROOT / ".local" / "enclaves"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the distributed key generation across the running local "
        "enclave processes. No process ever holds the full key."
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    arguments = parser.parse_args()
    try:
        group = generate_key_with_enclaves(arguments.root)
    except KeyGenerationFailedError as failure:
        print(f"key generation failed: {failure}", file=sys.stderr)
        return 2
    print("group public key:", group.configuration.group_public_key.serialize().hex())
    return 0


raise SystemExit(main())
