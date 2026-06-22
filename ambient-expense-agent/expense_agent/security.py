import re

_PII_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    # SSN with keyword label: "SSN 143-00-0000", "SSN number is 14300000000",
    # "social security number 123456789".  Catches dashed, undashed, and
    # spaced digit strings following the label with optional "number"/"is" connectors.
    (
        re.compile(
            r"(?:ssn(?:\s+number)?|social\s+security(?:\s+(?:number|no\.?|#))?)"
            r"\s*(?:is\s*)?[:\s#]*\d[\d\s\-]{6,}",
            re.IGNORECASE,
        ),
        "[SSN REDACTED]",
        "SSN",
    ),
    # SSN canonical dashed form without keyword: 123-45-6789
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN REDACTED]", "SSN"),
    # Credit-card: 16 digits with optional space or dash separators
    (
        re.compile(r"(?<!\d)\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}(?!\d)"),
        "[CARD REDACTED]",
        "credit_card",
    ),
]

# Patterns that are characteristic of prompt-injection attempts.
# Written as an alternation so a single search() suffices.
_INJECTION_RE = re.compile(
    r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|rules?|prompts?)"
    r"|forget\s+(your|the)\s+(rules?|instructions?)"
    r"|you\s+(must|should|shall|will)\s+approve"
    r"|auto[- ]?approve"
    r"|bypass\s+(review|approval|rules?)"
    r"|override\s+(the\s+)?(rules?|instructions?|approval)"
    r"|always\s+approve"
    r"|never\s+reject"
    r"|disregard\s+(the\s+)?(rules?|instructions?)"
    r"|act\s+as\s+if"
    r"|pretend\s+(you|to\s+be)"
    r"|\[system\]"
    r"|###\s*system",
    re.IGNORECASE,
)


def scrub_pii(text: str) -> tuple[str, list[str]]:
    """Replace SSNs and credit-card numbers in *text*.

    Returns (scrubbed_text, list_of_redacted_categories) so callers can
    record which categories were found without retaining the raw values.
    """
    redacted: list[str] = []
    for pattern, replacement, category in _PII_PATTERNS:
        scrubbed = pattern.sub(replacement, text)
        if scrubbed != text and category not in redacted:
            redacted.append(category)
        text = scrubbed
    return text, redacted


def detect_prompt_injection(text: str) -> bool:
    """Return True if *text* contains patterns consistent with a prompt-injection attack."""
    return bool(_INJECTION_RE.search(text))
