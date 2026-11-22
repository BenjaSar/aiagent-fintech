# fintech-agent

A local, development-only pipeline for a non-custodial agentic wallet: an ERC-4337 smart account that an AI agent can use only through a time-limited, capped, allowlisted session key, whose signing authority is split across a 2-of-3 FROST quorum of enclave processes.

> **Status: local development pipeline. Nothing here has run on Base Sepolia or any public chain.** The enclaves are ordinary Windows processes with simulated attestation, the keys are throwaway test keys, and the contracts are unaudited. Do not reuse any key, address or script on a real network.

The plan is in `..\IMPLEMENTATION-PLAN.md` and the decisions behind it in `..\ADR-001` to `..\ADR-005`. Each task has a write-up under [docs](docs), listed at the end.

The next phase turns this into an agent factory: a system that itself authors fintech agents, each one granted its authority — if it is given any — by a single human approval. The target, the gaps and the phased plan are in [ADR-006](docs/ADR-006-agent-factory-target.md), the [gap register](docs/agent-factory-gap-register.md) and the [program plan](docs/agent-factory-program-plan.md). Its first task is built: the authority plane now holds a fleet of per-agent authorities ([T-5.1](docs/T-5.1-fleet-authority-plane.md)) and those agents are restricted to paying approved payees ([T-5.2](docs/T-5.2-payment-guardrails.md)), and their authority is derived from an agent definition rather than written by hand ([T-5.3](docs/T-5.3-definitions-catalog-and-derivation.md)) and granted by one owner approval bound to that definition, its policy and its key, with an append-only record ([T-5.4](docs/T-5.4-bound-approval-and-provenance.md)). An agent also now runs: a bounded loop with four tools pays an approved payee once per idempotency key and cannot reach a signer ([T-5.5](docs/T-5.5-payment-agent-runtime.md)). Definitions are now authored through a gate that refuses any proposal beyond the owner's task specification ([T-5.6](docs/T-5.6-generator-and-synthesis-gate.md)). Every candidate is then evaluated against seven checks and seven scenarios that run the real enclave-side authorizer, and nothing reaches the owner without passing ([T-5.7](docs/T-5.7-evaluation-harness.md)). Each agent now has a lifecycle: a bounded development loop that iterates on a failed candidate but may never widen its authority, an expiry scheduler that renews the same authority unattended, and a teardown that leaves no key, allowance or enclave ([T-5.8](docs/T-5.8-closed-loop-and-lifecycle.md)). The fleet can now also be operated: a halt the enclaves enforce themselves and that the owner can order with every service down, several agents sharing one enclave set as tenancies that cannot reach each other, and a causal trace that reconstructs a payment sequence across agents ([T-5.9](docs/T-5.9-fleet-operations.md)). The factory now authors a second kind of agent: an analysis agent that reads and reports and holds no authority at all — no account, no session key, no payee, no asset and no budget ([T-5.10](docs/T-5.10-analysis-agents.md), under [ADR-007](docs/ADR-007-stage-2-agent-types.md)). What is missing is the model itself: the proposer and the agent's decisions are both deterministic stand-ins behind ports, because the model choice and the legal review are still open.

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
| Policy hook | `contracts/FintechPolicyHook.sol` | Allowlist, per-asset rolling 24 h caps, token transfer and approve parsing, sanctions screening (fail closed), and a payments-only profile with an approved-payee list |
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
5. runs the 46 checks in `scripts\run_local_drill.py`;
6. stops the bundler, enclaves and chain, even on failure.

Each check prints `[PASS]` or `[FAIL]` with a detail line. The exit code is 0 only if all 46 ran and passed. A run takes a few minutes.

### The 46 checks

| Area | Checks |
|---|---|
| Setup | Deploy contracts; create the account through `initCode` and install the session validator; install the policy hook |
| Enclaves | Issue the session policy once (a second issuance is refused); refuse eight kinds of out-of-policy operation before signing; refuse over-cap spend from the ledger |
| Signing | A 2-of-3 signature passes the bundler and executes; replay is rejected; one share is rejected; fewer than 2 enclaves halts signing |
| On-chain policy | Plain `execute` and self-calls are refused; the rolling native cap is enforced; a sanctioned target is refused although the enclaves signed; an oracle outage fails closed for sessions but not for the owner |
| Module install gate | An unattested module cannot be installed; a revoked attestation blocks installation again |
| Rotation | A declined rotation changes nothing; a real rotation swaps the key, window and hook cap in one owner batch; the old quorum is then rejected and the new one executes |
| Generation | Each agent is generated from a task specification that bounds its tools, payees, caps and window; a proposal that doubles its own cap is refused and recorded nowhere |
| Evaluation | Every candidate is evaluated before approval; one that cannot check a retry fails and is refused promotion, with nothing recorded |
| Lifecycle | A failed candidate is iterated inside an attempt and cost budget; an iteration that would widen authority is escalated instead of installed |
| Approval | Every agent is derived, approved by its owner and only then installed, with the chain of records verified and the fingerprint recomputed from the key the validator holds |
| Fleet | A treasury and two agent accounts hold separate, funded authorities; neither agent can use the other's key, policy or window; an agent cannot spend more than it was funded; revoking one leaves the other working |
| Tenancy | Three agents run on one set of three enclave processes as separate tenancies, each with its own key share, policy and ledger; a tenant presenting its own token for another tenancy is refused |
| Runtime | An agent's own loop lists its payees, reads its balance and pays the approved payee once; the same task again pays nothing more; a task naming a stranger never reaches the chain |
| Payments | A payment agent cannot pay an address its owner did not approve, and cannot attach call data to a payment; the enclaves refuse first, then the hook refuses a forced attempt |
| Renewal | One tick of the scheduler rotates every session due for renewal, unattended, when the authority is unchanged, and refuses one that would grow |
| Observability | A trace of three runs by two agents is reconstructed as one causal chain that crosses between the agents twice, which neither agent's own record shows |
| Halt | A fleet halt written into the enclaves' own storage refuses every agent and moves nothing, and both pay again once it is lifted; an agent halt stops one agent alone, and the owner still revokes a halted agent with no bundler and no enclave in the path |
| Analysis | An analysis agent is generated, evaluated and approved with no account, no session key, no payee, no asset and no budget; a generator that proposes a paying tool for it is refused, no policy can be derived for it, its screen says it holds nothing, and it reads and reports but cannot pay |
| Retirement | A retired agent keeps no session key, no allowance (swept back to the treasury) and no reachable enclave tenancy, while the rest of the fleet keeps its own |
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

Last verified results (2026-11-21): 954 Python tests and 218 Solidity tests passing, `forge fmt --check` clean, and the drill passing 46 of 46 on two consecutive runs.

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
                 approval screen, Agent Card, relationship graph, fleet identity,
                 agent definition, tool catalog, task specification
  application/   use cases and ports: signing, DKG, enclave node, policy
                 authorizer, rotation, agent directory, fleet ledger,
                 payee registry, policy derivation, provenance log,
                 agent runtime, agent tools, payment journal, agent synthesis,
                 agent evaluation, agent promotion, simulated settlement,
                 agent lifecycle, agent development, session schedule,
                 agent retirement
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

- **Real:** the contracts, the FROST maths and DKG, the on-chain signature check, enclave-side policy re-validation, the hook, the registry gate, rotation and recovery, a fleet of per-agent accounts with their own keys, caps and funded budgets, and a real bundler accepting operations.
- **Simulated:** remote attestation (always "simulated"), enclave isolation (ordinary processes), the sanctions oracle (an open dev contract), the user in the rotation ceremony (a script), all keys.
- **Not run:** Base Sepolia or any public chain; a bundler with ERC-7562 rule enforcement (Alto runs with safe mode off here); any audit; any measurement of the latency targets.
- **Not built yet:** MCP tool servers, A2A serving, the x402 client, ERC-8004 identity, the halt control, any graphical approval screen, and the Phase 4 research agent. The Agent Card and ReBAC code exists but nothing calls it.

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
| [ADR-006](docs/ADR-006-agent-factory-target.md), [gap register](docs/agent-factory-gap-register.md), [program plan](docs/agent-factory-program-plan.md) | The agent-factory target (C4, payment agents first), every gap to it, and the plan T-5.0 to T-5.10 |
| [ADR-007](docs/ADR-007-stage-2-agent-types.md) | Accepted: which stage 2 agent types open, in what order and on what gates. The analysis type is open; treasury, trading and x402 stay closed |
| [T-5.1](docs/T-5.1-fleet-authority-plane.md) | One account, enclave set, policy and funded budget per agent, with a treasury that holds no authority |
| [T-5.2](docs/T-5.2-payment-guardrails.md) | The payments-only scope: approved payees, no call data, no token approvals |
| [T-5.3](docs/T-5.3-definitions-catalog-and-derivation.md) | Agent definitions and their digest, the stage-one tool catalog, the payee registry, and policy derivation |
| [T-5.4](docs/T-5.4-bound-approval-and-provenance.md) | The agent approval screen, the fingerprint binding definition, policy and key, and the provenance chain |
| [T-5.5](docs/T-5.5-payment-agent-runtime.md) | The agent runtime: four tools, a payment journal for idempotency, and isolation from every signer |
| [T-5.6](docs/T-5.6-generator-and-synthesis-gate.md) | The task specification, the synthesis gate, the injection suite, and what is still missing without a model |
| [T-5.7](docs/T-5.7-evaluation-harness.md) | The evaluation harness: checks, scenarios against the real authorizer, and the promotion gate |
| [T-5.8](docs/T-5.8-closed-loop-and-lifecycle.md) | The lifecycle, the bounded loop, the monotonic rule, the expiry scheduler and the teardown |
| [T-5.9](docs/T-5.9-fleet-operations.md) | Fleet operations: the halt switch, multi-tenant enclaves, the interaction trace and the renewal scheduler |
| [T-5.10](docs/T-5.10-analysis-agents.md) | The analysis agent type: a second type that holds no authority, and the gates that keep it that way |

Each doc lists its own risks and limits; read them before relying on any part.

## Troubleshooting

- **`[FAIL] ... outside its time window`:** the chain clock and the machine clock differ. Session start times need a margin before chain time (the drill uses 300 s).
- **A port is busy or processes are left over:** run `scripts\stop-local-enclaves.ps1`, `stop-local-bundler.ps1` and `stop-local-chain.ps1`, then re-run the drill.
- **`start-local-chain.ps1` hangs:** it stalls if its output is piped. Run it directly.
- **Enclave start fails with a DPAPI error:** the sealed files belong to the Windows user that created them. Delete `.local\enclaves` and re-provision.
