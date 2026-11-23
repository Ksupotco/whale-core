"""Phone privacy: what may be kept from a phone the owner uses every day.

Two kinds of phone (``device.role``):

* ``agent``    — the AI's own phone. It has every capability; everything it
  experiences is kept, unfiltered.
* ``personal`` — the owner's everyday phone. Everything is recorded by default
  EXCEPT:
    1. system privacy screens, never kept: lock screen / keyguard, system UI
       overlays (notification shade, quick settings), input methods (they show
       what is being typed, including passwords), password/secure fields and
       FLAG_SECURE windows, security centres, permission and credential dialogs,
       password managers and authenticators;
    2. apps and web domains the owner excludes (exact host or ``*.domain``);
    3. events the phone itself classifies ``owner_sensitive``.
  Kept text has one-time codes, bank card and ID numbers masked.

The rules follow ScreenMemo's capture policy (keyguard check, system UI /
IME / launcher / automation-helper package skips, per-app selection, domain
rules), adapted from "don't screenshot" to "don't remember". Filtering happens
at ingest: a dropped event is stored only as a content-free tombstone
(event id + reason), so dedupe still works and nothing private is persisted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

ROLES = ("agent", "personal")

SYSTEM_PRIVATE_PACKAGES = {
    # system UI overlays (shade, quick settings, lock screen) — ScreenMemo transientOverlayPackages
    "com.android.systemui", "com.miui.systemui", "com.google.android.systemui", "com.samsung.android.systemui",
    "com.oppo.systemui", "com.coloros.systemui", "com.vivo.systemui", "com.huawei.systemui",
    "com.hihonor.systemui", "com.oplus.systemui",
    # security centres, permission / credential / install dialogs
    "com.miui.securitycenter", "com.huawei.systemmanager", "com.hihonor.systemmanager", "com.coloros.safecenter",
    "com.oplus.safecenter", "com.iqoo.secure", "com.samsung.android.lool",
    "com.android.permissioncontroller", "com.google.android.permissioncontroller",
    "com.android.packageinstaller", "com.google.android.packageinstaller", "com.android.credentialmanager",
    "com.android.keychain", "com.android.settings.intelligence",
    # system settings (accounts, Wi-Fi passwords, biometrics live here)
    "com.android.settings", "com.miui.settings", "com.coloros.settings", "com.oplus.settings",
    "com.huawei.android.settings",
    # password managers & authenticators
    "com.x8bit.bitwarden", "com.onepassword.android", "com.agilebits.onepassword", "keepass2android.keepass2android",
    "com.kunzisoft.keepass.free", "com.lastpass.lpandroid", "com.dashlane",
    "com.google.android.apps.authenticator2", "com.azure.authenticator", "com.authy.authy",
}
SYSTEM_PRIVATE_PREFIXES = ("com.android.systemui.", "com.miui.securitycenter.")
# input methods — ScreenMemo ImeExclusionService regexes
IME_PATTERNS = [re.compile(p, re.I) for p in (
    r"inputmethod", r"(^|\.)ime(\.|$)", r"keyboard", r"pinyin", r"sogou", r"baidu\.input", r"iflytek",
    r"swiftkey", r"qq(input|\.input)")]
# extras flags a phone may set on a screen event
PRIVATE_FLAGS = ("locked", "keyguard", "secure", "flag_secure", "password", "password_field", "private_window")

SECRET_PATTERNS = [
    (re.compile(r"(验证码|校验码|动态码|verification code|code)[^\d]{0,6}(\d{4,8})", re.I), lambda m: m.group(1) + "：******"),
    # ID numbers first: an 18-character ID would otherwise look like a card number
    (re.compile(r"(?<![\dXx])\d{17}[\dXx](?![\dXx])"), lambda m: "[证件号已隐藏]"),
    (re.compile(r"(?<!\d)(\d{4}[ -]?){3}\d{1,7}(?!\d)"), lambda m: "[卡号已隐藏]"),
]


def default_settings() -> dict[str, Any]:
    return {"excluded_apps": [], "excluded_domains": [], "redact_secrets": True, "drop_sensitive": True}


def clean_settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    out = default_settings()
    raw = raw or {}
    apps = [str(a).strip() for a in raw.get("excluded_apps") or [] if str(a).strip()]
    out["excluded_apps"] = sorted(set(a for a in apps if re.fullmatch(r"[A-Za-z0-9_.]{2,128}", a)))[:500]
    doms = []
    for d in raw.get("excluded_domains") or []:
        d = str(d).strip().lower()
        d = urlparse(d).hostname or d if "://" in d else d
        if re.fullmatch(r"(\*\.)?[a-z0-9.-]{2,253}", d or ""):
            doms.append(d)
    out["excluded_domains"] = sorted(set(doms))[:500]
    for key in ("redact_secrets", "drop_sensitive"):
        if key in raw:
            out[key] = bool(raw[key])
    return out


def is_system_private(package: str) -> bool:
    if not package:
        return False
    if package in SYSTEM_PRIVATE_PACKAGES or package.startswith(SYSTEM_PRIVATE_PREFIXES):
        return True
    return any(p.search(package) for p in IME_PATTERNS)


def _host_of(extras: dict[str, Any]) -> str:
    for key in ("domain", "host", "url"):
        v = extras.get(key)
        if isinstance(v, str) and v:
            return (urlparse(v).hostname or "") if "://" in v else v.lower()
    return ""


def domain_excluded(host: str, rules: list[str]) -> bool:
    host = host.lower().strip(".")
    for r in rules:
        if r.startswith("*."):
            base = r[2:]
            if host == base or host.endswith("." + base):
                return True
        elif host == r:
            return True
    return False


def redact(text: str) -> str:
    for pattern, repl in SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text


@dataclass
class Verdict:
    keep: bool
    reason: str = ""
    event: dict[str, Any] = field(default_factory=dict)


def screen(event: dict[str, Any], role: str, settings: dict[str, Any]) -> Verdict:
    """Apply the role's policy to one ExperienceEvent payload."""
    if role == "agent":
        return Verdict(True, "", event)
    extras = event.get("extras") if isinstance(event.get("extras"), dict) else {}
    package = str(extras.get("package") or extras.get("app") or "")
    if any(extras.get(flag) is True for flag in PRIVATE_FLAGS):
        return Verdict(False, "system_private_screen")
    if is_system_private(package):
        return Verdict(False, "system_private_app")
    if package and package in settings.get("excluded_apps", []):
        return Verdict(False, "excluded_app")
    host = _host_of(extras)
    if host and domain_excluded(host, settings.get("excluded_domains", [])):
        return Verdict(False, "excluded_domain")
    if settings.get("drop_sensitive", True) and event.get("privacy_class") == "owner_sensitive":
        return Verdict(False, "phone_marked_sensitive")
    kept = dict(event)
    if settings.get("redact_secrets", True):
        kept["summary"] = redact(str(event.get("summary") or "")) or "（已脱敏）"
    return Verdict(True, "", kept)


def describe() -> dict[str, Any]:
    """Built-in rules for the console (read-only)."""
    return {"system_packages": sorted(SYSTEM_PRIVATE_PACKAGES), "ime_patterns": [p.pattern for p in IME_PATTERNS],
            "screen_flags": list(PRIVATE_FLAGS),
            "redaction": ["一次性验证码", "银行卡号", "身份证号"]}
