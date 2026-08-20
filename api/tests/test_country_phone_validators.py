"""Non-KE country and phone validator coverage (#64).

The UI now offers the full ISO 3166-1 country list, so the shared
validators must hold up beyond the Kenyan home market:

- ``validate_country_code`` must accept ALL ISO 3166-1 alpha-2 codes —
  it is shape-based (two ASCII letters, uppercased), not a regional
  subset.
- ``normalize_phone(v, country)`` must behave sensibly for non-KE
  countries: local formats normalize against the selected country's
  dial code when we know it, full international (+...) input is always
  accepted verbatim, and a country we have no dial code for must never
  silently coerce a local number into a +254 Kenyan one (it raises a
  clear error instead).

Pure validator tests — no DB, no app fixtures.
"""

import pytest

from app.common.validators import (
    COUNTRY_DIAL_CODES,
    normalize_phone,
    validate_country_code,
)


class TestValidateCountryCodeFullIsoRange:
    """validate_country_code accepts any alpha-2 code, not a subset."""

    # A deliberately broad sample across regions, including codes that a
    # KE-centric subset would have dropped and codes with no dial-code
    # mapping in COUNTRY_DIAL_CODES.
    SAMPLE_CODES = [
        "KE", "UG", "TZ", "RW", "NG", "ZA", "EG", "MA",  # Africa
        "GB", "DE", "FR", "UA", "IS", "AX",              # Europe
        "US", "CA", "BR", "MX", "AR",                    # Americas
        "JP", "CN", "IN", "SG", "KR", "TL",              # Asia
        "AU", "NZ", "FJ", "TV",                          # Oceania
        "AQ", "BV", "UM",                                # territories
    ]

    @pytest.mark.parametrize("code", SAMPLE_CODES)
    def test_accepts_all_alpha2_codes(self, code):
        assert validate_country_code(code) == code

    @pytest.mark.parametrize("code", ["ke", "jp", "us"])
    def test_uppercases_lowercase_input(self, code):
        assert validate_country_code(code) == code.upper()

    def test_blank_becomes_none(self):
        assert validate_country_code("") is None
        assert validate_country_code("   ") is None
        assert validate_country_code(None) is None

    @pytest.mark.parametrize("bad", ["K", "KEN", "K1", "4E", "K-"])
    def test_rejects_non_alpha2_shapes(self, bad):
        with pytest.raises(ValueError):
            validate_country_code(bad)


class TestNormalizePhoneNonKenyanCountries:
    """normalize_phone with an explicit non-KE country."""

    @pytest.mark.parametrize(
        ("raw", "country", "expected"),
        [
            # Mapped countries: local 0-prefixed numbers get the right
            # dial code — not +254.
            ("0788123456", "RW", "+250788123456"),
            ("0701234567", "UG", "+256701234567"),
            ("07911123456", "GB", "+447911123456"),
            ("08031234567", "NG", "+2348031234567"),
            # Mapped countries with a known national-number length accept
            # the bare national number.
            ("2025550123", "US", "+12025550123"),
            ("9876543210", "IN", "+919876543210"),
            # Country codes are case-insensitive.
            ("0788123456", "rw", "+250788123456"),
            # Full international input is trusted verbatim for any
            # country, mapped or not.
            ("+447911123456", "GB", "+447911123456"),
            ("+81312345678", "JP", "+81312345678"),
            ("+61212345678", "AU", "+61212345678"),
        ],
    )
    def test_normalizes_against_selected_country(self, raw, country, expected):
        assert normalize_phone(raw, country) == expected

    def test_unmapped_country_never_coerces_to_kenya(self):
        # JP has no COUNTRY_DIAL_CODES entry. Before #64 a local Japanese
        # number fell back to the KE dial code and was silently stored as
        # +254312345678 — a valid-looking number in the wrong country.
        assert "JP" not in COUNTRY_DIAL_CODES
        with pytest.raises(ValueError, match="international format"):
            normalize_phone("0312345678", "JP")

    def test_unmapped_country_rejects_bare_national_number(self):
        with pytest.raises(ValueError):
            normalize_phone("312345678", "JP")

    def test_no_country_keeps_kenyan_default(self):
        # The owner-profile path calls normalize_phone without a country;
        # the KE default is unchanged behaviour.
        assert normalize_phone("0712345678") == "+254712345678"
        assert normalize_phone("0712345678", None) == "+254712345678"

    @pytest.mark.parametrize(
        ("raw", "country"),
        [
            ("not-a-phone", "DE"),
            # Mapped country, but neither international, 0-prefixed local,
            # nor a known national-number shape.
            ("12345", "DE"),
            ("", "FR"),
        ],
    )
    def test_still_rejects_garbage_for_any_country(self, raw, country):
        with pytest.raises(ValueError):
            normalize_phone(raw, country)
