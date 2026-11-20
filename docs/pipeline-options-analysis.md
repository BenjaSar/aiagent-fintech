# Pipeline options analysis — performance and architecture

2026-11-16. Scope: the signing and validation path built so far (T-1.6 aggregation, T-1.8 verifier, T-1.3 validator) and the stages the plan puts around it. Recommendations only; no code was changed for this analysis.

## Measurements

| Item | Result | Conditions |
|---|---|---|
| `aggregate`, 2 signers, includes self-verification | 39.8 ms median, 41.7 ms max | Pure Python 3.12, 30 runs, this machine |
| Self-verification alone | 19.6 ms median | same |
| One point multiplication | 6.6 ms median | same |
| On-chain `verify` (library) | 13.1k gas | Foundry local EVM, warm storage and precompiles |
| On-chain `validateUserOp` | 19.9k gas before the storage change, 17.5k after | same; the key was written in the same transaction, so its storage reads were warm |

Estimates, not measurements: aggregation grows by roughly one point multiplication (about 7 ms) per extra signer, since each adds one multiplication to the group commitment. In a real bundle the key's storage slots are read cold, which adds about 2,000 gas per slot over the warm figures above.

## Options and recommendations

| # | Stage | Options | Recommendation |
|---|---|---|---|
| 1 | On-chain signature scheme | (a) FROST Schnorr via `ecrecover` (current); (b) threshold ECDSA with native `ecrecover`; (c) elliptic-curve arithmetic in Solidity | **Keep (a).** (b) saves at most about 10k gas per operation but breaks the RFC 9591 mandate in R-ARCH-2 and needs a different, heavier signing protocol. (c) costs far more gas than the precompile route. |
| 2 | Key storage layout | (a) dynamic `bytes` in a mapping; (b) a struct of fixed fields (`uint8 prefix`, `uint256 x`) in the mapping (current) | **Done: changed to (b) on 2026-11-16; tests assert the slot positions.** ERC-7562 (status Review) rule STO-021 lets an account's validation read an external contract's storage only if the slot is "associated" with the account: `keccak(account‖x)+n`, n from 0 to 128. A 33-byte `bytes` value keeps its data at `keccak(slot)`, outside that range, so a bundler enforcing the canonical rules may reject every operation. Fixed fields stay inside the range and save about one cold read. |
| 3 | Aggregation implementation | (a) pure Python (current, about 40 ms for 2 signers); (b) a native secp256k1 binding | **Keep (a) for now.** It sits well inside the 120–200 ms execution figure, though the plan assigns no budget to the signing round trips; T-V1 must define one. (b) would add a dependency and is unmeasured. Revisit only if T-V1 shows the budget is exceeded, especially at 3-of-5. |
| 4 | Signing rounds | (a) two online rounds (current design); (b) precompute round one | **Keep (a) on the testnet.** RFC 9591's `commit` takes only the key share, so round one could be precomputed and the online path cut to one round trip. That needs a stored pool of single-use nonces, and RFC 9591 §7.3 warns nonce reuse allows full key recovery; a TEE restored from a snapshot could reuse them. Consider only after T-V1 shows round trips matter and enclave state is rollback-protected. |
| 5 | Off-chain self-verification | (a) always verify the aggregate (current); (b) skip it | **Keep (a).** It costs about 20 ms but stops a bad signature from reaching the bundler, and fits the fail-closed rule (I-8). |
| 6 | ERC-1271 forwarding | (a) never authorize the session key (current); (b) forward | **Keep (a).** (b) would let the agent sign permits outside any spend policy. |
| 7 | Cross-language test vectors | (a) hex literals in each test suite (current); (b) one shared vector file for Python and Solidity | **Move to (b)** when next touching the tests, so the two implementations cannot drift apart silently. Low priority. |

## Layering

The domain module is pure and layered as the architecture rules require. Still to build around it: an application-layer coordinator that calls `aggregate`, and ports for enclave signing, bundler and paymaster, each with an in-memory adapter for tests. Enclave-side policy re-validation (R-NEW-4) must run inside the enclave, not in the coordinator.

## Caveats

- ERC-7562 is a draft and applies to the canonical mempool; private bundlers may differ. A real bundler simulation on Base Sepolia is the only way to confirm acceptance, and it needs T-1.1.
- The opcode side is clean: the validator calls only precompiles 0x1, 0x2 and 0x5, which ERC-7562's OP-062 allows.
- Aggregation timings come from one machine and Python version.
