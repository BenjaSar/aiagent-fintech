# Base Sepolia runtime-code fixtures

Captured 2026-11-16 with `eth_getCode` at `latest` from https://sepolia.base.org (chain id 84532). They let the local pipeline etch the canonical EntryPoint v0.8.0 and its SenderCreator at their canonical addresses without network access.

| File | Address | Bytes | SHA-256 |
|---|---|---|---|
| EntryPoint-v0.8.0.runtime.hex | 0x4337084d9e255ff0702461cf8895ce9e3b5ff108 | 21738 | d7bbb54acf96ddf2fffd54e48d9e871b34778b7e51d446c41d21c2e31f417eb1 |
| SenderCreator-v0.8.0.runtime.hex | 0x449ed7c3e6fee6a97311d4b55475df59c44add33 | 1217 | cd59757ec5e0bc884a4a6308ddaebf8fc04c471fbccc6b8998a9e15a173f4716 |

The EntryPoint code was compared with `deployments/ethereum/EntryPoint.json` at tag v0.8.0 of eth-infinitism/account-abstraction: same length, and the 208 bytes that differ are all zero in the artifact (immutables filled in on chain). See `docs/T-1.0-version-and-provider-decisions.md`.

Etching copies code only; storage stays empty, which matches a fresh deployment because the EntryPoint keeps no required initial state.
