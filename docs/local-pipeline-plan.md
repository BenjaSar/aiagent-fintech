# Plan — T-1.3 policy scope, and T-1.1 / T-1.2 on a local pipeline

Proposed and approved 2026-11-16 with the recommended answers D1 to D6. No duration or effort figures appear, as in the main plan.

## Progress

| Step | State |
|---|---|
| 1 Local chain and EntryPoint etch | Done: `scripts/start-local-chain.ps1`, `stop-local-chain.ps1`, fixtures in `fixtures/base-sepolia/`, 4 smoke tests |
| 2 TTL in the validator | Done: 42 Solidity tests pass |
| 3 Account and factory spike | Done: OpenZeppelin account chosen; see `docs/T-1.1-account-and-factory.md`; 53 Solidity tests pass |
| 4 Enclave signer primitives | Done: nonce generation, commit and signature share match the RFC E.5 vectors |
| 5 Key generation | Done: dealer with RFC vectors (development use only) and the DKG; see `docs/T-1.2-dkg.md`; 99 Python tests pass |
| 6 Ports, coordinator, enclave processes | Done: ports, `EnclaveNode`, `SignUserOperation`, DPAPI sealing, localhost HTTP, three real processes, and the DKG run through the enclaves with an authenticated encrypted round two and a cryptographic round one echo check; see `docs/T-1.2-enclave-signing.md` |
| 7 Local bundler | Done: Alto 0.0.21 serves the v0.8 EntryPoint and bundled the drill operations; see `docs/local-bundler.md` |
| 8 End-to-end drill | Done: `scripts/run-local-drill.ps1`, first six checks (extended in step 9 to 13); see `docs/local-end-to-end-drill.md` |
| 9 Revocation, paymaster, recovery drills | Done: social recovery module, verifying paymaster, owner rotation; 13-check drill passes; see `docs/step-9-revocation-paymaster-recovery.md` |
| 10 Policy hook (T-2.2) | Done: `contracts/FintechPolicyHook.sol`, session entry point in the account, 17-check drill passes; see `docs/T-2.2-policy-hook.md` |
| 11 ERC-7484 registry and install gate (T-2.4) | Done: `contracts/FintechModuleRegistry.sol`, gate in the account's install path, 19-check drill passes; see `docs/T-2.4-module-registry-and-install-gate.md` |
| 12 Enclave-side re-validation (T-2.1) | Done: enclaves recompute the operation hash, validate against a sealed policy and keep a spend ledger; 22-check drill passes; see `docs/T-2.1-enclave-revalidation.md` |
| 13 Sanctions screening (T-2.3) | Done: the hook screens targets and token counterparties against an immutable oracle and fails closed; 24-check drill passes; see `docs/T-2.3-sanctions-screening.md` |
| 14 ReBAC engine and Agent Card defences (T-2.5) | Done off-chain (not part of the on-chain drill): strict card parser, pinned-key registry with typosquat and change-quarantine defences, task-scoped relationship graph; see `docs/T-2.5-rebac-and-agent-cards.md` |
| 15 Semantic approval decoder (T-2.6) | Done as a library and text screen (no graphical UI, not wired to a grant yet): `domain/policy_approval.py`; see `docs/T-2.6-policy-approval-decoder.md` |
| 16 Session rotation ceremony (T-2.7) | Done locally: DKG, review, approval, policy issuance, one owner batch and verification; 27-check drill passes; see `docs/T-2.7-session-rotation.md` |
| 17 Fleet authority plane (T-5.1) | Done: one account, enclave set, policy and funded budget per agent, with a treasury that holds no session authority; 31-check drill passes; see `docs/T-5.1-fleet-authority-plane.md` |
| 18 Payment guardrails (T-5.2) | Done: a payments-only scope in the hook refuses an unapproved payee, call data and token approvals, mirrored by the enclaves and the approval screen; 33-check drill passes; see `docs/T-5.2-payment-guardrails.md` |
| 19 Definitions, catalog and derivation (T-5.3) | Done: an agent definition with a digest, a stage-one tool catalog, a payee registry, and session policies derived from them; the drill's fleet policies are now derived; see `docs/T-5.3-definitions-catalog-and-derivation.md` |
| 20 Bound approval and provenance (T-5.4) | Done: one owner approval binds the definition, its derived policy and the session key, recorded in an append-only chain; the drill installs nothing without it; 34 checks; see `docs/T-5.4-bound-approval-and-provenance.md` |
| 21 Payment agent runtime (T-5.5) | Done: a bounded loop with four tools, a payment journal for idempotency and a sink that confirms the money moved; the agent pays end to end in the drill and cannot reach a signer; 35 checks; see `docs/T-5.5-payment-agent-runtime.md` |
| 22 Generator and synthesis gate (T-5.6) | Done except the model: a task specification bounds what may be generated, and a gate refuses any proposal that oversteps it; the drill's agents are generated; 36 checks; see `docs/T-5.6-generator-and-synthesis-gate.md` |
| 23 Evaluation harness (T-5.7) | Done: seven checks and seven scenarios that run the real enclave-side authorizer, with a hard promotion gate and the result written to the provenance chain before the owner sees a screen; 37 checks; see `docs/T-5.7-evaluation-harness.md` |
| 26 Analysis agents (T-5.10) | Done: a second agent type that holds no authority at all, opened by ADR-007 (accepted 2026-11-21); the definition, the task specification, the synthesis gate, derivation, evaluation and the approval screen all refuse to give it a payee, a cap, a budget, a policy or a session key, and it reads and reports without an account; 46 checks; see `docs/T-5.10-analysis-agents.md` |
| 25 Fleet operations (T-5.9) | Done: a halt switch the enclaves enforce for the fleet and for one agent, ordered by writing to their own storage and effective with every service down; multi-tenant enclaves, so the fleet runs on one set of three processes with a tenancy per agent and per renewal; a causal interaction trace across agents; and a renewal scheduler that renews every due session and escalates the rest; 45 checks; see `docs/T-5.9-fleet-operations.md` |
| 24 Closed loop and lifecycle (T-5.8) | Done: a state machine per agent, a bounded development loop whose iterations may never widen authority, an expiry scheduler driving the T-2.7 ceremony unattended for a renewal of the same authority, and a verified teardown that leaves no key, allowance or enclave; 41 checks; see `docs/T-5.8-closed-loop-and-lifecycle.md` |


Local environment checked today: Windows 11, 8 logical cores, 47.8 GB RAM, Docker 28.4, WSL 2.5.9, Node 22.15, pnpm 10.13, Python 3.12, Foundry 1.9.0 (`anvil`, `cast`, `forge`). No Rust or Go. Free space: C: 123 GB, F: 689 GB, so everything new goes under `F:\IA\IAFinTech\fintech-agent\.local\` (untracked, added to `.gitignore`). Docker is avoided because its images and volumes live on its own virtual disk, location not checked.

## Part 1 — T-1.3: which policy belongs in the validator

Performance reasoning uses fixed EVM costs (cold storage read 2,100, first write to a slot 22,100, rewrite 5,000) plus the 17.5k warm gas measured for the current validator. The option costs below are estimates, not measurements.

| Option | What the validator checks | Cost relative to today | Verdict |
|---|---|---|---|
| A. Signature only (current) | Quorum signature over `userOpHash` | 17.5k warm | Safe but no expiry on the session key |
| **B. A plus TTL** | Also `validAfter`/`validUntil`, returned in the packed `validationData` so the EntryPoint enforces the clock | About zero: both 6-byte values pack into the same storage slot as the key prefix, so no extra read; a few bit operations | **Recommended for T-1.3** |
| C. B plus cap, allowlist and 24-hour window in the validator | Decode the ERC-7579 execution calldata, read and write spend counters | At least one more cold read; each counter write 5,000 to 22,100; calldata decoding on top | Not recommended |
| D. Cap, allowlist, window in the T-2.2 policy hook | Same checks at execution time | Paid once per execution, outside the validation gas limit | Recommended home for C's checks |

Why C belongs in the hook: validation runs three times (bundler simulation, bundle re-validation, on-chain), and the ERC-7562 rules ban `TIMESTAMP` there, so a rolling window cannot be computed in the validator anyway. Spend counters written during validation also race across operations in one bundle. The plan currently lists the cap and allowlist in both T-2.1 (validator) and T-2.2 (hook), so adopting B and D means amending T-2.1's wording. That is your call.

If B is adopted, install data grows to the 33-byte key plus two 6-byte times (45 bytes), packed as `uint8 prefix`, `uint48 validAfter`, `uint48 validUntil` in slot one and `x` in slot two. This keeps the ERC-7562 layout.

## Part 2 — T-1.1 and T-1.2 locally

### Finding that changes T-1.2

RFC 9591 specifies only trusted-dealer key generation (Appendix C); its security section says shares come from a dealer "or through a distributed key generation protocol" without defining one. The project's notes assumed the RFC covers DKG (item B-6). A real DKG needs another specification, for example the FROST paper's Pedersen-style DKG, which is not in the project.

### Local architecture

| Layer | Local choice | Why |
|---|---|---|
| Chain | `anvil` 1.9.0, chain id 31337, `--hardfork cancun`, state dumped to `.local\anvil-state.json` | Same EVM version as the solc target. A different chain id than Base Sepolia (84532) means a dev-signed operation can never replay on the real testnet |
| EntryPoint v0.8 | Etch the runtime code of the canonical EntryPoint and its SenderCreator at their canonical addresses with `anvil_setCode`, from fixtures captured once from Base Sepolia with hashes recorded | The canonical address uses a mined salt that is not in the repo (I tried the repo's salt and zero; neither reproduces `0x4337…`), so deploying creation code gives a different address. Etching keeps testnet-identical addresses and is offline afterwards. The code was already shown equal to the repo's v0.8.0 build apart from filled-in immutables |
| Bundler | Alto from npm or source on Node 22, `safe-mode` off, against anvil | Pimlico's local setup lists the v0.8 address in its entry points. Whether Alto serves v0.8 is unconfirmed; the first step is `eth_supportedEntryPoints`. Fallbacks: Transeptor, Skandha, Voltaire |
| Account | Spike OpenZeppelin Contracts 5.6.1 `AccountERC7579` (core, `draft-` prefix, EntryPoint-version-agnostic as far as the docs show) against writing a minimal account. We write the factory and the social-recovery module either way: OpenZeppelin has neither | OpenZeppelin avoids hand-rolling the most security-sensitive contract. No source confirms Nexus, Kernel or Safe7579 support v0.8 |
| Paymaster | OpenZeppelin `PaymasterSigner` spiked first; otherwise a minimal signature-based paymaster | Sponsors gas only (I-6). The v0.8.0 repo tag has no sample paymaster |
| Enclaves (T-1.2) | Three separate Python processes on localhost, each with its own data folder under `.local\enclaves\node-N`, shares encrypted at rest through Windows DPAPI (via `ctypes`, no new dependency) behind a `SealedStorage` port | A local process is not a TEE. It exercises the protocol and the process isolation, not hardware protection |
| Attestation | `AttestationVerifier` port with a development adapter that reports "SIMULATED" | The Phase 1 exit gate for remote attestation stays NOT RUN until real hardware exists |

### Steps, in dependency order

| Step | Work | Needs |
|---|---|---|
| 0 | Decisions D1 to D6 below | You |
| 1 | `anvil` runner script, EntryPoint etch fixtures, smoke test that `getUserOpHash` and `handleOps` work | D2 |
| 2 | T-1.3 TTL change, if D1 = B | D1 |
| 3 | Account and factory spike (OpenZeppelin vs minimal), choose, then build the factory | D3 |
| 4 | Enclave signer primitives: `nonce_generate` (H3), `commit`, `sign` (round two), checked against the RFC E.5 round-one and round-two vectors | None; can start now |
| 5 | Key generation: trusted dealer per Appendix C, labelled development-only, then a real DKG | D4 |
| 6 | Ports (`EnclaveGateway`, `SealedStorage`, `AttestationVerifier`) with in-memory adapters, the `SignUserOperation` use case with unit tests, then the localhost HTTP adapters and three enclave processes | Steps 4 and 5 |
| 7 | Local bundler up, `eth_supportedEntryPoints` test | D5 |
| 8 | End-to-end: factory creates the account, owner installs the session validator with the group key, a 2-of-3 signature passes `handleOps`, a 1-of-3 signature fails, fewer than 2 reachable enclaves halts the agent (I-8) | Steps 2 to 7 |
| 9 | Drills: owner revokes the session key through a self-submitted `handleOps` call (it is permissionless, so no company bundler is needed) with all enclave processes stopped (I-2); paymaster and social-recovery module, then the recovery drill (I-7) | Step 8 |

### Phase 1 exit criteria: what the local pipeline can and cannot cover

| Criterion | Local result |
|---|---|
| Operation bundled and executed via paymaster | Locally yes; on Base Sepolia NOT RUN |
| T-quorum verifies, T-1 rejected | Locally yes |
| No reconstructed key on disk or in memory; enclave attestation | Partly: processes never hold the full key (with a real DKG). Remote attestation NOT RUN |
| ERC-8004 identity resolves (T-1.4) | Out of scope here |
| Revocation with company infrastructure offline (I-2) | Locally yes, via self-submitted `handleOps` |
| Recovery without company (I-7) | Locally yes once the module exists |
| Fewer than T enclaves halts the agent (I-8) | Locally yes |
| Schnorr gas and latency vs R-MET-3 | Gas yes (already measured); latency on Base NOT RUN |
| ERC-7562 rule acceptance by a bundler | Not exercised locally with `safe-mode` off; the slot-position tests cover the storage rule only. A tracing node or Base Sepolia is needed |

## Decisions needed

| # | Question | Recommendation |
|---|---|---|
| D1 | T-1.3 policy scope | B now; cap, allowlist and window in the T-2.2 hook; amend T-2.1 |
| D2 | EntryPoint locally | Etch canonical code from Base Sepolia fixtures (one read-only RPC fetch of two contracts) |
| D3 | Account implementation | Spike OpenZeppelin `AccountERC7579` first (a new dependency pulled from GitHub) |
| D4 | Key generation | Dealer first, development-only; then DKG. This needs your approval to fetch the FROST paper's DKG specification into `specs/` |
| D5 | Bundler | Alto from npm/source, not Docker |
| D6 | Dev key sealing | Windows DPAPI through `ctypes` |
