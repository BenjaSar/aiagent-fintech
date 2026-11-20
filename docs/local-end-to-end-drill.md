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
