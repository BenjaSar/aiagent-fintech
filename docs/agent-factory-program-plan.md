# Agent factory — program plan (T-5.0 to T-5.10)

2026-11-20. The plan to get from the system as built to the target in `docs/ADR-006-agent-factory-target.md`: a C4 system that authors fintech agents, stage 1 payment agents, one human approval per agent. Gaps are in `docs/agent-factory-gap-register.md`.

Nothing in this plan is an estimate. The project has no start date, team, budget or velocity history, so no duration or effort figure appears, on the same terms as `..\IMPLEMENTATION-PLAN.md` §0. What does appear is **order**, **gates** and **acceptance criteria**.

## 1. Target architecture

Two planes, never mixed (ADR-006 D-2).

```
                        agent plane (untrusted)
  task specification ─► generator ─► candidate AgentDefinitions
                                            │  (inert data, digests)
                                            ▼
                                     evaluation harness ─► score + report
                                            │  promotion gate
                        ────────────────────┼──────────────────────────────
                        authority plane     ▼
                              derive_session_policy(definition)
                                            │
                        approval screen: definition + policy + key fingerprint
                                            │  ONE human approval (I-9)
                                            ▼
                     DKG for this agent ─► owner batch: create account,
                                           install validator + hook, fund
                                            │
   runtime (one agent) ─intent─► signing coordinator ─► 2-of-3 enclaves ─► bundler
                                            │                                │
                                            └── provenance (I-14) ◄──────────┘
```

**One account per agent** (ADR-006 D-3), funded with exactly its approved allowance, so the account balance is the total-authority bound (I-13) and no new on-chain budget feature is needed (D-4). The validator, the FROST quorum and the hook are unchanged in role: they remain the only things that can move value, and the agent plane can only ask.

## 2. Stage 1: what a payment agent is

A deliberately narrow profile, so that stage 1 proves the factory rather than the agent.

**Purpose.** Execute outbound payments in native currency or an approved ERC-20, to approved payees, within per-asset caps, inside a time window.

**Tool catalog for stage 1** (the only capabilities a generated definition may reference):

| Tool | Kind | Notes |
|---|---|---|
| `list_payees` | read | From the approved payee registry only |
| `get_balance` | read | The agent's own account |
| `get_payment_status` | read | By idempotency key |
| `pay_payee(payee_id, asset, amount)` | authority-bearing | Produces one intent; never signs |

No swaps, no approvals, no arbitrary contract calls, no x402 (ADR-006 §7.3).

**Agent definition** (`domain/agent_definition.py`, T-5.3). Canonical JSON, SHA-256 digest, same pattern as the Agent Card body and the policy digest:

```
schema_version, agent_type="payment", name, purpose,
model {id, temperature, max_output_tokens},
system_prompt,                       # data, never authority (I-11)
tools      [tool_catalog_id],        # must all exist in the catalog
payees     [payee_id],               # from the approved registry
assets     [{asset, cap_per_window}],
window_seconds,                      # must equal the hook's WINDOW_SECONDS
valid_after, valid_until,
budget     {asset, total},           # enforced by funding (D-4)
risk_tier, owner, provenance_id
```

**Derived policy** (`derive_session_policy`, T-5.3). Mechanical, with no free choice left to the generator:

| Policy field | Derivation |
|---|---|
| `account` | The agent's own account |
| `validator`, `hook` | The attested deployments |
| `allowed_targets` | Payee addresses (for native payments) and the token contracts of `assets` only |
| payee allowlist *(new)* | Exactly the definition's payees, checked for the decoded `transfer` recipient (D20) |
| call shape *(new)* | Value-only with empty call data for payee targets; `transfer` only for capped tokens, never `approve` (D18, D19) |
| `rules` | One `AssetRule` per asset, cap as declared |
| `window_seconds` | The hook's constant; a definition that disagrees is refused |
| `valid_after`, `valid_until` | As declared, with the clock-skew margin T-2.7 established |
| `allowed_paymasters`, `maximum_gas_cost` | Deployment parameters, not generated |

Every derived policy then passes the T-2.6 screen unchanged, so the existing refusals (effectively unlimited cap, token allowed as a target without a cap, over-long session) apply to generated agents for free.

## 3. Phases, gates and acceptance criteria

Each phase ends in a gate. A gate is a drill check, a mutation check or a signed review — the conventions already used for T-2.x. No phase starts before its predecessor's gate passes.

### T-5.0 — target and legal review *(gate: signed review)*

ADR-006 is written. The remaining work is the §7.1 legal review: does authoring agents change the provider's position under ADR-001's decision tree? Record the answer in ADR-006 §7. **Nothing after T-5.2 begins without it**, and a negative answer sends the program to the C3 fallback in ADR-006 §6.

### T-5.1 — fleet authority plane *(no contract change)* — DONE 2026-11-20

Built; see `docs/T-5.1-fleet-authority-plane.md`. The four acceptance criteria below are drill checks 28 to 31, and the drill passes 31 of 31.

Account per agent through the existing factory with a per-agent salt; treasury funding of each agent account with its approved allowance; per-agent enclave set provisioning; per-agent revocation.

*Acceptance, as drill checks:* two agent accounts exist with separate keys, caps and windows, and neither can spend the other's budget; an agent cannot spend more than its account holds, even inside its cap; revoking agent A leaves agent B working; the treasury account itself holds no session authority.

### T-5.2 — payment guardrails in the hook *(contract change)* — DONE 2026-11-20

Built; see `docs/T-5.2-payment-guardrails.md`. The payee registry (D21) was not built and moves to T-5.4, where the provenance log lives; everything else below is done and the drill passes 33 of 33.

Payee allowlist (D20), per-target call shape and selector policy (D18), `approve` forbidden for payment scopes (D19), payee registry (D21).

*Acceptance:* a transfer to a non-allowlisted payee reverts although it is inside the cap; `approve` reverts for a payment scope; a call with non-empty data to a payee target reverts; an allowlisted payee that the oracle lists still reverts (sanctions first); `onUninstall` still cannot revert; one mutation per new control is caught by at least one test; the changed hook is re-attested in the registry (T-2.4) and the drill proves an unattested build cannot install.

### T-5.3 — definition, catalog and derivation *(no model yet)* — DONE 2026-11-20

Built; see `docs/T-5.3-definitions-catalog-and-derivation.md`. The payee registry (D21) landed here rather than in T-5.4, because derivation needed it. The drill's fleet policies are now derived, and it passes 33 of 33.

`agent_definition.py`, `tool_catalog.py`, `derive_session_policy`.

*Acceptance:* a definition naming an uncatalogued tool is refused; a definition whose window disagrees with the hook is refused; a payee not in the registry is refused; the derived policy for a known definition is byte-stable; every derived policy passes the T-2.6 screen or is refused with its reason.

### T-5.4 — bound approval and provenance *(C3 complete)* — DONE 2026-11-21

Built; see `docs/T-5.4-bound-approval-and-provenance.md`. The agent directory from T-2.5 stays as it is and remains unwired: it pins the cards of **counterpart** agents, while our own agents are identified by their definition digest, their account and their session key. Giving a generated agent a served Agent Card is T-3.1 and stage 2, so **D8 is only half closed**. The drill passes 34 of 34.

Approval screen showing the definition and its derived policy; the fingerprint extended to (definition digest, policy digest, session key); the agent directory wired for the first time with a provenance field; the append-only provenance log (T-G2).

*Acceptance:* an approval cannot be replayed onto another definition or another key; a changed definition needs a fresh approval; the provenance record of an approved agent is complete and reproducible from its digest; a definition whose text contains invisible or bidirectional characters is refused by the existing Agent Card text rules.

### T-5.5 — runtime for one human-authored payment agent *(C3 in operation)* — DONE 2026-11-21

Built; see `docs/T-5.5-payment-agent-runtime.md`. **MCP was not built**: the tools sit behind an invocation boundary shaped like MCP's, but no wire protocol is implemented and T-3.2 remains unbuilt. The decider is a port filled by a scripted implementation, which is where a model would go. The drill passes 35 of 35.

The agent loop, the stage 1 tools as MCP servers (T-3.2), idempotency keys (D22), escape tests in the style of T-4.2.

*Acceptance:* a human-authored payment agent pays an approved payee end to end through the drill; the model demonstrably cannot reach key material or submit a `UserOperation` (I-10); a retried payment with the same idempotency key does not pay twice; an intent naming an unapproved payee never reaches the enclaves, and would be refused there and on chain if it did.

### T-5.6 — the generator *(G1 and G2; the first C4 step)* — DONE except the model, 2026-11-21

Built; see `docs/T-5.6-generator-and-synthesis-gate.md`. The task specification, the synthesis gate, the cost ceiling, the injection suite, the isolation walk and the reproducibility report are all in place, and the drill's agents are generated. **No model client exists**: the proposer is a port filled by a deterministic stand-in, because IG-5 and the §7.1 legal review are still open. Writing that adapter is all that remains of G2. The drill passes 36 of 36.

`agent_synthesis.py`: a task specification in, candidate definitions out, constrained to the catalog and the payee registry. Cost ceiling per generation (D16). Model id and parameters pinned and recorded (D17).

*Acceptance:* a generated definition that would exceed the catalog, the registry or the budget is rejected before it reaches a human; the injection suite cannot make a generated definition carry authority it was not given (I-11); the generator has no credential, no key and no network path to the signer; two runs with the same specification and seed produce the same digest, or the variance is reported.

### T-5.7 — evaluation harness *(G4)* — DONE 2026-11-21

Built; see `docs/T-5.7-evaluation-harness.md`. Two deviations: **ADR-004's case bank has no payment cases** (its five are trading strategies for the Phase 4 research agent), so the payment bank is new and borrows only its structure; and the **adversarial suite against a model-driven decider is not here**, because there is no model-driven decider to attack. The drill passes 37 of 37.

Payment scenario bank seeded from ADR-004's case bank; adversarial and injection suite feeding T-V3; duplicate-payment and policy-violation attempts; latency and cost budgets; a scored rubric with a hard promotion gate.

*Acceptance:* a deliberately bad candidate (over-wide payees, missing idempotency, unbounded cap) is refused promotion; a candidate that passes evaluation but fails the T-2.6 screen is still refused; evaluation results are attached to the provenance record before any human sees the candidate.

### T-5.8 — closed loop and lifecycle *(G5)* — DONE 2026-11-21

Built; see `docs/T-5.8-closed-loop-and-lifecycle.md`. Three deviations: the **scheduler is proven by its arithmetic, not by the passage of time** (advancing the chain clock desynchronises the enclaves, which re-validate against the wall clock); the **gas deposit is not swept** on retirement, only the spendable allowance; and **iteration feedback is delivered but not read**, because there is still no model. The drill passes 41 of 41.

`AgentLifecycle` state machine with bounded iteration; the monotonic rule that no automatic iteration widens authority; the expiry scheduler that triggers T-2.7 rotation per agent; allowance teardown on retirement; cost control.

*Acceptance:* an iteration that would widen caps, payees or the window is refused and escalated to a fresh human approval (I-9, I-13); the iteration budget is bounded and enforced; a retired agent has no session key, no outstanding allowance and no reachable enclave set; a rotation happens on schedule without a human for a *renewal of the same authority*, and never for a widened one.

### T-5.9 — fleet operations — DONE 2026-11-21

Built; see `docs/T-5.9-fleet-operations.md`. Four deviations: "all company services offline" is proven by **what is not in the path** (a halt written to the enclaves' own storage, a revocation by self-submitted `handleOps`) rather than by killing the bundler twice in one run; the halt stops the **signature share**, not the nonce commitment or the attestation; **nothing runs the scheduler on a timer** — the loop and its escalations exist and are tested, but there is no daemon; and a node that is **down** when a tenancy is evicted is not drilled. The drill passes 45 of 45.

Fleet halt and per-agent halt (T-G1); multi-tenant enclaves to lift the one-set-per-agent limit (D23c, D9); interaction-level observability (R-NEW-14).

*Acceptance:* a fleet halt stops every agent with all company services offline (I-12); after a halt the user can still revoke and recover; the observability trace reconstructs a multi-agent payment sequence, not just each agent's own output.

### T-5.10 — stage 2 scope extension *(gate: ADR-006 extension)* — DONE 2026-11-21

Other agent types. Each new type needs its own catalog entries, derived-policy rules, scenario bank and risk tier, and an extension to ADR-006 §2 D-5. Trading and treasury types also reopen the slippage and minimum-received gaps that T-2.2 left open.

The gate passed: `docs/ADR-007-stage-2-agent-types.md` was accepted as proposed by the owner on 2026-11-21, which opens **one** type. Built; see `docs/T-5.10-analysis-agents.md`: an analysis agent that reads and reports and holds no authority at all, with the definition, the task specification, the synthesis gate, derivation, evaluation and the approval screen each refusing to give it a payee, a cap, a budget, a policy or a session key. Three deviations: the **catalog gained no tools** (a price or a fleet-wide balance has nothing to read yet), **`get_balance` is not a reader's tool** (it has no account), and a reader is **neither renewed nor halted**, because it is not provisioned. Treasury, trading and x402 stay closed on E-4, E-5 and E-6. The drill passes 46 of 46.

Two findings from writing the extension are now settled or recorded. The monotonic rule's blind spot is **closed** (E-3): tool kinds are classified in `domain/tool_catalog.py`, `authority_order.py` asks that question instead of naming `ToolKind.PAYMENT`, and a test fails if a new kind is added without classifying it. And `payments_only` remains the hook's whole call-shape policy, with no partial setting, so any later type that needs to call a contract needs new positive controls rather than a relaxation.

## 4. Where this leaves the existing plan

- `..\IMPLEMENTATION-PLAN.md` now carries a Phase 5 row set for T-5.0 to T-5.10, the sequencing edge in §4.2, and three orderings in §4.3 (T-2.7 before T-5.8, T-3.2 before the tools cross a process boundary, T-5.0 before any model writes a definition). Edited 2026-11-21.
- The drill grew from 27 checks to 46, one or more per acceptance criterion, which is the convention this repository has followed so far.
- T-3.1 (A2A) now has a second consumer: a generated agent is exactly the kind of counterpart the Agent Card and ReBAC code was written for, and T-5.4 is where that code stops being unwired.

## 5. What to do first

1. **T-5.0's legal review.** It is the only item that can invalidate the whole design, and it costs no engineering.
2. ~~T-5.1 in parallel~~ — done on 2026-11-20.
3. ~~T-5.2 through T-5.10~~ — the program is built, except that **no model writes the proposals**, no process drives the scheduler on a timer, and three stage 2 types stay closed on ADR-007's gates. IG-5 and the §7.1 legal review remain open and both gate the model adapter, which is what is left of G2.

Everything from T-5.3 onward depends on those three.

## 6. Risks specific to this program

- **The approval step is the bottleneck by design** (ADR-006 §5). A factory that can author faster than a human can review will queue, and the queue must never be shortened by batching.
- **Three safety-critical hook changes** land on a contract whose current evidence (204 Solidity tests, mutation checks, 27 drill checks) was earned without them. Every change needs its own mutation check and a re-attestation.
- **Cost and non-determinism enter a codebase built on exact assertions.** Generation and evaluation are the first components whose output is not reproducible by construction; T-5.6 and T-5.7 must quarantine that non-determinism behind digests and budgets.
- **Everything is still local.** Base Sepolia, a bundler enforcing ERC-7562, remote attestation and an independent audit (T-V2) remain unbuilt, and a fleet multiplies the consequences of all four.
- **Liquidity fragments** across one account per agent, and no treasury-sweep procedure exists.
