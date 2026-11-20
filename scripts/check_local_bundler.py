import argparse
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from fintech_agent.adapters.bundler_rpc import BundlerRpcClient, BundlerRpcError

ENTRY_POINT_V08 = "0x4337084D9E255Ff0702461CF8895CE9E3b5Ff108"
LOCAL_CHAIN_ID = 31337


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check that the local bundler serves the v0.8 EntryPoint."
    )
    parser.add_argument("--url", default="http://127.0.0.1:4337")
    arguments = parser.parse_args()
    client = BundlerRpcClient(arguments.url)
    try:
        supported = client.supported_entry_points()
        chain_id = client.chain_id()
    except BundlerRpcError as failure:
        print(f"bundler check failed: {failure}", file=sys.stderr)
        return 2
    print("supported entry points:", ", ".join(supported))
    print("chain id:", chain_id)
    serves_v08 = ENTRY_POINT_V08.lower() in (address.lower() for address in supported)
    if not serves_v08 or chain_id != LOCAL_CHAIN_ID:
        print("bundler check failed: expected the v0.8 EntryPoint on chain 31337")
        return 1
    return 0


raise SystemExit(main())
