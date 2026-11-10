"""Synthetic leak check: build chat requests full of generated PII, run them through the
plugin's llm_request middleware, and count every raw value that still reaches the wire.

    python scripts/leakcheck.py            # 300 conversations, seed 7
    python scripts/leakcheck.py -n 1000 --seed 1

Values are generated, checksums included (Luhn cards and SINs, mod-97 IBANs, valid SSN
ranges), and placed in user messages, the system prompt, tool-call arguments and tool
results, in Chat Completions and Responses shapes. Person names are drawn from two pools:
names the plugin's first-name list knows, and names it does not (to measure honestly
what a gazetteer cannot see), each with and without a cue word.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import random
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_plugin():
    spec = importlib.util.spec_from_file_location("redactgate_plugin", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["redactgate_plugin"] = mod
    spec.loader.exec_module(mod)
    from redactgate_plugin.engine import Engine, Settings

    return Engine, Settings


KNOWN_FIRST = ["Jane", "Omar", "Priya", "Daniel", "Fatima", "Lucas", "Olena", "Sofia", "Ahmed", "Grace", "Taras"]
UNKNOWN_FIRST = ["Wei", "Zebulon", "Thandiwe", "Aurelio", "Ngozi", "Kwame", "Saoirse", "Bjorn", "Itzel"]
LAST = ["Doe", "Haddad", "Raman", "Kowalczyk", "Okafor", "Lindqvist", "Petrenko", "Moreau", "Nguyen", "Abara"]
CUES = ["My client", "Patient", "Customer", "Dear", "Contact", "my colleague"]
DOMAINS = ["acme-corp.com", "example.org", "mail.ca", "clinic.health", "law.ua"]


def luhn_complete(prefix: str, length: int, rnd: random.Random) -> str:
    body = prefix + "".join(str(rnd.randint(0, 9)) for _ in range(length - len(prefix) - 1))
    total = 0
    for i, ch in enumerate(reversed(body)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return body + str((10 - total % 10) % 10)


def card(rnd):
    n = luhn_complete(rnd.choice(["4", "51", "55", "37"]), 16, rnd)
    return " ".join(n[i : i + 4] for i in range(0, 16, 4))


def sin(rnd):
    n = luhn_complete(rnd.choice("1234567"), 9, rnd)
    return f"{n[:3]} {n[3:6]} {n[6:]}"


def ssn(rnd):
    return f"{rnd.randint(100, 665)}-{rnd.randint(10, 99)}-{rnd.randint(1000, 9999)}"


def iban_de(rnd):
    bban = "".join(str(rnd.randint(0, 9)) for _ in range(18))
    num = int("".join(str(int(c, 36)) for c in bban + "DE00"))
    check = 98 - num % 97
    raw = f"DE{check:02d}{bban}"
    return " ".join(raw[i : i + 4] for i in range(0, len(raw), 4))


def phone(rnd):
    return rnd.choice(["+1 ({a}) {b}-{c}", "+1 {a}-{b}-{c}", "({a}) {b}-{c}", "{a}.{b}.{c}"]).format(
        a=rnd.randint(201, 989), b=rnd.randint(200, 999), c=f"{rnd.randint(0, 9999):04d}"
    )


def person(rnd, known: bool, cue: bool):
    first = rnd.choice(KNOWN_FIRST if known else UNKNOWN_FIRST)
    name = f"{first} {rnd.choice(LAST)}"
    return name, (f"{rnd.choice(CUES)} {name}" if cue else name)


def conversation(rnd):
    vals: dict[str, str] = {}
    known, cue = rnd.random() < 0.5, rnd.random() < 0.5
    name, name_phrase = person(rnd, known, cue)
    vals["PERSON" + ("_known" if known else "_unknown") + ("_cue" if cue else "_bare")] = name
    vals["EMAIL"] = f"{name.split()[0].lower()}.{name.split()[1].lower()}@{rnd.choice(DOMAINS)}"
    vals["PHONE"] = phone(rnd)
    vals["CREDIT_CARD"] = card(rnd)
    vals["SIN"] = sin(rnd)
    vals["SSN"] = ssn(rnd)
    vals["IBAN"] = iban_de(rnd)
    user = (
        f"{name_phrase} wrote from {vals['EMAIL']} and {vals['PHONE']}. Card {vals['CREDIT_CARD']} was charged twice; "
        f"SIN {vals['SIN']}, US SSN {vals['SSN']}, refund to IBAN {vals['IBAN']}."
    )
    tool_args = json.dumps({"command": f"grep -n '{vals['EMAIL']}' crm.csv"})
    tool_out = f"42: {name}, {vals['PHONE']}, {vals['IBAN']}"
    if rnd.random() < 0.5:
        req = {"messages": [
            {"role": "system", "content": f"Account owner e-mail: {vals['EMAIL']}"},
            {"role": "user", "content": user},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "c", "type": "function", "function": {"name": "terminal", "arguments": tool_args}}]},
            {"role": "tool", "tool_call_id": "c", "content": tool_out},
        ]}
    else:
        req = {"instructions": f"Account owner e-mail: {vals['EMAIL']}", "input": [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": user}]},
            {"type": "function_call", "call_id": "c", "name": "terminal", "arguments": tool_args},
            {"type": "function_call_output", "call_id": "c", "output": tool_out},
        ]}
    return req, vals


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    Engine, Settings = _load_plugin()
    eng = Engine(Settings(), b"leakcheck-secret".ljust(32, b"."), Path(tempfile.mkdtemp()))
    rnd = random.Random(a.seed)
    seen, leaked = Counter(), Counter()
    for i in range(a.n):
        req, vals = conversation(rnd)
        res = eng.on_llm_request(req, session_id=f"s{i}")
        wire = json.dumps(res["request"] if res else req, ensure_ascii=False)
        for k, v in vals.items():
            seen[k] += 1
            if v in wire:
                leaked[k] += 1
    print(f"{a.n} synthetic conversations (seed {a.seed}); every value appears in 1–3 places per request\n")
    print(f"{'type':<24}{'values':>8}{'leaked':>8}{'hidden':>9}")
    for k in sorted(seen):
        hid = 100 * (seen[k] - leaked[k]) / seen[k]
        print(f"{k:<24}{seen[k]:>8}{leaked[k]:>8}{hid:>8.1f}%")
    structured = [k for k in seen if not k.startswith("PERSON")]
    total_leak = sum(leaked[k] for k in structured)
    print(f"\nstructured identifiers leaked: {total_leak}")
    return 1 if total_leak else 0


if __name__ == "__main__":
    raise SystemExit(main())
