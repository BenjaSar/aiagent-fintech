import argparse
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from fintech_agent.composition.local_enclaves import (
    serve_enclave_forever,
)

DEFAULT_ROOT = REPOSITORY_ROOT / ".local" / "enclaves"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one local enclave process.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--identifier", type=int, required=True)
    arguments = parser.parse_args()
    try:
        serve_enclave_forever(arguments.root, arguments.identifier)
    except KeyboardInterrupt:
        return 0
    return 0


raise SystemExit(main())
