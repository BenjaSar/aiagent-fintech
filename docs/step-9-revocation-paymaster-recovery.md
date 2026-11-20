# Step 9 — revocation, paymaster and social recovery

2026-11-16. Three contracts' worth of work and three invariant drills, all on the local pipeline. The full drill (`powershell -File scripts\run-local-drill.ps1`, now 13 checks) passed on three consecutive runs after the last code change; the last took 23 seconds.

## Design decisions (please review)

These were not specified in the roadmap; I chose the simplest sound option.

**Social recovery** (`contracts/SocialRecoveryModule.sol`, R-NEW-1, I-7)

- An ERC-7579 **executor** module. Install data: guardian list, threshold, delay (at least 1 hour, so the owner always has a cancellation window).
- Guardians are plain addresses chosen by the account at install time. Each approves `(account, newOwner)` with its own transaction. When the threshold is reached the recovery becomes executable after the delay. Anyone can then call `executeRecovery`; no bundler or paymaster is involved.
- The account itself can `cancelRecovery()` (through an owner-signed operation) at any time; this invalidates every pending approval (a per-account round counter). Uninstalling the module clears the guardians and invalidates pending recoveries.
- Recovery rotates the owner only. It does **not** uninstall the session validator; the new owner should revoke or keep it deliberately.
- The company cannot be a guardian by construction only in the sense that guardians are chosen by the account; nothing on chain can prove who controls an address, so I-7 is enforced by process and by the drill running without any company service.

**Owner rotation** (`FintechModularAccount.rotateOwner`): callable only by an installed executor module. A call from the account itself is refused, so a plain self-call cannot rotate the owner.

**Paymaster** (`contracts/FintechPaymaster.sol`, R-NEW-2, I-6): a verifying paymaster. A company-run sponsor signer approves each operation with a validity window; the signed hash covers the operation fields except the paymaster signature (the v0.8 operation hash cannot sign over its own paymaster data). It returns no context, so `postOp` is never called and always reverts. It has no function that touches an account: it can only validate, hold an EntryPoint deposit, and let its owner withdraw that deposit. OpenZeppelin 5.6.1 ships no paymaster, so this is written here and is **unaudited**.

## Results

| Check | Result |
|---|---|
| Owner installs social recovery, 2-of-3 guardians, 3600 s delay | PASS |
| Paymaster sponsors a quorum-signed session operation; the account's EntryPoint deposit is unchanged and the paymaster deposit pays | PASS |
| A sponsored operation signed by a stranger is rejected (`AA24`): sponsorship is not authority | PASS |
| With the bundler and all three enclaves stopped, the owner uninstalls the session validator through a self-submitted `handleOps`; the session key is cleared; a quorum-signed operation prepared earlier, which a simulation first shows to be valid, now fails with `AA24` (I-2) | PASS |
| Two guardians approve; execution before the delay is refused with `RecoveryNotReady`; after the delay a stranger executes and the owner changes (I-7) | PASS |
| After recovery the old owner is rejected (`AA24`) and the new owner's operation executes, again through self-submitted `handleOps` | PASS |

Contract tests: 19 for the recovery module (configuration, thresholds, delay, cancellation, restart after cancellation, other accounts' guardians, non-executor and self-call rotation refused, uninstall) and 12 for the paymaster (sponsored operation charged to the paymaster, wrong signer, expired and not-yet-valid windows, malformed data, signature not reusable for another operation, paymaster cannot move funds or rotate the owner, only the EntryPoint validates, only the owner withdraws, `postOp` unsupported). Mutation check: removing the signer comparison in the paymaster made the two relevant tests fail; reverted. Totals now: 84 Solidity tests, 192 Python tests; `forge fmt`, ruff and mypy `--strict` are clean.

## Risks and limits

- **Update 2026-11-17: closed locally by T-2.2 (`docs/T-2.2-policy-hook.md`); session operations now run through the policy hook and cannot install modules, call the account or delegatecall. The text below describes the state at step 9.** **A session-signed operation still has unrestricted authority over the account**, including module installation and the ERC-7579 delegatecall mode, until the Phase 2 policy hook (T-2.2) exists. Because of that, a compromised session quorum could install its own executor module and rotate the owner. `rotateOwner` does not create this exposure (it was already open through `installModule` and delegatecall), but recovery makes the consequence worse. Treat the hook as a prerequisite for anything beyond local development.
- **Guardian collusion** at the threshold takes the account after the delay; the owner's only defence is noticing and cancelling within that window. No notification mechanism exists.
- **Guardian approvals are on-chain transactions** that each guardian pays for; there is no off-chain signature aggregation.
- **The paymaster is unaudited**, unstaked, and was tried only with safe mode off. A real bundler will apply ERC-7562 rules to it (it reads only immutables), which is untested.
- **The sponsor signer is a plain key** held by whoever runs the paymaster; there is no rate limit, budget or per-account cap yet.
- **The recovery drill advances anvil's clock** with `evm_increaseTime`; real timing was not exercised.
- Revocation and recovery here are demonstrated on one account on a local chain with throwaway keys. Base Sepolia execution is NOT RUN.
