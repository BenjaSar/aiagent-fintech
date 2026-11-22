import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from fintech_agent.adapters.bundler_rpc import BundlerRpcClient, JsonRpcClient
from fintech_agent.adapters.foundry_toolkit import FoundryConfiguration, FoundryToolkit
from fintech_agent.composition.drill_toolbox import DrillEnvironment
from fintech_agent.composition.local_drill import STEP_COUNT, LocalDrill

NODE_URL = "http://127.0.0.1:8545"
BUNDLER_URL = "http://127.0.0.1:4337"
ENCLAVE_ROOT = REPOSITORY_ROOT / ".local" / "enclaves"


def halt_enclave(root: Path, identifier: int) -> None:
    pid_lines = (root / "enclaves.pid").read_text().split()
    subprocess.run(
        ["taskkill", "/PID", pid_lines[identifier - 1], "/T", "/F"],
        capture_output=True,
        check=True,
    )


def halt_bundler() -> None:
    pid = (REPOSITORY_ROOT / ".local" / "bundler" / "alto.pid").read_text().strip()
    subprocess.run(
        ["taskkill", "/PID", pid, "/T", "/F"], capture_output=True, check=True
    )


def main() -> int:
    environment = DrillEnvironment(
        foundry=FoundryToolkit(
            FoundryConfiguration(
                REPOSITORY_ROOT / ".tools" / "foundry" / "bin",
                REPOSITORY_ROOT,
                NODE_URL,
            )
        ),
        node=JsonRpcClient(NODE_URL),
        bundler=BundlerRpcClient(BUNDLER_URL),
        enclave_root=ENCLAVE_ROOT,
        halt_enclave=halt_enclave,
        halt_bundler=halt_bundler,
        enclave_sets_directory=REPOSITORY_ROOT / ".local" / "enclave-sets",
        enclave_script=REPOSITORY_ROOT / "scripts" / "run_enclave_node.py",
    )
    completed = 0
    passed = 0
    for result in LocalDrill(environment).results():
        verdict = "PASS" if result.passed else "FAIL"
        print(f"[{verdict}] {result.name}\n        {result.detail}", flush=True)
        completed += 1
        if result.passed:
            passed += 1
    return 0 if completed == STEP_COUNT and passed == STEP_COUNT else 1


raise SystemExit(main())
