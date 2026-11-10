# Disclosure for the Hermes Agent Plugin Hackathon

The hackathon opened on 2026-11-04 21:33 UTC. This plugin reuses code that existed before
that moment, and code written for another product after it. Both are listed here.

## Taken from RedactGate (github.com/ctmakc/redactgate, AGPL-3.0, same author)

| Files in this repo | RedactGate source | Commit (date) | Before the hackathon? |
|---|---|---|---|
| `redactgate_core/validators.py`, `normalize.py`, `merge.py`, `regex_packs.py` | `app/redaction/` | `54688c2` (2026-06-26) | yes |
| `redactgate_core/packs/*.json` | `app/packs/*.yaml` | `54688c2` (2026-06-26) | yes |
| `redactgate_core/entities.py` (trimmed to `EntitySpan`) | `app/schemas/entities.py` | `54688c2` (2026-06-26) | yes |
| `redactgate_core/ner_rules.py`, `gazetteer.py` | `app/desktop/` (RedactGate Desktop) | `96c09ad`, `ec04b7e` (2026-11-06) | no: written after the start, for a different product (an offline document anonymizer) |

Changes made to the vendored code for this plugin:

* packs converted from YAML to JSON (content unchanged), so the plugin needs no PyYAML;
* imports rewritten to be package-relative;
* `eu.json` VAT pattern now requires at least six digits (the original matched words such as
  `GREEN` in "RED-GREEN-REFACTOR", found while testing against the Hermes system prompt);
* `generic.json` IBAN pattern now requires the mod-97 checksum.

`normalize.py` is vendored for reference; the plugin uses its own offset-preserving
normalization in `detect.py`.

## Written for the hackathon (2026-11-10 onward)

Everything outside `redactgate_core/`: `__init__.py`, `plugin.yaml`, `engine.py`,
`detect.py`, `names_en.py`, `vault.py`, `walk.py`, `audit.py`, `commands.py`, `setup_check.py`, `tests/`,
`scripts/leakcheck.py`, `demo/`, CI, README. The git history of this repository shows the
dates.

## Runtime behaviour (rule 13)

No network calls, no model downloads, no shell commands or subprocesses, no background
threads, no credentials, no telemetry, no self-update. Reads the merged Hermes config
read-only to report settings that bypass plugins; writes Hermes config only when the user runs
`/redact harden` (one key: `auxiliary.title_generation.model_upgrade_enabled = false`). Files are written only inside the
plugin's Hermes data folder: `audit.jsonl` (entity type counts, tool names, provider host,
hashed session ids) and the plugin's `state.json` (random HMAC secret, user-added terms).
Personal values are held in process memory only.
