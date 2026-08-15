"""Duo conversation script normalization.

This module is intentionally isolated from the production TTS/render path.
It converts the already-planned conversation map into a validated speaker
script that the future multi-speaker TTS stage can consume.
"""

from typing import Any, Dict, List

_ALLOWED_MODES = {"SOLO_FEMALE", "SOLO_MALE", "DUO"}
_ALLOWED_SPEAKERS = {"female", "male"}
_ALLOWED_PURPOSES = {
    "hook", "fact", "reaction", "challenge", "explanation", "counterpoint",
    "transition", "punchline", "closing",
}


def normalize_conversation_map(strategy: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return a safe, ordered conversation map without changing editorial content."""
    strategy = strategy or {}
    mode = str(strategy.get("uygunluk") or strategy.get("anlatim_modu") or "DUO").upper()
    if mode not in _ALLOWED_MODES:
        mode = "DUO"

    selected = strategy.get("konusma_haritasi") or []
    if not isinstance(selected, list):
        selected = []

    if mode == "SOLO_FEMALE":
        allowed = {"female"}
    elif mode == "SOLO_MALE":
        allowed = {"male"}
    else:
        allowed = _ALLOWED_SPEAKERS

    normalized: List[Dict[str, Any]] = []
    for index, item in enumerate(selected, start=1):
        if not isinstance(item, dict):
            continue
        speaker = str(item.get("speaker", "")).lower().strip()
        if speaker not in allowed:
            continue
        purpose = str(item.get("amac", "transition")).lower().strip()
        if purpose not in _ALLOWED_PURPOSES:
            purpose = "transition"
        detail = str(item.get("detay", "")).strip()
        if not detail:
            continue
        normalized.append({
            "sira": len(normalized) + 1,
            "speaker": speaker,
            "amac": purpose,
            "detay": detail,
            "duygu": str(item.get("duygu", "natural")).strip() or "natural",
        })

    return normalized


def validate_script_segments(segments: Any, mode: str = "DUO") -> List[Dict[str, str]]:
    """Validate generated speaker lines without enabling them in production yet."""
    mode = str(mode or "DUO").upper()
    if mode not in _ALLOWED_MODES:
        mode = "DUO"
    allowed = {"female"} if mode == "SOLO_FEMALE" else {"male"} if mode == "SOLO_MALE" else _ALLOWED_SPEAKERS

    if not isinstance(segments, list):
        return []

    result: List[Dict[str, str]] = []
    for item in segments:
        if not isinstance(item, dict):
            continue
        speaker = str(item.get("speaker", "")).lower().strip()
        text = str(item.get("text", "")).strip()
        if speaker not in allowed or not text:
            continue
        result.append({
            "speaker": speaker,
            "text": text,
        })
    return result


def flatten_for_legacy_tts(segments: Any) -> str:
    """Create the old plain-text representation for the existing single-voice path."""
    valid = validate_script_segments(segments, "DUO")
    return "\n".join(item["text"] for item in valid)
