# RedactGate for Hermes Agent

**Your agent works with real client data. The model only ever sees placeholders.**

RedactGate is a Hermes Agent plugin that pseudonymizes personal data at the model boundary.
Before any request leaves your machine, names, e-mails, phone numbers, payment cards, IBANs and
national IDs become `[[TYPE_xxxxxx]]` placeholders. The reply you read and the arguments your
local tools receive get the real values back. It runs offline, uses only the Python standard
library, and keeps a hash-chained audit journal of what was hidden and where.

```text
you type      My client Jane Doe (jane.doe@acme-corp.com, +1 613 555 0142) was charged twice on
              card 4111 1111 1111 1111. Her SIN is 130 692 544.

model gets    My client [[PERSON_ddd963]] ([[EMAIL_b8cc9b]], [[PHONE_29aa01]]) was charged twice on
              card [[CREDIT_CARD_64028b]]. Her SIN is [[SIN_a4f557]].

model writes  Dear [[PERSON_ddd963]], we refunded the duplicate charge and sent the
              confirmation to [[EMAIL_b8cc9b]].

you read      Dear Jane Doe, we refunded the duplicate charge and sent the confirmation to
              jane.doe@acme-corp.com.
```

The second line is the real output of `/redact test` on the first; the placeholder suffixes differ
for every profile and session.

## Install

```bash
hermes plugins install ctmakc/hermes-redactgate --enable
hermes                      # start a session
/redact                     # status: what is protected
```

No API keys, no model download, no Python dependencies. Requires Hermes `>= 0.23.0`.

## What it covers

Every request Hermes sends to the main model passes through RedactGate's `llm_request`
middleware, so the whole payload is rewritten, history included:

| Payload part | Chat Completions | Anthropic Messages | Responses / Codex |
|---|---|---|---|
| System prompt (memory, skills, context files) | `system` message | top-level `system` | `instructions` |
| User and assistant messages, every turn | ✓ | ✓ | ✓ |
| Tool-call arguments the model wrote | `tool_calls[].function.arguments` | `tool_use.input` | `function_call.arguments` |
| Tool results (files, terminal output, web pages) | `tool` messages | `tool_result` | `function_call_output` |

Left untouched on purpose: ids, roles, tool names and schemas, images and files, and signed
reasoning blocks (`thinking`, `reasoning`), because providers verify their signatures.

**Where real values come back**

* **The reply you read.** `transform_llm_output` restores placeholders and is registered with
  `hold_stream=True`, so you never see a placeholder flash by while the reply streams.
* **Local tools only.** `tool_request` middleware restores values in the arguments of
  `terminal`, `process_manage`, `read_file`, `write_file`, `patch`, `search_files`,
  `execute_code`, `todo_list`, `memory` and `clarify`. Web search, the browser, image
  generation, MCP servers, other plugins' tools and subagents receive the placeholders, so a
  client's name never reaches a search engine. Change the list with `restore_tools`.

**What it cannot rewrite, and says so.** Hermes runs auxiliary model calls (session titles,
context compression, vision, approvals) without request middleware. RedactGate watches them
through the `pre_auxiliary_call` observer; when one carries personal data, it records an
`aux_unprotected` event and `/redact` shows a warning. Point auxiliary tasks at a local model,
or put the types you never want sent in `block_types`: a blocked message is stopped before
any model call and stored by Hermes as a placeholder, so no later call sees it either.

## Commands

```text
/redact                 status: packs, counters, tool policy, warnings
/redact last            your last message exactly as the model received it
/redact test <text>     dry run, no model call
/redact audit [n]       last n audit events (type counts, tool names, host)
/redact verify          check the audit hash chain
/redact terms add <t>   always hide <t>: a client, a project code name
/redact terms rm <t>    stop hiding it
```

## How it works

* **Detection.** RedactGate's jurisdiction packs with checksum validators: e-mail, phone,
  payment card (Luhn), IPv4/IPv6, IBAN (mod-97), date of birth near a keyword, Canada
  (SIN with Luhn, Business Number, GST/HST, Québec NEQ), US (SSN, ITIN, EIN), EU (IBAN, VAT),
  Ukraine (IBAN, ІПН, ЄДРПОУ), IRCC (UCI, application numbers). Person names come from cue
  words ("my client", "Dr.", "Dear", "patient", …), honorifics and a first-name list, plus
  RedactGate's document rules for Ukrainian, Russian and English names, companies and addresses.
* **A name found once is hidden everywhere.** When "my client Zebulon Moreau" is detected in
  your message, every later "Zebulon Moreau" or "Moreau" in tool output of that session is
  hidden too, even with no cue word around it.
* **Evasion-resistant without touching your files.** Detection runs on an NFKC,
  zero-width-stripped view of each text and maps hits back to the original offsets. A card
  number typed with full-width digits or a zero-width space is caught, and the code and files
  around it are passed through byte for byte.
* **Placeholders** are `[[TYPE_` + 6 hex of HMAC-SHA256(profile secret, session, value) + `]]`.
  The same value in the same session always gets the same placeholder, on every request and
  after a restart, so prompt caching keeps working and old tool calls in the history still
  resolve. Another session gets different placeholders, so a provider cannot link them.
* **Values stay in memory.** The placeholder map lives in the Hermes process only. Hermes
  already keeps the raw transcript locally, and every outgoing request is re-tokenized from
  it, so the map rebuilds itself after a restart. Restore accepts the ways models mangle
  placeholders (single brackets, lower case, spaces) and only substitutes placeholders the
  session issued.
* **Fails closed.** If redaction itself raises, the request goes out with its text replaced by
  a "content withheld" notice instead of the raw data (`fail_mode: open` reverses this).
* **Audit journal.** `audit.jsonl` in the plugin's data folder: one line per model request,
  restore, blocked turn, flagged auxiliary call or error, each with entity type counts and a
  SHA-256 link to the previous line. No values, no placeholders, session ids hashed.
  `/redact verify` finds an edited or deleted line. The chain detects tampering; someone who
  rewrites the whole tail can recompute it.
* **One short note for the model.** On turns that contain placeholders, a `pre_llm_call` hook
  adds one paragraph telling the model to copy placeholders exactly. Turn it off with
  `guide_model: false`.

## Measured

`python scripts/leakcheck.py -n 400` generates 400 conversations with checksum-valid
synthetic data placed in the system prompt, user messages, tool-call arguments and tool
results (Chat Completions and Responses shapes), runs them through the middleware and counts
raw values left on the wire:

| Type | Values | Leaked |
|---|---:|---:|
| E-mail, phone, card, SIN, SSN, IBAN | 2,400 | 0 |
| Person name from the first-name list | 105 | 0 |
| Person name after a cue word ("Patient …", "Dear …") | 207 | 0 |
| Unusual name with no cue word | 88 | 88 |

The last row is the honest limit of a model-free detector: "Thandiwe Okafor wrote" with no
cue and a first name outside the list is not recognised. Add such names with
`/redact terms add`, and once a name has been caught anywhere in a session it is hidden
everywhere after that.

Other numbers from this repository: 58 tests, including three that load the plugin through
Hermes's own `PluginManager` and drive `apply_llm_request_middleware`,
`apply_tool_request_middleware` and `transform_llm_output`; `hermes plugins validate` passes;
detection takes about 60 ms for 11,000 characters of new text, and history already seen is
served from a content cache (a 60-turn request re-sent costs about 2 ms).

## Configuration

`hermes config set plugins.entries.redactgate.settings.<key> <value>`, or the plugin's
settings form in Hermes Desktop.

| Key | Default | Meaning |
|---|---|---|
| `packs` | `[GENERIC, CA, US, EU, UA, IRCC]` | Detection packs; `ALL` for every pack. GENERIC is always on. |
| `names` | `true` | English chat name detection |
| `rule_ner` | `true` | RedactGate document rules (people, companies, addresses; en/uk/ru) |
| `terms` | `[]` | Extra words to always hide (case-insensitive) |
| `allow` | `[]` | Values never hidden, e.g. your own support address |
| `ignore_types` | `[]` | Types to leave alone, e.g. `[IP_ADDRESS]` |
| `restore_tools` | the 10 local tools above | Tools that receive real values; `["*"]` for all |
| `block_types` | `[]` | Types that must never reach a model, e.g. `[CREDIT_CARD, SIN]` |
| `fail_mode` | `closed` | `open` sends the request unmodified if redaction fails |
| `guide_model` | `true` | One-paragraph placeholder note for the model |

## Disclosure

* **Network:** none. The plugin makes no network calls and loads no models.
* **Files written**, all inside its Hermes plugin data folder
  (`$HERMES_HOME/plugin-data/<plugin namespace>/`): `audit.jsonl` (type counts, tool names,
  provider host, hashed session ids; rotated at 20 MB) and Hermes's own `state.json` for this
  plugin (a random 32-byte HMAC secret and the terms you add with `/redact terms add`).
* **Reads:** nothing outside its own data folder and the request payloads Hermes hands it.
* **No** shell commands, subprocesses, background threads, credentials, telemetry or
  self-update. Personal values are held in process memory only.
* **Changes what is sent:** the model and non-local tools receive placeholders instead of
  personal data; replies and local tool arguments get real values back.
  `transform_llm_output` holds the reply stream until it is restored, so Hermes drops
  commentary between tool calls on surfaces that stream (see Hermes docs, *hold_stream*).

## Provenance

The detection core in `redactgate_core/` is vendored from
[RedactGate](https://github.com/ctmakc/redactgate) (AGPL-3.0), the author's self-hosted
redaction proxy. The jurisdiction packs, validators, Unicode normalization and span merging
date from June 2026; the Ukrainian/Russian/English document rules date from 6 November 2026
(RedactGate Desktop). Everything else, the Hermes integration, placeholders, request walker,
restore policy, audit journal, `/redact` command, tests and leak check, was written for the
Hermes Agent Plugin Hackathon. Details in [DISCLOSURE.md](DISCLOSURE.md).

## License

AGPL-3.0-only. © 2026 Maksym Stepanenko.
