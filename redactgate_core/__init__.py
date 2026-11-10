"""Vendored detection core of RedactGate (github.com/ctmakc/redactgate, AGPL-3.0).

Provenance (see DISCLOSURE.md):
* validators.py, normalize.py, merge.py, regex_packs.py and packs/*.json come from
  RedactGate ``app/redaction`` + ``app/packs`` at commit 54688c2 (2026-06-26), written
  before the Hermes Plugin Hackathon. Packs were converted from YAML to JSON unchanged so
  the plugin needs no PyYAML; imports were rewritten to be package-relative.
* ner_rules.py and gazetteer.py come from RedactGate Desktop ``app/desktop`` at commit
  96c09ad / ec04b7e (2026-11-06), a separate product written after the hackathon start.
Everything outside this package is new code written for the hackathon.
"""
