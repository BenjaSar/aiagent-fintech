# Step 7 — local bundler

2026-11-16. Alto (Pimlico's TypeScript bundler) runs against the local anvil chain and reports the v0.8 EntryPoint.

## Result

`eth_supportedEntryPoints` returns `0x4337084D9E255Ff0702461CF8895CE9E3b5Ff108` on chain id 31337 (checked by `scripts/check_local_bundler.py`, exit code 0). The fallbacks in the plan (Transeptor, Skandha, Voltaire) were not needed.

## Setup

| Item | Choice |
|---|---|
| Package | `@pimlico/alto` 0.0.21, exact pin, installed with npm under `.local\bundler` (untracked); no global install |
| Runtime | Node 22.15, started as `node …\alto.js` so the pid file refers to the real process (`taskkill /T` on stop) |
| Mode | `--safe-mode false`, `--eip-7702-support false` (the chain runs cancun, not prague) |
| Keys | A fresh executor key and a fresh utility key per checkout, generated into `.local\bundler\*.key` and funded with 100 ETH through `anvil_setBalance`. Alto refuses to start without the utility key because it deploys its simulation contracts with it |
| Guards | `start-local-bundler.ps1` refuses to run unless the RPC reports chain id 31337 and the EntryPoint code exists |

## Scripts

| Script | Purpose |
|---|---|
| `scripts/install-local-bundler.ps1` | Pinned npm install under `.local\bundler` |
| `scripts/start-local-bundler.ps1` | Fund the keys, start Alto on port 4337, wait for the v0.8 entry point |
| `scripts/stop-local-bundler.ps1` | Kill the process tree |
| `scripts/check_local_bundler.py` | Assert the v0.8 EntryPoint and chain id |
| `src/fintech_agent/adapters/bundler_rpc.py` | Minimal JSON-RPC client (entry points, chain id, generic call) for step 8; 8 tests against a fake server |

Order: `start-local-chain.ps1`, then `start-local-bundler.ps1`.

## Not verified

- **Bundling was verified in step 8** (`docs/local-end-to-end-drill.md`): Alto accepted and included owner and session operations for the v0.8 account.
- **Safe mode is off**, so ERC-7562 rules are not enforced. The slot-position tests cover only the storage rule; a bundler with tracing or Base Sepolia is still needed to confirm the validator passes.
- **`npm install` reported 43 advisories** in Alto's dependency tree (35 moderate, 8 high). The bundler is a development tool bound to the local chain and holds only throwaway keys; do not run this install on a machine or network where that matters, and do not reuse the keys on any real chain.
- The Alto version is a 0.0.x release; behaviour on other versions is untested.
- `start-local-chain.ps1` can stall when its output is piped (the background anvil keeps the pipe open); run it without piping.
