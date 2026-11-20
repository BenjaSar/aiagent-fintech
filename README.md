# fintech-agent

A local, development-only pipeline for a non-custodial agentic wallet: an ERC-4337 smart account that an AI agent can use only through a time-limited, capped, allowlisted session key, whose signing authority is split across a 2-of-3 FROST quorum of enclave processes.

> **Status: local development pipeline. Nothing here has run on Base Sepolia or any public chain.** The enclaves are ordinary Windows processes with simulated attestation, the keys are throwaway test keys, and the contracts are unaudited. Do not reuse any key, address or script on a real network.

The plan is in `..\IMPLEMENTATION-PLAN.md` and the decisions behind it in `..\ADR-001` to `..\ADR-005`. Each task has a write-up under [docs](docs), listed at the end.

## What the pipeline does

```
user (owner key) ──grants a reviewed policy──────────────────────────┐
                                                                     ▼
agent ─intent─► signing coordinator ─► 3 enclave processes ──► FROST signature (2 of 3)
                  (Python)             each one re-checks the            │
                                       policy, keeps a spend ledger,     ▼
                                       and signs only inside it     bundler (Alto)
                                                                         │
                                                                         ▼
                                                  EntryPoint v0.8 ► smart account
                                                                     │  validator: FROST key + window
                                                                     │  hook: caps, allowlist, sanctions oracle
                                                                     ▼
                                                                  target contract
```

Two independent layers refuse bad operations. An enclave refuses to sign an operation outside the session policy; separately, the on-chain hook refuses to execute it even if a signature exists. The drill shows both.

### Components

| Part | Where | What it is |
|---|---|---|
| Smart account and factory | `contracts/FintechModularAccount.sol`, `FintechAccountFactory.sol` | ERC-7579 modular account on OpenZeppelin 5.6.1; `executeAsSession` is the only entry point for session operations |
| Session validator | `contracts/FrostSessionValidator.sol`, `FrostSecp256k1.sol` | Verifies a FROST (RFC 9591) Schnorr signature for one session key and time window |
| Policy hook | `contracts/FintechPolicyHook.sol` | Allowlist, per-asset rolling 24 h caps, token transfer and approve parsing, sanctions screening (fail closed) |
| Module registry | `contracts/FintechModuleRegistry.sol` | ERC-7484-style attestations; the account refuses to install an unattested module |
| Social recovery, paymaster | `contracts/SocialRecoveryModule.sol`, `FintechPaymaster.sol` | Guardian recovery of the owner; gas sponsorship that never confers spending authority |
| Development contracts | `contracts/dev/` | A ping target and a sanctions oracle with open setters. Local use only |
| FROST, DKG, enclave logic | `src/fintech_agent/domain/frost`, `application/` | Key generation, two-round signing, enclave-side policy re-validation and spend ledger |
| Enclave processes | `adapters/http_enclave.py`, `composition/local_enclaves.py` | Three loopback HTTP servers with DPAPI-sealed key shares |
| Approval and rotation | `domain/policy_approval.py`, `application/session_rotation.py` | Plain-language policy screen; the rotation ceremony (new DKG, review, grant, verify, retire) |
| Agent access control | `domain/agent_card.py`, `domain/relationship_graph.py`, `application/agent_directory.py` | Agent Card checks, typosquat and poisoning defences, task-scoped ReBAC. Library only, not yet called by anything |
| Drill | `composition/local_drill.py`, `scripts/run-local-drill.ps1` | The end-to-end script that exercises all of the above against a real chain and bundler |

## Requirements

Windows only (the enclave storage uses DPAPI), with:

| Tool | Version used | Notes |
|---|---|---|
| Python | 3.12 | `requires-python` is `>=3.12,<3.13` |
| Foundry (`forge`, `anvil`, `cast`) | 1.9.0 | Binaries must be in `.tools\foundry\bin`. There is no installer script for this; download the Windows release from the Foundry project and unpack it there |
| Node.js and npm | 22.15 | Only for the local bundler |
| Git | any | Used to fetch OpenZeppelin |
| PowerShell | 5.1 or later | The scripts are `.ps1` |

## One-time setup

Run from the `fintech-agent` folder.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\scripts\fetch-dependencies.ps1
.\scripts\install-local-bundler.ps1
```

- `fetch-dependencies.ps1` clones OpenZeppelin v5.6.1 into `lib\` and checks the commit hash.
- `install-local-bundler.ps1` installs Alto 0.0.21 under `.local\bundler`. It reported advisories in its dependency tree; it is a local tool that holds only throwaway keys.
- The canonical EntryPoint v0.8.0 code is already in `fixtures\base-sepolia` and is etched onto the local chain, so no network access is needed.

## Run the end-to-end drill

```powershell
.\scripts\run-local-drill.ps1
```

This is the main entry point. It stops anything left over, wipes `.local\enclaves` and `.local\rotations`, then:

1. starts a local anvil chain (chain id 31337, Cancun);
2. seals identities for three enclaves and starts them;
3. runs the distributed key generation across the three processes;
4. starts the Alto bundler against the chain;
5. runs the 27 checks in `scripts\run_local_drill.py`;
6. stops the bundler, enclaves and chain, even on failure.

Each check prints `[PASS]` or `[FAIL]` with a detail line. The exit code is 0 only if all 27 ran and passed. A run takes a few minutes.

### The 27 checks

| Area | Checks |
|---|---|
| Setup | Deploy contracts; create the account through `initCode` and install the session validator; install the policy hook |
| Enclaves | Issue the session policy once (a second issuance is refused); refuse eight kinds of out-of-policy operation before signing; refuse over-cap spend from the ledger |
| Signing | A 2-of-3 signature passes the bundler and executes; replay is rejected; one share is rejected; fewer than 2 enclaves halts signing |
| On-chain policy | Plain `execute` and self-calls are refused; the rolling native cap is enforced; a sanctioned target is refused although the enclaves signed; an oracle outage fails closed for sessions but not for the owner |
| Module install gate | An unattested module cannot be installed; a revoked attestation blocks installation again |
| Rotation | A declined rotation changes nothing; a real rotation swaps the key, window and hook cap in one owner batch; the old quorum is then rejected and the new one executes |
| Paymaster | A sponsored session operation costs the account nothing; sponsorship is not authority |
| Revocation and recovery | The owner revokes the session with all company services offline; guardians recover the account; the old owner is rejected afterwards |

## Run the tests

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.tools\foundry\bin\forge.exe test
```

Static checks used throughout:

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m ruff format --check src tests
.\.venv\Scripts\python.exe -m mypy --strict src tests
.\.tools\foundry\bin\forge.exe fmt --check
```

Last verified results (2026-11-17): 459 Python tests and 204 Solidity tests passing, `forge fmt --check` clean, and the drill passing 27 of 27 on three consecutive runs.

## Run the pieces by hand

The drill scripts can be used separately, for example to poke at a running chain:

| Script | Purpose |
|---|---|
| `scripts\start-local-chain.ps1` / `stop-local-chain.ps1` | Local anvil with the EntryPoint etched in. Run without piping the output |
| `scripts\start-local-bundler.ps1` / `stop-local-bundler.ps1` | Alto on port 4337, with its own throwaway keys |
| `scripts\provision_local_enclaves.py` | Seal enclave identities (`--identities-only`) or, for development only, deal a key with a trusted dealer |
| `scripts\start-local-enclaves.ps1` / `stop-local-enclaves.ps1` | Start or stop the three enclave processes (ports 9101 to 9103). The stop script also stops rotation sets |
| `scripts\generate_local_key.py` | Run the DKG across the running enclaves |
| `scripts\check_local_bundler.py` | Confirm the bundler reports the v0.8 EntryPoint |

Order: chain, `provision_local_enclaves.py --identities-only`, enclaves, `generate_local_key.py`, bundler. Runtime state lives in `.local\` (untracked): logs, pid files, sealed shares and throwaway keys.

## Repository layout

```
contracts/     Solidity sources (dev/ holds local-only helpers)
test/          Foundry tests
src/fintech_agent/
  domain/        pure logic: FROST, DKG, user operation hash, session policy,
                 approval screen, Agent Card, relationship graph
  application/   use cases and ports: signing, DKG, enclave node, policy
                 authorizer, rotation, agent directory
  adapters/      DPAPI storage, X25519 channel, HTTP enclave, bundler RPC,
                 Foundry toolkit, Ed25519 card signatures
  composition/   local wiring and the drill
tests/         Python tests (mirrors src)
scripts/       setup, start/stop and drill scripts
fixtures/      EntryPoint v0.8.0 runtime code captured from Base Sepolia
specs/         RFC 9591 and the ERC texts used as sources
docs/          one write-up per task
```

## What is real, simulated or missing

- **Real:** the contracts, the FROST maths and DKG, the on-chain signature check, enclave-side policy re-validation, the hook, the registry gate, rotation and recovery, and a real bundler accepting operations.
- **Simulated:** remote attestation (always "simulated"), enclave isolation (ordinary processes), the sanctions oracle (an open dev contract), the user in the rotation ceremony (a script), all keys.
- **Not run:** Base Sepolia or any public chain; a bundler with ERC-7562 rule enforcement (Alto runs with safe mode off here); any audit; any measurement of the latency targets.
- **Not built yet:** the agent loop and MCP tool servers, A2A serving, the x402 client, ERC-8004 identity, the halt control, any graphical approval screen, and the Phase 4 research agent. The Agent Card and ReBAC code exists but nothing calls it.

## Documentation

| Doc | Topic |
|---|---|
| [local-pipeline-plan.md](docs/local-pipeline-plan.md) | Step table for the local pipeline and its status |
| [local-end-to-end-drill.md](docs/local-end-to-end-drill.md) | What the drill checks, with per-update notes |
| [local-bundler.md](docs/local-bundler.md) | Alto setup and limits |
| [pipeline-options-analysis.md](docs/pipeline-options-analysis.md) | Performance measurements and options |
| [T-1.0](docs/T-1.0-version-and-provider-decisions.md), [T-1.1](docs/T-1.1-account-and-factory.md), [T-1.2 DKG](docs/T-1.2-dkg.md), [T-1.2 signing](docs/T-1.2-enclave-signing.md), [T-1.3](docs/T-1.3-session-validator.md), [T-1.8](docs/T-1.8-gas-and-latency-report.md) | Phase 1: versions, account, DKG and signing, validator, gas |
| [step 9](docs/step-9-revocation-paymaster-recovery.md) | Revocation, paymaster, social recovery |
| [T-2.1](docs/T-2.1-enclave-revalidation.md), [T-2.2](docs/T-2.2-policy-hook.md), [T-2.3](docs/T-2.3-sanctions-screening.md), [T-2.4](docs/T-2.4-module-registry-and-install-gate.md) | Enclave re-validation, hook, sanctions, install gate |
| [T-2.5](docs/T-2.5-rebac-and-agent-cards.md), [T-2.6](docs/T-2.6-policy-approval-decoder.md), [T-2.7](docs/T-2.7-session-rotation.md) | ReBAC and Agent Cards, approval screen, session rotation |

Each doc lists its own risks and limits; read them before relying on any part.

## Troubleshooting

- **`[FAIL] ... outside its time window`:** the chain clock and the machine clock differ. Session start times need a margin before chain time (the drill uses 300 s).
- **A port is busy or processes are left over:** run `scripts\stop-local-enclaves.ps1`, `stop-local-bundler.ps1` and `stop-local-chain.ps1`, then re-run the drill.
- **`start-local-chain.ps1` hangs:** it stalls if its output is piped. Run it directly.
- **Enclave start fails with a DPAPI error:** the sealed files belong to the Windows user that created them. Delete `.local\enclaves` and re-provision.
