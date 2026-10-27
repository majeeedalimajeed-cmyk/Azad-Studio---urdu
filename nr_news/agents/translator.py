"""Agent C — a distinct Roman Urdu translation agent, plus an independent validator."""
from __future__ import annotations

import re
from typing import Any

from ..config import CONFIG
from ..llm import AgentUnavailable, LLMClient
from ..util import clean_text, numeric_atoms

TRANSLATOR_SYSTEM = """You are Agent C, a SEPARATE Roman Urdu translation agent.
Your only source of meaning is the approved Urdu-script headline, bullets, fact inventory and verification notes.
Translate into natural Roman Urdu without adding opinions, explanations, entities or facts.
Rules:
- Preserve every name, location, title, date, number and unit accurately.
- Preserve uncertainty exactly: alleged, proposed, attributed, developing, and phrases such as 'wherever feasible'.
- The Roman Urdu bullet count MUST equal the Urdu bullet count.
- Keep spelling of names/locations consistent and use common Latin spellings for acronyms.
- Do not use publisher names and do not invent a quotation.
- Return ONLY one JSON object with keys: roman_headline (string), roman_bullets (array),
  entity_map (array of {urdu, roman} objects), uncertainty_map (array of {urdu, roman} objects),
  no_new_facts (boolean).
"""

REVIEWER_SYSTEM = """You are an independent Urdu/Roman Urdu consistency reviewer.
Compare the two versions against the fact inventory and verification notes.
Do not rewrite. Return ONLY JSON: {consistent:boolean, issues:[strings], name_checks:[strings], number_checks:[strings]}.
Flag any changed meaning, added opinion/explanation/fact, omitted uncertainty, wrong name, date, figure or title.
"""

UNCERTAINTY_HINTS = {
    "جہاں ممکن": ("where feasible", "wherever feasible", "jahan mumkin", "jahan mumkin ho"),
    "تجویز": ("proposal", "proposed", "propose"),
    "مبینہ": ("alleged", "allegedly"),
    "الزام": ("allegation", "alleged", "accusation"),
    "دعوی": ("claim", "claimed"),
    "جانچ": ("scrutiny", "review", "check", "verification"),
    "ترقی پذیر": ("developing",),
    "تازہ ترین": ("latest",),
}

PUBLISHER_NAMES = ("munsif", "etemaad", "indian express", "the wire", "azad studio")


class RomanUrduTranslatorAgent:
    def __init__(self, llm: LLMClient | None = None, config=CONFIG):
        self.llm = llm or LLMClient(config)
        self.config = config

    def translate(self, urdu: dict[str, Any], facts: dict[str, Any] | None = None,
                  verification_notes: list[str] | None = None) -> dict[str, Any]:
        if not self.llm.available:
            raise AgentUnavailable("Separate Roman Urdu translation model not configured")
        payload = {
            "approved_urdu_headline": urdu["urdu_headline"],
            "approved_urdu_bullets": urdu["urdu_bullets"],
            "fact_inventory": facts or {},
            "verification_notes": verification_notes or [],
        }
        data = self.llm.chat_json(
            system=TRANSLATOR_SYSTEM,
            user=payload,
            model=self.config.translator_model,
        )
        bullets = data.get("roman_bullets")
        if not data.get("roman_headline") or not isinstance(bullets, list) or len(bullets) != len(urdu["urdu_bullets"]):
            raise AgentUnavailable("Agent C output shape is inconsistent with the Urdu version")
        if data.get("no_new_facts") is False:
            raise AgentUnavailable("Agent C reported that it added facts")
        return data


class TranslationValidator:
    def __init__(self, llm: LLMClient | None = None, config=CONFIG):
        self.llm = llm or LLMClient(config)
        self.config = config

    def validate(self, urdu: dict[str, Any], roman: dict[str, Any], facts: dict[str, Any] | None = None,
                 verification_notes: list[str] | None = None, evidence_text: str = "") -> dict[str, Any]:
        errors: list[str] = []
        warnings: list[str] = []
        urdu_text = clean_text(urdu["urdu_headline"] + " " + " ".join(urdu["urdu_bullets"]))
        roman_text = clean_text(roman["roman_headline"] + " " + " ".join(roman["roman_bullets"]))

        if not 3 <= len(roman["roman_bullets"]) <= 5:
            errors.append("Roman Urdu must contain 3-5 bullets.")
        if len(urdu["urdu_bullets"]) != len(roman["roman_bullets"]):
            errors.append("Urdu and Roman Urdu bullet counts differ.")

        urdu_numbers = numeric_atoms(urdu_text)
        roman_numbers = numeric_atoms(roman_text)
        if urdu_numbers != roman_numbers:
            errors.append(f"Dates/figures differ: Urdu={sorted(urdu_numbers)}, Roman={sorted(roman_numbers)}")

        allowed_numbers = numeric_atoms(evidence_text)
        for key in ("dates", "figures"):
            for value in (facts or {}).get(key, []) or []:
                allowed_numbers |= numeric_atoms(str(value))
        # Publication dates and page numbers can appear in evidence; if no evidence
        # was supplied, the programmatic check is limited to cross-version parity.
        if allowed_numbers and not urdu_numbers.issubset(allowed_numbers):
            errors.append(f"Urdu contains numbers absent from evidence: {sorted(urdu_numbers - allowed_numbers)}")

        for pair in roman.get("entity_map", []) or []:
            u, r = clean_text(str(pair.get("urdu", ""))), clean_text(str(pair.get("roman", "")))
            if not u or not r:
                errors.append("Incomplete entity mapping.")
            elif u not in urdu_text:
                errors.append(f"Mapped Urdu entity not present: {u}")
            elif r.lower() not in roman_text.lower():
                errors.append(f"Mapped Roman entity not present: {r}")

        # Any Latin acronym/name in Urdu copy must survive into Roman Urdu unless
        # explicitly transliterated through entity_map.
        latin_names = {m for m in re.findall(r"\b[A-Z][A-Za-z.-]{1,}\b", urdu_text) if m.lower() not in {"urdu"}}
        mapped_urdu = " ".join(str(x.get("urdu", "")) for x in roman.get("entity_map", []) or [])
        for name in latin_names:
            if name.lower() not in roman_text.lower() and name.lower() not in mapped_urdu.lower():
                errors.append(f"Proper name dropped in Roman Urdu: {name}")

        combined_roman = roman_text.lower()
        for urdu_marker, expected_phrases in UNCERTAINTY_HINTS.items():
            if urdu_marker in urdu_text:
                if not any(phrase in combined_roman for phrase in expected_phrases):
                    errors.append(f"Uncertainty wording not preserved for '{urdu_marker}'.")

        for pair in roman.get("uncertainty_map", []) or []:
            u, r = clean_text(str(pair.get("urdu", ""))), clean_text(str(pair.get("roman", "")))
            if u and r and (u not in urdu_text or r.lower() not in combined_roman):
                errors.append(f"Declared uncertainty mapping not present: {u} -> {r}")

        for banned in PUBLISHER_NAMES:
            if banned in urdu_text.lower() or banned in combined_roman:
                errors.append("Publisher name must not appear on the card.")

        # Independent model cross-check is used whenever configured.
        model_review: dict[str, Any] | None = None
        if self.llm.available:
            try:
                model_review = self.llm.chat_json(
                    system=REVIEWER_SYSTEM,
                    user={
                        "urdu": {"headline": urdu["urdu_headline"], "bullets": urdu["urdu_bullets"]},
                        "roman": {"headline": roman["roman_headline"], "bullets": roman["roman_bullets"]},
                        "fact_inventory": facts or {},
                        "verification_notes": verification_notes or [],
                    },
                    model=getattr(self.config, "validator_model", None) or self.config.llm_model,
                    max_tokens=1200,
                )
                if model_review.get("consistent") is False:
                    errors.append("Independent model reviewer found inconsistencies: " +
                                  "; ".join(str(x) for x in model_review.get("issues", [])[:5]))
            except AgentUnavailable as exc:
                warnings.append(f"Independent model review unavailable: {exc}")
        else:
            warnings.append("Independent model review not configured; deterministic consistency checks only.")

        return {"ok": not errors, "errors": errors, "warnings": warnings, "model_review": model_review}
