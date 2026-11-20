import argparse
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from fintech_agent.composition.local_enclaves import (
    AlreadyProvisionedError,
    ProvisioningLayout,
    provision_ceremony_identities,
    provision_development_enclaves,
)

DEFAULT_ROOT = REPOSITORY_ROOT / ".local" / "enclaves"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Development only: a trusted dealer creates the key and seals "
        "one share per local enclave process."
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--threshold", type=int, default=2)
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--base-port", type=int, default=9101)
    parser.add_argument(
        "--identities-only",
        action="store_true",
        help="seal only identities, roster and tokens; the key comes from the "
        "distributed key generation (generate_local_key.py)",
    )
    arguments = parser.parse_args()
    ports = {
        identifier: arguments.base_port + identifier - 1
        for identifier in range(1, arguments.count + 1)
    }
    layout = ProvisioningLayout(arguments.root, arguments.threshold, ports)
    if arguments.identities_only:
        provision_ceremony_identities(layout)
        print("identities sealed; start the enclaves and run generate_local_key.py")
        return 0
    try:
        group = provision_development_enclaves(layout)
    except AlreadyProvisionedError as failure:
        print(failure, file=sys.stderr)
        return 1
    print("group public key:", group.configuration.group_public_key.serialize().hex())
    print("threshold:", group.configuration.threshold, "of", arguments.count)
    return 0


raise SystemExit(main())
