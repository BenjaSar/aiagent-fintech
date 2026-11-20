# Specifications (T-1.0 deliverable, part 1)

Retrieved 2026-11-16 from the sources approved for this task. The ERC files are plain text extracted from the rendered pages at eips.ethereum.org, so markup, tables and code-block formatting may differ from the source; consult the URL for the authoritative rendering.

| File | Source | Status on the source page | SHA-256 |
|---|---|---|---|
| rfc9591.txt | https://www.rfc-editor.org/rfc/rfc9591.txt | RFC (verbatim text) | fd8366795c833348d552e6a4141e8bf413310940fd50af484283207716d1a6a7 |
| erc-4337.txt | https://eips.ethereum.org/EIPS/eip-4337 | Final | 783092f51a8010fc950ceb2c917dba9ef2c8408515b0d51657e872cc5c566cf4 |
| erc-7579.txt | https://eips.ethereum.org/EIPS/eip-7579 | Draft | 42486aeec49c616960ecfae030f0b208a1224291541f2f5882252b1679e946e0 |
| erc-7484.txt | https://eips.ethereum.org/EIPS/eip-7484 | Draft | 85220433f7f28a2f950fe42faefcfed530253a16ff246a884fed5d39f2e75ece |
| erc-8004.txt | https://eips.ethereum.org/EIPS/eip-8004 | Draft | 3244408569cab5df960823e1971d0853ba5db1620eb05890872cb2ffa9beddf5 |

| rfc9380.txt | https://www.rfc-editor.org/rfc/rfc9380.txt | RFC (verbatim text); added 2026-11-16 for T-1.6 because RFC 9591's secp256k1 hashes (H1, H2, H3) are defined through its `expand_message_xmd` and `hash_to_field` | a2e6ab02b2117d6f52ac293020857752972b2e23ae005c33f612e40436ff4b6d |

| erc-1271.txt | https://eips.ethereum.org/EIPS/eip-1271 | Final; added 2026-11-16 for T-1.3 because ERC-7579's validator interface returns the ERC-1271 magic value without stating it | bd2ca3a00d108eb45be47bad0f0ec03c6c5cf7562c4b41774c0fcf8eda08c097 |

| erc-7562.txt | https://eips.ethereum.org/EIPS/eip-7562 | Review; added 2026-11-16 for the pipeline analysis because ERC-4337 defers its validation storage and opcode rules to it | d245898acd8e1aa43bd6f2cdc01aa6fbb23db63826638aa913d625760ebece49 |

| zf-frost-book-dkg.txt | https://frost.zfnd.org/tutorial/dkg.html | Zcash Foundation documentation; added 2026-11-16 for the DKG (step 5b) | see below |
| frost-core-dkg.txt | https://docs.rs/crate/frost-core/3.0.0/source/src/keys/dkg.rs | Zcash Foundation reference implementation source (frost-core 3.0.0), text extracted from the docs.rs page; its comments quote Figure 1 of the FROST paper | see below |

The FROST paper itself (https://eprint.iacr.org/2020/852.pdf) could not be fetched: the host answers automated requests with a Cloudflare bot challenge, which was not bypassed. The two Zcash Foundation sources above, which describe and implement the paper's Figure 1, were used instead. Neither defines the DKG challenge hash for secp256k1 (it is ciphersuite-specific), so this project defines its own; see `docs/T-1.2-dkg.md`.

SHA-256: zf-frost-book-dkg.txt `952df36406b3a8456868b334364fc29aa74b1a791a0b8345be62444a07c53aa4`; frost-core-dkg.txt `1d32bd0b64a2e35f3754de21a00803d7bb15e7b05154135d0fbdd634d8ce9e7e`.

Hashes are of the files as stored here.
