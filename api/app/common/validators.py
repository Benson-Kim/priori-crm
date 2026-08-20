import re

# ISO country code → E.164 dial code mapping
COUNTRY_DIAL_CODES: dict[str, str] = {
    "KE": "254",  # Kenya
    "UG": "256",  # Uganda
    "TZ": "255",  # Tanzania
    "RW": "250",  # Rwanda
    "US": "1",  # United States
    "GB": "44",  # United Kingdom
    "ZA": "27",  # South Africa
    "NG": "234",  # Nigeria
    "GH": "233",  # Ghana
    "ET": "251",  # Ethiopia
    "EG": "20",  # Egypt
    "IN": "91",  # India
    "CN": "86",  # China
    "AU": "61",  # Australia
    "CA": "1",  # Canada
    "FR": "33",  # France
    "DE": "49",  # Germany
    "IT": "39",  # Italy
    "ES": "34",  # Spain
    "BR": "55",  # Brazil
    "MX": "52",  # Mexico
    "AE": "971",  # UAE
    "SA": "966",  # Saudi Arabia
}

DEFAULT_DIAL_CODE = "254"  # Kenya


def empty_str_to_none(v: str | None) -> str | None:
    """Convert empty or whitespace-only strings to None."""
    if v is None:
        return None
    if isinstance(v, str) and v.strip() == "":
        return None
    return v


def normalize_phone(phone: str, country: str | None = None) -> str:
    """
    Validate and normalize a phone number to E.164 format.
    """
    # Country handling (#64): a missing country keeps the historical KE
    # default, but a country we have no dial code for must NOT fall back to
    # +254 — that silently coerced e.g. a Japanese local number into a
    # Kenyan one. For unmapped countries only full international (+...)
    # input is accepted.
    country_code = country.upper() if country else None
    if country_code is None:
        dial_code: str | None = DEFAULT_DIAL_CODE
    else:
        dial_code = COUNTRY_DIAL_CODES.get(country_code)

    cleaned = phone.strip()
    had_plus = cleaned.startswith("+")

    # Strip everything except digits
    digits = re.sub(r"[^\d]", "", cleaned[1:] if had_plus else cleaned)

    if not digits:
        raise ValueError(f"Invalid phone number: '{phone}'. No digits found.")

    if dial_code is None:
        # Unmapped country: no local-format inference possible.
        if not had_plus:
            raise ValueError(
                f"Invalid phone number: '{phone}'. For country "
                f"{country_code}, enter the number in international "
                f"format, e.g. +81312345678."
            )
        dial_code = ""

    # Expected national-number length per dial code (E.164 subscriber digits).
    national_len = {"254": 9, "1": 10, "91": 10, "44": 10}.get(dial_code)

    # Normalize to E.164 from a known shape only. The previous catch-all
    # `else` prefixed arbitrary input with the dial code, coercing malformed
    # numbers into valid-looking E.164; reject those instead.
    if had_plus:
        # Caller supplied a full international number; trust the digits as-is.
        normalized = f"+{digits}"
    elif digits.startswith(dial_code):
        normalized = f"+{digits}"
    elif digits.startswith("0"):
        normalized = f"+{dial_code}{digits[1:]}"
    elif national_len is not None and len(digits) == national_len:
        normalized = f"+{dial_code}{digits}"
    else:
        raise ValueError(
            f"Invalid phone number: '{phone}'. "
            f"Expected format for {country or 'KE'}: "
            f"+{dial_code}XXXXXXXXX, a local number starting 0, "
            f"or the {national_len}-digit national number."
        )

    if not re.match(r"^\+[1-9]\d{7,14}$", normalized):
        raise ValueError(
            f"Invalid phone number: '{phone}'. "
            f"Expected format for {country or 'KE'}: "
            f"+{dial_code}XXXXXXXXX or local format"
        )

    return normalized


def validate_country_code(v: str | None) -> str | None:
    """Validate and uppercase an ISO 3166-1 alpha-2 country code."""
    if v is None or v.strip() == "":
        return None
    v = v.strip().upper()
    if len(v) != 2 or not v.isalpha():
        raise ValueError(
            f"Country must be a valid ISO 3166-1 alpha-2 code (e.g., KE). Got: '{v}'"
        )
    return v


def capitalize_location(v: str | None) -> str | None:
    """Title-case a location name (e.g. 'nairobi' → 'Nairobi')."""
    if v is None or v.strip() == "":
        return None
    return v.strip().title()


# Password policy

# Single source of truth for the account password policy
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128


def validate_password_strength(password: str) -> str:
    """Validate a password against the shared account policy.

    Rules: 8-128 characters, at least one letter and at least one digit. Kept
    deliberately modest so it can be tightened in one place later without
    touching call sites. Returns the password unchanged on success; raises
    ``ValueError`` (surfaced by Pydantic as a 422) otherwise.
    """
    if not isinstance(password, str):
        raise ValueError("Password must be a string.")

    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(
            f"Password must be at least {PASSWORD_MIN_LENGTH} characters long."
        )
    if len(password) > PASSWORD_MAX_LENGTH:
        raise ValueError(
            f"Password must be at most {PASSWORD_MAX_LENGTH} characters long."
        )
    if not re.search(r"[A-Za-z]", password):
        raise ValueError("Password must contain at least one letter.")
    if not re.search(r"\d", password):
        raise ValueError("Password must contain at least one number.")

    return password
