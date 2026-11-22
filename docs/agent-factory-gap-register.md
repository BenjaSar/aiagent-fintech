# Agent factory — gap register

2026-11-20. Every gap between the system as built and the target in `docs/ADR-006-agent-factory-target.md`: a C4 system that authors fintech agents, stage 1 payment agents, one human approval per agent. Each row names a remediation and the task that owns it. Task IDs `T-5.x` are proposed and need a row in `..\IMPLEMENTATION-PLAN.md` §4, which is not edited here.

Severity: **Critical** blocks the target or breaks an invariant; **Major** blocks a phase gate; **Minor** is a quality or scale limit.

## 1. Baseline, corrected

The audit of 2026-11-20 first reported the verdict "Partially" for agent-generation capability. That verdict rested on treating a session policy, FROST key and hook cap as the "configs" of an agent. A spend-permission grant for an external agent is not an agent definition, and under the strict reading the correct verdict is:

- **No G criterion is present or partial. Verdict: No.** The system does not author agents.
- Class **C1** structurally (a fixed, developer-defined sequence with no model-driven control flow), with **C3** partially present (session-authority management plus an agent registry that nothing calls). None of C1–C4 fits cleanly, because no model inference step is documented or present at all.
- Confidence: high that C4 is absent, because the README states the relevant components are unbuilt; lower for "no model-driven behaviour anywhere", which rests on silence.

Everything below is measured against that baseline.

## 2. Headline: the authority plane is single-tenant in all three layers

| ID | Gap | Severity | Evidence (verified 2026-11-20) | Remediation | Task |
|---|---|---|---|---|---|
| D23a | The validator holds one session key per account and refuses a second install | Critical | `mapping(address account => SessionKey sessionKey)`, `revert SessionAlreadyInstalled()` in `contracts/FrostSessionValidator.sol` | Do not change the validator. One account per agent (ADR-006 D-3), provisioned through the existing factory with a per-agent salt | T-5.1 |
| D23b | Caps, allowlist and the rolling window are scoped per account, not per agent | Critical | `scopeOf` returns `keccak256(abi.encode(account, epochs[account]))` in `contracts/FintechPolicyHook.sol` | As above; per-agent accounting then comes for free | T-5.1 |
| D23c | An enclave set accepts a session policy once and refuses a second issuance | Major | T-2.1 §5 and the drill check "owner issues the session policy to the three enclaves, once" | Closed by T-5.9: a tenancy per agent inside one process set, each with its own token, key share, policy and ledger; the single-issuance rule now holds per tenancy | T-5.1, T-5.9 |

Until T-5.1 exists, nothing in the repository can hold more than one agent authority, so no fleet is possible regardless of what the generator produces.

## 3. Payment-specific gaps (stage 1 blockers)

These are the gaps that stage 1's choice of **payment** agents creates. All three touch the hook, the most safety-critical contract in the repository.

| ID | Gap | Severity | Evidence | Remediation | Task |
|---|---|---|---|---|---|
| D20 | **No recipient allowlist for ERC-20 transfers.** A capped token may be sent to any address; the recipient is only screened against the sanctions oracle | Critical | `accountForToken` calls `requireNotSanctioned(tokenCounterparty(...))` and then only caps the amount; no allowlist check. Already recorded as a known gap in T-2.1 and T-2.2 | Add a per-scope payee allowlist to the hook, checked for the decoded `transfer` recipient. Derive it from the agent definition's approved payees | T-5.2 |
| D18 | **No call-shape restriction per target.** An allowlisted target may be called with any function and any data, and a capped token accepts `transfer` and `approve` equally under one cap | Critical | `isTransferOrApproval` admits both selectors; `_movements_of` places no constraint on `execution.data` for an allowlisted target. The T-2.6 screen states this honestly: "Call any function, with any data" | Add a per-scope call-shape rule: value-only with empty call data for payee targets, and a selector allowlist for capped tokens so a payment agent can transfer but never approve | T-5.2 |
| D19 | **Allowances outlive the session.** An approval granted inside a window survives expiry, revocation and retirement, so authority persists after the agent is gone | Critical | The hook caps the approval amount but keeps no record of outstanding allowances; T-2.6 already warns that an earlier allowance works "outside these caps" | For stage 1, forbid `approve` outright (D18). For later stages, keep an allowance inventory per agent and require it to be zeroed as part of retirement | T-5.2, T-5.8 |
| D21 | **No payee registry.** Nothing defines who may be a payee or records who approved them | Major | No payee concept in `domain/`; the Agent Card directory is for counterpart agents and takes a human-supplied key | Add a user-approved payee registry (address, label, approver, date) as the only source for the derived allowlist. Labels follow the Agent Card text rules so a payee name cannot spoof the approval screen | T-5.2 |
| D22 | **No payment idempotency.** The nonce stops replay of an identical operation; nothing stops an agent re-issuing a semantically duplicate payment after a timeout | Major | No idempotency concept anywhere; the project's own Phase 3 gate already requires "duplicate 402 does not double-pay" | Idempotency key per payment intent, recorded in the agent's provenance and checked by the runtime before signing; a settled key is never re-signed | T-5.5 |

## 4. Agent plane — absent entirely

| ID | Gap | Severity | Remediation | Task |
|---|---|---|---|---|
| D1 | No agent definition artifact or schema; nothing represents an agent, so attestation, approval and provenance have nothing to bind to | Critical | `domain/agent_definition.py`: identity, model and parameters, system prompt, declared tools, payees, assets and caps, window, risk tier, owner. Canonical serialization plus SHA-256 digest, reusing the Agent Card canonical-body and policy-digest pattern | T-5.3 |
| D2 | No generator, and no model client anywhere | Critical | `application/agent_synthesis.py` behind a port: a task specification in, candidate definitions out as inert data. Holds no keys, submits nothing (I-10) | T-5.6 |
| D3 | No tool catalog; generated capabilities would be free text | Critical | `domain/tool_catalog.py`: per-tool schema, risk tier and the policy constraints each tool forces. A definition may reference catalog entries only | T-5.3 |
| D4 | No agent runtime; the agent loop and MCP tool servers are unbuilt, so a definition could not be instantiated | Critical | A minimal runtime that consumes a definition and emits intents only, behind the existing signing coordinator, with the stage 1 payment tools as MCP servers (depends on T-3.2) | T-5.5 |
| D13 | No sandbox for generated artifacts | Major | Keep generation declarative (ADR-006 D-7). If code generation is ever considered, a sandbox plus escape tests in the style of T-4.2 is the gate | deferred |

## 5. Authority plane — not fleet-shaped

| ID | Gap | Severity | Remediation | Task |
|---|---|---|---|---|
| D5 | No derivation of authority from an agent, and no binding between them. Policies are hand-written standalone objects | Critical | `derive_session_policy(definition)`; extend the T-2.6 screen to show definition and derived policy together; extend T-2.7's fingerprint from (policy digest, key) to (definition digest, policy digest, key) so an approval cannot be replayed onto another agent (I-9) | T-5.3, T-5.4 |
| D9 | Rotation spawns a new three-process enclave fleet per session, and nothing schedules it | Major | Closed by T-5.8 and T-5.9: an expiry scheduler renews a due session, and a rotation now admits a fresh tenancy on the existing process set and evicts the old one instead of spawning three more processes. Nothing runs the scheduler on a timer | T-5.8, T-5.9 |
| D10 | No bound on total authority across agents | Critical | Superseded by ADR-006 D-4: fund each agent account with its approved allowance; the balance is the bound (I-13). No hook change | T-5.1 |
| D15 | No halt control; a fleet needs per-agent and fleet-wide stops | Major | Closed by T-5.9: the enclaves enforce a fleet halt and a per-agent halt, ordered by writing to their own storage with no service in the path, and the owner still revokes a halted agent through a self-submitted `handleOps` | T-5.9 |
| D8 | Identity assumes a human registrar: `Registration` takes a human-supplied public key and card, the directory is unwired, and ERC-8004 identity is unbuilt | Major | Add a provenance field (human-registered versus generated, with the generation id), require an attestation over the definition digest, and keep the pin-and-quarantine mechanics, which are the right defence for a fleet | T-5.4 |

## 6. Evaluation and improvement — absent

| ID | Gap | Severity | Remediation | Task |
|---|---|---|---|---|
| D6 | No evaluation of generated agents; current testing evaluates the pipeline, by humans | Critical | `evaluation/`: a payment scenario bank seeded from ADR-004's case bank, an injection and adversarial suite feeding T-V3, deliberate policy-violation attempts, duplicate-payment attempts, and latency and cost budgets, scored against a rubric with a hard promotion gate | T-5.7 |
| D7 | No closed loop from evaluation back to a revised definition | Critical | An `AgentLifecycle` state machine (draft, evaluated, approved, active, retired) with bounded iterations and a monotonic rule: no automatic iteration may widen authority; widening needs a fresh human approval (I-9, I-13) | T-5.8 |
| D17 | No reproducibility for a non-deterministic component; the repository's conventions assume determinism | Major | Pin model id and sampling parameters in the definition and the provenance; score evaluations over repeated runs with variance reported; apply mutation testing to the guard rails rather than to generated prose | T-5.6, T-5.7 |

## 7. Governance — the binding constraint for fintech

| ID | Gap | Severity | Remediation | Task |
|---|---|---|---|---|
| D11 | The custody posture was argued for one user-granted session, not for machine-authored agents | Critical | ADR-006 §3 states I-9 to I-14; its §7 legal review is the gate. No Phase 1 sign-off without it | T-5.0 |
| D12 | No decision audit trail (R-NEW-7) and no multi-agent observability (R-NEW-14) | Critical | Append-only signed provenance per agent (I-14), exportable as an audit artifact; trace the interaction, not only each agent's output | T-5.4, T-5.9 |
| D14 | The injection surface widens: a generator reading specifications, payee data or counterpart cards inherits prompt injection plus data poisoning (R-NEW-10) | Critical | Generalize the rule already proven for Agent Cards — text never grants authority — to every generator input and output (I-11). Nothing a model produces or reads becomes authority without passing the catalog, the derivation and the human approval | T-5.6, T-5.7 |
| D16 | No cost control for generation or evaluation | Major | Per-generation and per-evaluation budgets with a hard stop, reported next to the evaluation score; closes part of IG-5 | T-5.8 |

## 8. Status

Closed by T-5.9 on 2026-11-21: **D15 and T-G1** (a halt switch the enclaves enforce themselves, for the whole fleet and for one agent, ordered by writing to their own storage so no company service is in the path, with the owner still able to revoke through a self-submitted `handleOps` while everything is halted), **D23c and D9's one-set-per-agent half** (a tenancy per agent inside one process set, isolated by per-tenancy tokens and sealed storage, admitted and evicted without touching the other tenancies) and **D12's multi-agent observability half (R-NEW-14)** (a causal trace whose reconstruction crosses between agents, measured against what each agent's own record would show). **What stays open**: nothing runs the scheduler on a timer, the trace is in memory and is not part of the signed provenance chain, and a shared process set means a compromise of one process now reaches every tenancy on it.

Closed by T-5.8 on 2026-11-21: **D7** (the closed loop from evaluation back to a revised definition, with the state machine and the bounded iteration it asked for), **I-9 and I-13** (no automatic iteration or renewal can widen an agent's authority, enforced by a definition-level comparison the live path runs on every iteration and every renewal), the **lifecycle and bounded-iteration half of D16** (attempt and cost ceilings per development loop, on top of T-5.6's per-generation ceiling) and the **retirement half of D19** (a retired agent's unspent allowance is swept back to the treasury and the ledger freed; the inventory of outstanding ERC-20 approvals that D19 also asks for is still out of scope, because stage 1 forbids `approve` outright). **What stays open**: nothing drives the scheduler on a timer, the lifecycle is in memory only, and an unattended renewal installs a session key the owner never saw (recorded on the chain with the scheduler as approver).

Closed by T-5.7 on 2026-11-21: **D6** (an evaluation harness with seven checks and seven scenarios that drive the candidate's runtime against the real enclave-side authorizer, and a promotion gate nothing reaches the owner without) and **D12's evaluation half** (the result is on the provenance chain before any screen is built). **D6 is narrower than it looks**: the scenarios judge what a candidate's limits permit, not what a model would decide, and the adversarial suite against a model-driven decider waits for one to exist.

Closed by T-5.6 on 2026-11-21: **D2 in structure** (a generator port with a gate that refuses any proposal beyond the owner's task specification, proved against a hostile proposer rather than a well-behaved model), **D14's generation half** (nothing a proposal writes or reads becomes authority: the ignore-your-limits case is a test) and **D16** (a cost ceiling in money and tokens, enforced and recorded). **D2 is not fully closed**: no model client exists, and IG-5 and ADR-006 §7.1 gate writing one. **D17 is half closed**: the model and its sampling parameters are pinned by the owner and recorded, and variance over repeated runs is reported, but nothing non-deterministic has been measured.

Closed by T-5.5 on 2026-11-21: **D4** (there is now an agent runtime: a bounded loop, a tool boundary and a decider port, with its isolation from any signer checked by walking its imports) and **D22** (payment idempotency, through a journal that refuses a second payment under one key and a sink that confirms the money moved). **D13 stays deferred**: generation is still declarative, so no sandbox is needed yet. The MCP dependency in §9 is unchanged — the tools are shaped for it but nothing speaks it.

Closed by T-5.4 on 2026-11-21: the rest of **D5** (one owner approval now binds the definition digest, the derived policy digest and the session key, and nothing is installed without it) and **D12's provenance half** (an append-only, verifiable chain per agent holding the definition, the approval and the installation). **D8 stays half open**: our own agents have identity through their definition digest and account, but the Agent Card directory is still for counterparts and still unwired, and ERC-8004 identity is unbuilt. D12's multi-agent observability half (R-NEW-14) is also still open, in T-5.9.

Closed by T-5.3 on 2026-11-20: **D1** (an agent definition artifact with a canonical document and digest), **D3** (a tool catalog that bounds declared capabilities) and **D21** (a payee registry that records the approver and the approval time, brought forward from T-5.4). **D5 is half closed**: a policy is now derived mechanically from a definition, but nothing yet binds a definition, its policy and a session key into one human approval — that is T-5.4.

Closed by T-5.2 on 2026-11-20: **D20** (a payee allowlist, enforced on chain and by the enclaves), **D18** (no call data on a payment, and `transfer` only for capped tokens) and **D19** (`approve` forbidden outright in a payments-only scope, so no allowance can outlive the session). **D21 stays open and moves to T-5.4**: the hook enforces payment destinations, but nothing records who approved a payee or when. **D22** (payment idempotency) is unchanged and belongs to T-5.5.

Closed by T-5.1 on 2026-11-20: **D23a** and **D23b** (one account per agent, so the single-tenant validator and hook scopes no longer bind), **D10** (the approved total is enforced by funding and by the fleet ledger). **D23c** stands and is accepted for stage 1: one enclave set per agent. Everything else in this register is open; the stage 1 blockers in §3 are next (T-5.2).

## 9. Carried over from the existing plan

These were already open before this program and now block it. They are listed so the program does not silently assume them.

| Item | Why it blocks the factory |
|---|---|
| T-3.2 MCP tool servers | The runtime's only sanctioned way to give an agent tools (D4) |
| ~~T-G1 halt~~, T-G2 audit trail | T-G1 is built (T-5.9): the enclaves enforce a fleet and a per-agent halt. T-G2's signed, exportable audit artifact is still open, and the interaction trace is not part of it |
| T-V2 audit, T-V3 adversarial suite | The hook changes in §3 are safety-critical and unaudited; T-V3 is the falsification suite for the whole guardrail set |
| Base Sepolia execution, a bundler enforcing ERC-7562, remote attestation | Everything to date is local with simulated attestation and safe mode off |
| IG-5 model and cost, IG-8 attester, IG-13 oracle provider, IG-14 bundler and paymaster | A factory multiplies every one of these dependencies |
