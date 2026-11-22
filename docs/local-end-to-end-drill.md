# Step 8 — local end-to-end drill

2026-11-16. One command, `powershell -File scripts\run-local-drill.ps1`, brings up the whole local pipeline, runs the drill and tears everything down. Two consecutive runs passed (about 17 seconds for the second). Redirect its output to a file rather than piping it, because anvil running in the background can keep a pipe open.

## What the script does

1. Stops anything left from an earlier run and deletes `.local\enclaves` (development data only).
2. Starts anvil (chain 31337, EntryPoint v0.8 etched), seals fresh enclave identities, starts three enclave processes, and runs the distributed key generation across them. No process holds the full key.
3. Starts Alto against anvil, then `scripts/run_local_drill.py` runs six checks and prints PASS or FAIL for each.
4. Stops the bundler, the enclaves and the chain, even if a check fails.

## Results (second run)

| Check | Result | Evidence |
|---|---|---|
| Contracts deployed, account predicted, 1 ETH deposited in the EntryPoint | PASS | Factory, validator and a ping target deployed with `forge create`; account address from `getAddress` |
| Owner operation creates the account through `initCode` and installs the session validator with the group key | PASS | Sent through Alto (estimate, sign, `eth_sendUserOperation`, receipt); `isModuleInstalled` returns true |
| 2-of-3 signature from the enclave processes passes the bundler and executes | PASS | The account called the ping target; `pings()` is 1 |
| Replayed operation rejected | PASS | `AA25 invalid account nonce` |
| Operation signed with a single share rejected | PASS | `AA24 signature error` (the drill also checks locally that the forged signature does not verify) |
| Fewer than 2 reachable enclaves halts signing (I-8) | PASS | Enclaves 1 and 2 killed; `signing halted: 1 of 2 required enclaves are available`; contract state unchanged |

The session signature is the 65-byte `R ‖ z` that the Python aggregation produces; the Solidity verifier accepts it on the real EntryPoint through the bundler, so the Python and Solidity sides agree end to end, and the key came from the DKG, not a dealer.

## How the pieces fit

- `domain/user_operation.py`: the operation, its packed gas fields and the RPC form (no I/O).
- `adapters/foundry_toolkit.py`: wraps `cast` and `forge` (ABI encoding, calls, sends, owner signing, deployment). Used only by the drill harness.
- `adapters/bundler_rpc.py`: `JsonRpcClient` plus `BundlerRpcClient` (entry points, gas estimate, send, receipt).
- `composition/local_drill.py` and `scripts/run_local_drill.py`: the six checks.
- `contracts/dev/DevelopmentPingTarget.sol`: a one-function counter used as the session call target; development only.
- 6 new unit tests for the operation value object; the drill itself is not part of `pytest` because it needs the chain, bundler and processes.

## Limits

- **Safe mode is off.** The bundler did not enforce ERC-7562 rules, so this does not show the validator would be accepted by a rule-enforcing bundler or on Base Sepolia.
- **No paymaster.** The account is prefunded in the EntryPoint. The paymaster is step 9.
- **Gas margins are fixed** (estimate plus 50k call, 150k verification, 20k pre-verification) and were not tuned or measured; no latency figure (R-MET-3) is claimed.
- **One account, one session, one call target.** No spend policy: a session operation can still call the account's own module functions until the Phase 2 hook exists (known risk in `docs/T-1.3-session-validator.md`).
- **The drill uses throwaway generated keys**; nothing in it is suitable for a real chain.
- **The halt check kills processes**; it shows that the coordinator refuses, not that an agent process reacts. The agent loop itself does not exist yet.
- **The 1-of-3 case** is a forged signature from a stolen share. The case where only one enclave is reachable is the halt check.

## Update (T-5.10)

The drill now has 46 checks and the factory authors two kinds of agent. The new check authors an **analysis** agent: a generator that proposes a paying tool for it is refused, the approved reader holds no account, no session key, no payee, no asset and no budget, showing it with a session key is refused, its fingerprint binds its definition alone, deriving a policy for it is refused, and its approval screen says in words that it holds nothing. It then reads the fleet's payees and one payment and reports, and the same definition driven by the payment decider is refused at the tool boundary with its account never touched. See `docs/T-5.10-analysis-agents.md`.

## Update (T-5.9)

The drill now has 45 checks and the fleet is operable. Three agents run on **one** set of three enclave processes as separate tenancies, each with its own key share, policy and spend ledger, and a tenant that presents its own token for another tenancy is refused. A fleet halt, written straight into the enclaves' own storage with no service in the path, refuses every agent and moves nothing; an agent halt stops one agent alone; and with the whole fleet halted the owner still revokes an agent through a self-submitted `handleOps`, with no bundler and no enclave involved. A renewal now happens through the scheduler, which renews every session due within the lead. An interaction trace of three runs by two agents is reconstructed as one causal chain that crosses between them twice, which neither agent's own record shows. See `docs/T-5.9-fleet-operations.md`.

## Update (T-5.8)

The drill now has 41 checks and every agent has a lifecycle. Provisioning runs through the development loop, so a candidate that fails evaluation is iterated inside a bounded attempt and cost budget; an iteration that would widen caps, payees or the window is escalated instead of installed; a session due for renewal is rotated unattended through the T-2.7 ceremony when the authority is unchanged, and refused when it is not; and a retired agent ends with no session key, no allowance (swept back to the treasury) and no reachable enclave tenancy. See `docs/T-5.8-closed-loop-and-lifecycle.md`.

## Update (T-5.7)

The drill now has 37 checks, and every agent is evaluated before it is approved: seven checks on the candidate and seven scenarios that drive its runtime against the real enclave-side authorizer, written to the provenance chain as an EVALUATED entry before the owner sees a screen. The new check strips the status tool from a freshly generated candidate and shows promotion refused, with nothing recorded and the agent's cap and payee on chain unchanged. See `docs/T-5.7-evaluation-harness.md`.

## Update (T-5.6)

The drill now has 36 checks, and the two fleet agents are no longer hand-written: each one is generated from a task specification that bounds its tools, payees, caps, window and risk tier, and the generation is written to the provenance chain before the policy is derived. The new check runs a proposer that doubles its own cap and shows it refused, with nothing recorded and the agent's cap and payee on chain unchanged. No model is involved: the proposer is a deterministic stand-in behind a port. See `docs/T-5.6-generator-and-synthesis-gate.md`.

## Update (T-5.5)

The drill now has 35 checks. The second fleet agent is driven by its own runtime: it lists its payees, reads its balance and pays the approved payee, and the drill confirms the balance and the rolling window moved by exactly that amount. Running the same task again pays nothing more, and a task naming a stranger is refused with the agent's session nonce unchanged, so nothing was built, signed or submitted. See `docs/T-5.5-payment-agent-runtime.md`.

## Update (T-5.4)

The drill now has 34 checks. Each fleet agent is derived, shown on an agent approval screen, approved by its owner, and only then installed, with a derivation, an approval and an installation written to an append-only provenance chain. The new check verifies that chain, reproduces each definition from its record, and recomputes the approval fingerprint from the recorded definition, the derived policy and the key the validator actually holds. See `docs/T-5.4-bound-approval-and-provenance.md`.

## Update (T-5.3)

The fleet's policies are no longer written by the drill. Each agent now gets an agent definition, and its session policy is derived from that definition, the deployment parameters, the stage-one tool catalog and a payee registry. Both the sealed enclave policy and the on-chain hook install data are built from that one derived policy, and the drill checks that the window installed in the validator is the derived one. The unapproved-payee check now uses an address that is not allowlisted either, because derivation produces no dead configuration; the isolation of the payee gate alone lives in the Forge tests. Still 33 checks. See `docs/T-5.3-definitions-catalog-and-derivation.md`.

## Update (T-5.2)

The drill now has 33 checks, and the two fleet agents are installed as payments-only scopes: one approved payee, plus a second allowlisted address that is deliberately not a payee. Two checks were added, each proving both layers: a payment to the allowlisted non-payee is refused by the enclaves and then by the hook when a compromised quorum forces it on chain, and the same for a payment that carries call data, where the payee's `pings` counter shows nothing was called. See `docs/T-5.2-payment-guardrails.md`.

## Update (T-5.1)

The drill now has 31 checks. Before the recovery steps it provisions a treasury account and two agent accounts, each with its own enclave set, distributed key generation, session policy and funded budget, and then checks that the agents cannot use each other's authority, that an agent cannot spend more than it was funded even inside its cap, and that revoking one leaves the other paying. Enclave sets for rotation and for the fleet both live under `.local\enclave-sets`. See `docs/T-5.1-fleet-authority-plane.md`.

## Update (T-2.7)

The drill now has 27 checks. Before the recovery steps it runs a declined session rotation (nothing changes on chain, the new enclaves are stopped) and a real rotation: a second set of three enclave processes is provisioned and runs the DKG, the policy is shown and approved, issued to the new enclaves, and one owner batch swaps the validator key and window and raises the hook cap; the old enclaves are stopped. Later steps run against the new enclave set. See `docs/T-2.7-session-rotation.md`.

## Update (T-2.3)

The drill now has 24 checks. The deployment step deploys a development sanctions oracle for the hook, and two checks were added: an allowlisted target that the oracle lists is refused on chain although the enclaves signed the operation (with a delisted control), and an unavailable oracle fails closed for session operations but not for the owner. See `docs/T-2.3-sanctions-screening.md`.

## Update (T-2.1)

The drill now has 22 checks. The owner issues a session policy to the three enclaves, which then refuse eight kinds of violation before signing, and refuse a spend over the cap from their own ledgers. The hook-level checks from T-2.2 now sign with `compromised_quorum_signed` (two shares read from sealed storage), because the enclaves would never sign those operations. See `docs/T-2.1-enclave-revalidation.md`.

## Update (T-2.4)

The drill now has 19 checks. The deployment step attests the validator, recovery module and hook with a generated attester key before they are installed, and two checks were added: an unattested module is refused by the registry and its install changes nothing (with an attested control), and a revoked attestation blocks installation again. Two consecutive runs passed. See `docs/T-2.4-module-registry-and-install-gate.md`.

## Update (T-2.2)

The drill now has 17 checks. Session operations use `executeAsSession` and run through the policy hook (`docs/T-2.2-policy-hook.md`); four checks were added: hook installation, plain `execute` refused for session operations, self-call refused, and the rolling native cap. Two consecutive runs passed.

## Update (step 9)

The drill now has 13 checks: it adds the social recovery install, a paymaster-sponsored operation, the sponsorship-is-not-authority check, a held-back operation, the I-2 revocation with the bundler and all enclaves stopped, and the I-7 recovery. See `docs/step-9-revocation-paymaster-recovery.md`. The `scripts/run-local-drill.ps1` teardown now also tolerates a bundler that the drill already stopped.
