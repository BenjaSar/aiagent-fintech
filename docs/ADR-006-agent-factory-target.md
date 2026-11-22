# ADR-006 — agent factory: target class, scope and approval model

2026-11-20. **Status: accepted for engineering, pending the legal review in §7.** Decided by the project owner on 2026-11-20, answering the four questions raised by the audit of the same day. This record belongs to the ADR series kept at the project root (`..\ADR-001` to `..\ADR-005`); it lives in `docs\` because the owner asked for the program documents to live here.

## 1. Context

The repository today is an **authority plane**: it decides what an agent may spend, to whom, for how long, and how the user revokes it. It is not an agent and it does not contain one. Verified on 2026-11-20: the only runtime dependency is `cryptography`, no model client appears anywhere in `src`, the agent loop and MCP tool servers are listed as not built, and the Agent Card and ReBAC code "exists but nothing calls it". In the classification used by the audit this is **C1**, with **C3** only partially present.

The owner's decision is to build a **C4** system: one that itself authors new fintech agents. Three further decisions scope it: stage 1 covers payment agents and other types follow in stage 2; approval is by a human; the program documents live in `docs\`.

This re-opens ADR-001. The custody posture (RAMA A, non-custodial software provider, user retains ultimate control) was argued for **one** user-granted session authority per user. A system that authors agents multiplies authorities and introduces a third actor — the generator — that is neither the user nor an enclave.

## 2. Decision

**D-1. Target class C4, with authorship and authority separated.** The system may author and evaluate agent definitions with no human in the loop. No definition becomes an authority without an individual human approval bound to that definition. Autonomy applies to *writing*, never to *granting*.

**D-2. Two planes, strictly separated.** The *agent plane* (generator, definitions, runtime, intents) is untrusted by construction. The *authority plane* (policy derivation, approval, FROST quorum, validator, hook, revocation) is the existing system and remains the only thing that can move value. Every intent crosses the boundary as data, never as authority.

**D-3. One smart account per generated agent.** Reasoning in §4. The authority plane is single-tenant in all three of its layers today, and this is the only path that does not modify the validator or the hook.

**D-4. Budget by funding, not by a new cap mechanism.** The user's treasury account holds the funds; each agent account is funded with exactly its approved allowance. The hook's rolling caps limit the *rate*; the account balance limits the *total*. No new on-chain budget feature is built. This supersedes the "hierarchical budgets in the hook" remediation proposed in the audit.

**D-5. Stage 1 is payment agents only** — outbound native and ERC-20 payments to approved payees, under caps, inside a window. Analysis, treasury, trading and x402 settlement are stage 2 and require an extension to this record.

**D-6. One human approval per agent definition version.** Batch approval is rejected: this approval is the mechanism that keeps the custody argument intact and bounds the blast radius of a bad generation.

**D-7. No code generation in stage 1.** A definition is declarative data whose capabilities are drawn from a reviewed tool catalog. Generated code would need a sandbox and escape tests before it could be considered.

## 3. New invariants

These extend I-1 to I-8 in `..\IMPLEMENTATION-PLAN.md` §1.4. Violating any one of them moves the project to RAMA B, on the same terms as the existing eight.

| # | Invariant |
|---|---|
| I-9 | No agent definition becomes an on-chain authority without an individual user approval bound to the triple (definition digest, derived policy digest, session public key) |
| I-10 | Neither the generator nor the agent runtime holds key material of any kind, root or session, and neither can submit a `UserOperation`. Extends I-5 from root keys to session keys and to the generator |
| I-11 | Nothing a model writes or reads grants authority. Capabilities come only from the tool catalog, limits only from the derived policy, and a human reviews both |
| I-12 | Every generated agent is individually revocable by the user alone, on-chain, without company cooperation; a fleet-wide halt has the same property. Extends I-2; depends on T-G1 |
| I-13 | The total authority of all active agents is bounded by what the user funded. No generation, iteration or re-evaluation can raise it |
| I-14 | Every generated agent carries an append-only provenance record: task specification, model identity and parameters, candidates, evaluation results, approver, deployment transaction. Extends T-G2 |

## 4. Why one account per agent

Three layers are single-tenant today, each verified in the code on 2026-11-20:

- `FrostSessionValidator` keeps `mapping(address account => SessionKey sessionKey)` and reverts with `SessionAlreadyInstalled` — **one session key per account**.
- `FintechPolicyHook` derives every scope from `keccak256(abi.encode(account, epochs[account]))` — caps, allowlist and the rolling window are **per account**.
- An enclave set accepts a session policy once and refuses a second issuance (T-2.1) — **one policy per enclave set**.

Two options follow.

**(a) Make the layers multi-tenant.** A validator keyed by (account, agent), per-agent scope in the hook, and multi-tenant enclaves. This touches the two most safety-critical contracts, invalidates their current test and mutation evidence, and needs fresh attestations.

**(b) One account per agent** (chosen). Reuse `FintechAccountFactory` with a per-agent salt. The validator and hook are untouched and keep their evidence. Per-agent accounting, per-agent revocation and blast-radius isolation come for free, it matches the spirit of I-4 (no pooled accounts), and it makes I-13 enforceable by funding alone.

Accepted costs of (b): an EntryPoint deposit and a guardian set per account; more gas; and one enclave set per agent until multi-tenancy exists, which bounds stage 1 to a small fleet (single digits on one machine).

## 5. Consequences

**Positive.** The blast radius of a bad generation is one funded account. The custody argument survives, because the user still grants each authority and can still revoke each one alone. Every agent is auditable from task specification to deployment transaction. The existing drill, mutation-testing and documentation conventions carry over unchanged.

**Negative.** The human approval rate becomes the throughput ceiling of the factory — by design, and it should be stated to anyone who expects a factory to scale freely. Payment agents still require three changes to the hook (§6 of the gap register), which is the most safety-critical contract in the repository. One enclave set per agent does not scale and defers a real multi-tenancy task. Per-account funding fragments liquidity, so a treasury-sweep procedure is needed that the project does not have.

**Regulatory.** ADR-001's decision tree keys everything to who controls the funds. I-9 and I-12 are the mechanisms intended to keep that answer "the user". Whether authoring agents changes the provider's position is the open question in §7.

## 6. Rejected alternatives

| Alternative | Why rejected |
|---|---|
| C3 — humans author agents, the system governs them | Rejected by the owner on 2026-11-20. Recorded because it remains the fallback if §7 goes against C4 |
| Batch approval of generated agents | Defeats D-6's purpose; one careless approval could mint a fleet |
| A generator with any signing path or key access | Violates I-3, I-5 and I-10 |
| Code generation in stage 1 | No sandbox and no escape tests exist (T-4.2 is unbuilt); deferred by D-7 |
| Hierarchical budgets enforced in the hook | Superseded by D-4; funding achieves the same bound with no change to the hook |

## 7. Open questions — the first is blocking

1. **Legal review (blocking Phase 1 sign-off).** Under ADR-001's decision tree, does a system that authors agents make the provider a controller rather than a software provider? I-9 and I-12 are the intended answer, since the user grants and revokes every authority. This must be reviewed by whoever owns the regulatory position, and the answer recorded here.
2. **Model provider and cost.** No model is named anywhere in the project, and IG-5 (model selection and cost budget) is still open. Generation and evaluation cost must be budgeted before Phase 4.
3. **x402 in stage 1?** Proposed: no. Machine-to-machine settlement is T-3.3 and unbuilt; stage 1 payments are plain native and ERC-20 transfers.
4. **Payee provenance.** Who may become an approved payee, and on what evidence? Stage 1 assumes the user approves each payee; a KYA-style check is out of scope here.

## 8. Related documents

- `docs/ADR-007-stage-2-agent-types.md` — the accepted extension to D-5: which stage 2 types open, in what order, and on what gates. Accepted on 2026-11-21. D-5 now reads with it: the analysis type is open and holds no authority; treasury, trading and x402 remain closed on the gates named there.
- `docs/agent-factory-gap-register.md` — every gap between today's system and this target, with remediations.
- `docs/agent-factory-program-plan.md` — the phased plan, gates and acceptance criteria (T-5.0 to T-5.10).
- `..\ADR-001-custody-path.md` (custody posture), `..\ADR-002-frost-session-authority.md` (session authority), `..\IMPLEMENTATION-PLAN.md` (invariants, task table, open inputs).
