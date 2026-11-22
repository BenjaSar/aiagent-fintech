# ADR-007 — stage 2 agent types (extension to ADR-006 D-5)

2026-11-21. **Status: accepted as proposed. Decided by the project owner on 2026-11-21.** All seven decisions in §4 stand as written. It belongs to the ADR series kept at the project root and lives in `docs\` for the same reason ADR-006 does.

This record exists because T-5.10 is gated on it, and accepting it opens that gate: E-2 authorises the analysis type, which `docs/T-5.10-analysis-agents.md` builds, and E-5 and E-6 keep trading and x402 closed. ADR-006 D-5 reads: *"Stage 1 is payment agents only — outbound native and ERC-20 payments to approved payees, under caps, inside a window. Analysis, treasury, trading and x402 settlement are stage 2 and require an extension to this record."* T-5.10 is that extension.

## 1. Context

Stage 1 is built. T-5.1 to T-5.9 are done: one account per agent, payment guardrails in the hook, definitions derived into policies, one owner approval bound to (definition digest, policy digest, session key), a runtime, a generator with a synthesis gate, an evaluation harness with a promotion gate, a lifecycle with bounded iteration and a monotonic rule, and fleet operations with a halt, multi-tenant enclaves and an interaction trace. The drill passes 45 of 45; 922 Python and 218 Solidity tests pass. What is still missing from stage 1 is the model itself (IG-5), the §7.1 legal review, and everything in the standing list: Base Sepolia, a bundler enforcing ERC-7562, remote attestation, T-V2's audit and T-V3's adversarial suite.

Two things follow for this record. First, **stage 1's evidence is the asset being spent**: the hook carries mutation checks, an attestation and 218 tests earned under a payments-only reading, and the most expensive thing any new type can do is invalidate that. Second, the seams where a type plugs in are now known exactly, not estimated, because the code exists. §2 measures them.

## 2. What an agent type costs in this system, measured

| Seam | Where | What it holds today |
|---|---|---|
| The type itself | `domain/agent_definition.py` | `AgentType` has one member, `PAYMENT` |
| Tool kinds | `domain/tool_catalog.py` | `ToolKind` is `READ` or `PAYMENT`; `carries_payment_authority` asks whether any tool is `PAYMENT` |
| The catalog | `domain/tool_catalog.py` | `STAGE_ONE_PAYMENT_CATALOG`: three reads and `pay_payee` |
| Derivation | `application/policy_derivation.py` | Refuses any type but `PAYMENT`; requires a paying tool and at least one approved payee; sets `allowed_targets` to payee addresses and `payments_only=True` |
| The monotonic rule | `domain/authority_order.py` | Only a `ToolKind.PAYMENT` tool counts as added authority, so an automatic iteration may add a reading tool and may not add a paying one |
| The hook | `contracts/FintechPolicyHook.sol` | `paymentScopes[scope]`: value-only with empty call data to an allowlisted payee, `transfer` only for capped tokens, `approve` refused outright |
| Evaluation | `application/agent_evaluation.py` | One payment rubric and seven payment scenarios, run against the real enclave-side authorizer |
| The payee registry | `application/payee_registry.py` | Addresses the owner approved, with the approver and the time |
| The risk tier | `domain/agent_definition.py` | An integer 1 to 3, carried into the approval screen; nothing maps a type to a tier |
| The approval screen | `domain/approval_display.py`, `approval_text.py` | Renders assets, caps, payees and the window for a human |

Three of those are load-bearing in a way that is easy to miss.

**The monotonic rule reads tool kinds, not names.** `widenings` counts an added `ToolKind.PAYMENT` tool as a widening and ignores an added `READ` one. A new kind that can move value — a swap, an approval, a settlement — is **not** authority as far as that function is concerned until it is named there. A development loop could then add it to a failing candidate without escalating, which is exactly what I-9 and I-13 forbid. Any record that adds a value-moving kind must change `widenings` in the same change.

**`payments_only` is the hook's whole call-shape policy.** Setting it false for a scope turns off the value-only rule, the empty-call-data rule and the `approve` refusal together. There is no partial setting today. A type that needs to call a contract needs new, positive controls in the hook — a target allowlist is not enough, because the dangerous part of a swap is its parameters, not its address.

**Derivation is where a type becomes authority or does not.** `derive_session_policy` is the only path from a definition to a policy, and it refuses everything that is not a payment agent. A type that holds no authority does not need that path at all; a type that holds a different authority needs its own rules in it.

## 3. The four candidates, one at a time

### (A) Analysis — reads, reports, holds no authority

**What it needs.** Read-only tools: the three stage 1 reads, and plausibly a market price, a transfer history or a balance across the fleet. No payee, no cap, no asset, no session key, no account, no funding.

**What it reuses unchanged.** The definition, the digest, the catalog, the generator and its synthesis gate, the injection suite, the provenance chain, the approval screen, the lifecycle, the trace.

**What must change.** Less than it sounds, and it is subtraction rather than addition: a definition with no paying tool and no payee is refused today by `derive_session_policy`, by `AgentDefinition._check_limits` (which requires at least one asset with a positive cap and a budget naming one of them) and by the evaluation rubric (which requires `get_payment_status`). An authority-free type needs a path where **no policy is derived, nothing is installed and nothing is funded**, and an approval screen that says exactly that.

**What it stresses.** I-9 is vacuous, because there is no authority to grant — which is itself the thing to be careful about: the approval screen must not let a reader believe they approved an authority. I-10 and I-11 apply unchanged and are the real controls here: an analysis agent reads attacker-influenced data and its output may inform a human or another agent, so the T-5.7 injection suite and the T-5.6 isolation walk are the evidence that matters. I-14 applies unchanged.

**What it costs in evidence.** No contract change, no re-attestation, no new on-chain control, nothing invalidated.

### (B) Treasury — moves the user's own funds between the user's own accounts

**What it needs.** Payments to accounts the user owns: topping up an agent account, sweeping a retired one, consolidating balances.

**What it reuses.** Nearly all of the payment type. The user's own accounts can be approved payees, so most of a treasury agent is a payment agent with a different payee set and a different purpose.

**What must change, and what it breaks.** The treasury account holds the whole approved total, and ADR-006 D-4 makes that balance the bound in I-13. A session key on the treasury would therefore hold an authority equal to the fleet's entire budget, bounded only by a rolling cap. Today the drill asserts the opposite as a property: *"the treasury account itself holds no session authority"* (check 20, since T-5.1). Opening this type as a session authority deletes a drilled invariant, and the blast radius of one bad generation stops being one funded account — which is the first sentence of ADR-006 §5.

The alternative costs almost nothing: a treasury agent that **proposes** transfers and leaves the owner to execute them keeps the treasury unkeyed, keeps check 20, and still automates the part that is tedious (deciding what to top up and when). The owner operation already exists — it is what provisioning and funding use.

**What it stresses.** I-13 directly, and I-4's "no pooled accounts" in spirit.

### (C) Trading — swaps through a router

**What it needs.** Calls to a DEX router with parameters, an `approve` for the input token, slippage protection, and a price source to judge the result.

**What must change.** All three T-5.2 guardrails: D18 (no call data on a payment), D19 (`approve` refused in a payments-only scope) and D20 (the payee allowlist, which a router is not). `payments_only` would be false for a trading scope, which turns the call-shape policy off wholesale, so the hook would need new positive controls: a router allowlist, a selector allowlist, a decoded-parameter check, a **minimum-received check in `postCheck`**, and an allowance inventory so an approval cannot outlive the session. T-2.2 recorded the gap and the reason it is still open: *"Minimum-received checks need the trade intent or a price source."* That is a new external dependency of the same kind as IG-13's oracle.

**What it costs in evidence.** A change to the most safety-critical contract in the repository, invalidating its mutation evidence and its attestation, in a project where T-V2 (independent audit) and T-V3 (the adversarial suite) are both unbuilt. The program plan already names this as a program-level risk.

**What it stresses.** I-13 (a swap changes which assets the account holds, so a cap in one asset no longer bounds the total), and the whole of §3's reasoning about the hook being the thing that cannot be bypassed.

### (D) x402 settlement — paying machines per request

**What it needs.** Payment to a counterparty chosen at request time, per request, without an owner approval for that counterparty.

**What must change.** The payee registry and D20's allowlist are the mechanism stage 1 is built on; x402's counterparties are not in it by definition. It needs a per-request authorization model that does not exist, T-3.3 (unbuilt), and a fresh look at ADR-001, because "the user approved every counterparty" is part of the current custody argument. ADR-006 §7.3 already proposes "no" for stage 1 and nothing has changed since.

## 4. Proposed decisions

Each was for the owner to accept or reject on its own; all seven were accepted as written on 2026-11-21. They are numbered E for extension.

| # | Proposed decision |
|---|---|
| E-1 | **Stage 2 is never opened as a whole.** One type, one record, one gate. "Stage 2 is open" is not a decision this project takes. |
| E-2 | **The first stage 2 type is analysis, and it holds no authority.** No account, no session key, no funding, no policy derived, nothing installed. Its approval screen states in words that the owner is approving a reader, not an authority. |
| E-3 | **A tool kind that can move value is authority.** Any record that adds such a kind to `ToolKind` must, in the same change, name it in `domain/authority_order.py` so that an automatic iteration adding it is escalated, not installed. Until then no kind but `PAYMENT` may move value. |
| E-4 | **The treasury account never holds a session key.** A treasury agent proposes transfers; the owner executes them with the owner key. Drill check 20 stays as it is. |
| E-5 | **Trading is not opened before** T-V2's audit, T-V3's adversarial suite and a named price source for minimum-received checks, because it requires `payments_only=false` and new positive controls in the hook. |
| E-6 | **x402 stays closed** until T-3.3 exists and ADR-001's custody question has been re-reviewed for counterparties the owner has not approved. |
| E-7 | **An unknown `agent_type` stays a hard failure.** `agent_definition_document.py` raises on a type it does not know, which is the correct fail-closed behaviour for an older build reading a newer definition. Adding an enum member does not bump `SCHEMA_VERSION`; adding a field does. |

## 5. Consequences of accepting it as proposed

**Positive.** The factory gains a second type whose blast radius is zero and which needs no contract change, no re-attestation and no new on-chain control — so the first stage 2 step spends none of stage 1's evidence. The two expensive types stay behind named gates rather than behind a general "stage 2" flag. The monotonic rule's blind spot is closed before anything can walk into it.

**Negative.** An authority-free type needs its own path through derivation, approval and evaluation, which means the definition's required fields (at least one capped asset, a budget naming it) stop being universal: either the schema gains optional fields or the type carries its own validation. That is a real change to the artifact every later record depends on, and it should be designed once rather than twice. A reader approving an analysis agent is approving something whose risk is informational, which the approval screen was not written to express.

**Regulatory.** E-2 and E-4 keep ADR-001's answer where it is: the user grants every authority and holds the treasury. E-6 keeps the question from being reopened by the back door.

## 6. What this record does not decide

- The §7.1 legal review of ADR-006, which is still open and still blocks sign-off.
- The model and its cost (IG-5), which gates every type equally.
- Whether an analysis agent's output may be an input to a payment agent's decision. That is a composition question, and on the evidence of T-5.9's trace it is the next interesting one; it is not in scope here.

## 7. Related documents

- `docs/ADR-006-agent-factory-target.md` — the record this extends (D-5, §7.3).
- `docs/agent-factory-program-plan.md` — T-5.10 and its gate.
- `docs/T-2.2-policy-hook.md` — the slippage and minimum-received gap that (C) reopens.
- `docs/T-5.2-payment-guardrails.md` — D18, D19 and D20, which (C) would turn off.
- `..\ADR-001-custody-path.md` — the custody argument (D) reopens.
