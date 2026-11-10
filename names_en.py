"""Chat-oriented English person-name detection (new for the Hermes plugin).

The vendored RedactGate rules (``redactgate_core.ner_rules``) are tuned for Ukrainian /
Russian / English *documents* (bank statements, contracts): patronymics, initials, legal
forms. Chat text looks different — "email Jane Doe", "my client is Omar Haddad",
"Dr. Patel said". This module adds three conservative signals:

* cue phrases followed by 1–3 capitalised words ("my name is", "client", "Dr.", "Dear" …);
* a common-first-name gazetteer followed by a capitalised surname ("Jane Doe");
* honorifics ("Mr. Okafor", "Ms Lindqvist").

It never fires on a lone capitalised word without a cue or a known first name, so ordinary
sentence-initial words stay untouched. Pure stdlib, no model download.
"""

from __future__ import annotations

import re

from .redactgate_core.entities import EntitySpan

# ~300 common given names across English-speaking countries (US SSA / UK ONS / StatCan top
# lists) plus frequent names of immigrant communities in North America. Lower-case.
_AMBIGUOUS = frozenset("bill mark ruby rose dawn crystal amber holly lee kim jean chen wei yan victoria austin jesus".split())
_FIRST_NAMES = frozenset(
    """
    aaron abdul abigail adam adrian ahmed aiden aisha alan albert alejandro alex alexander
    alexandra alexis alice alicia alison amanda amber amelia amir amy ana andrea andrew angela
    anita ann anna anne anthony antonio arjun arthur ashley aubrey audrey austin ava barbara
    beatrice ben benjamin beth betty beverly bill blake bob bonnie brandon brenda brian
    brittany bruce bryan caleb cameron carl carlos carmen carol caroline carter catherine
    charles charlotte chen cheryl chloe chris christian christina christine christopher
    claire clara connor craig crystal cynthia daniel danielle david dawn deborah denise
    dennis derek diana diane diego dmitri dmitry donald donna dorothy douglas dylan edward
    elena eli elijah elizabeth ella ellen emily emma eric erica erin ethan eugene eva evelyn
    farah fatima felix fernando fiona frances francis frank gabriel gabriela gary george
    gerald gloria grace greg gregory hailey hannah harold harry hassan heather helen henry
    hiroshi holly hugo ian igor irene isaac isabel isabella ivan jack jacob jacqueline james
    jamie jane janet janice jason javier jayden jean jeffrey jennifer jeremy jerry jesse
    jessica jesus joan joe joel john jonathan jordan jorge jose joseph joshua joyce juan
    judith judy julia julian julie justin karen karl kate katherine kathleen kathryn kayla
    keith kelly kenneth kevin kim kimberly kyle laura lauren lawrence leah lee leo leon
    liam lily linda lisa logan lucas lucy luis luke lydia madison maria mariam marie marilyn
    mark martha martin mary mason matthew maya megan melissa mia michael michelle miguel
    mike mohammed mohammad muhammad nancy naomi natalie nathan nicholas nicole noah nora
    oleg oliver olivia omar oscar pamela patricia patrick paul paula peter philip priya
    rachel rahul rajesh ralph raymond rebecca richard robert roger ronald rose ruby russell
    ruth ryan samantha samuel sandra sara sarah scott sean sebastian sergei sergey shannon
    sharon shirley sofia sophia sophie stephanie stephen steven susan taylor teresa terry
    thomas timothy tina todd tom tony tyler valerie vanessa victor victoria vincent walter
    wayne wei william yan yusuf zachary zoe olena oksana iryna natalia mykola taras andriy
    yulia anastasia maksym bohdan dmytro serhiy volodymyr kateryna svitlana viktor yaroslav
    """.split()
) - _AMBIGUOUS

_CAP = r"(?:Mc|Mac|O['’]|D['’])?[A-Z][a-z]+(?:[-'’][A-Z][a-z]+)?"
_SURNAME = rf"{_CAP}"
_NAME_2_3 = rf"{_CAP}(?:\s+{_CAP}){{0,2}}"

# Words that look like names after a cue but are not (pronouns, common nouns, weekdays …).
_STOP = frozenset(
    """
    i me my we our you your he she they them it this that the a an and or but not here there
    monday tuesday wednesday thursday friday saturday sunday january february march april may
    june july august september october november december today tomorrow yesterday please
    thanks thank hello hi hey dear sir madam team all everyone everybody someone somebody
    customer client patient user admin support sales manager director doctor
    """.split()
)

_CUE = re.compile(
    r"(?:\b(?:[Mm]y name is|[Nn]ame:|[Ff]ull name:|[Cc]alled|[Nn]amed|[Dd]ear|"
    r"[Pp]atient|[Cc]lient|[Cc]ustomer|[Ee]mployee|[Tt]enant|[Cc]ontact|[Ss]igned|[Ff]rom:|[Tt]o:|"
    r"[Aa]pplicant|[Bb]eneficiary|[Aa]ccount holder|[Cc]ardholder|[Hh]usband|[Ww]ife|[Ss]on|"
    r"[Dd]aughter|[Bb]rother|[Ss]ister|[Mm]other|[Ff]ather|[Cc]olleague|[Ll]andlord|[Ll]awyer)"
    r"[ \t]*[:,]?[ \t]+)(?P<v>" + _NAME_2_3 + r")"
)
_HONORIFIC = re.compile(
    r"\b(?:Mr|Mrs|Ms|Mx|Dr|Prof|Sir|Madam|Mme|Mlle|M)\.?[ \t]+(?P<v>" + _NAME_2_3 + r")"
)
_FIRST_LAST = re.compile(r"\b(?=(?P<f>[A-Z][a-z]+)[ \t]+(?P<l>" + _SURNAME + r")\b)")


def _clean_tail(text: str, start: int, value: str) -> tuple[int, int, str] | None:
    words = value.split()
    while words and words[-1].lower() in _STOP:
        words.pop()
    if not words or words[0].lower() in _STOP:
        return None
    joined_end = start
    # Re-locate the kept words inside the original slice so offsets stay exact.
    pos = start
    for w in words:
        idx = text.find(w, pos)
        if idx < 0:
            return None
        pos = idx + len(w)
        joined_end = pos
    return start, joined_end, text[start:joined_end]


def detect_people_en(text: str) -> list[EntitySpan]:
    """Return PERSON spans for English chat text. Offsets index into ``text``."""
    out: list[EntitySpan] = []
    for rx, score in ((_CUE, 0.86), (_HONORIFIC, 0.9)):
        for m in rx.finditer(text):
            got = _clean_tail(text, m.start("v"), m.group("v"))
            if got is None:
                continue
            s, e, v = got
            # A cue followed by a single common word ("Dear Team") was already dropped by
            # _STOP; a single capitalised word after a strong cue is still a name.
            out.append(EntitySpan(start=s, end=e, entity_type="PERSON", text=v, score=score, source="names_en"))
    for m in _FIRST_LAST.finditer(text):
        first, last = m.group("f"), m.group("l")
        if first.lower() in _FIRST_NAMES and last.lower() not in _STOP and last.lower() not in _FIRST_NAMES | {"street", "avenue", "road"}:
            out.append(
                EntitySpan(start=m.start("f"), end=m.end("l"), entity_type="PERSON", text=text[m.start("f"):m.end("l")], score=0.8, source="names_en")
            )
        elif first.lower() in _FIRST_NAMES and last.lower() in _FIRST_NAMES:
            # "Jane Grace" — two given names; still a person.
            out.append(
                EntitySpan(start=m.start("f"), end=m.end("l"), entity_type="PERSON", text=text[m.start("f"):m.end("l")], score=0.7, source="names_en")
            )
    return out
