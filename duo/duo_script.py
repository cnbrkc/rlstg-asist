"""Production DUO/SOLO conversation-map normalization.

The normalized map feeds the active Gemini multi-speaker TTS path. DUO maps
are repaired to contain both voices; explicitly requested SOLO maps retain
only their requested speaker.
"""

from typing import Any, Dict, List

_ALLOWED_MODES = {"SOLO_FEMALE", "SOLO_MALE", "DUO"}
_ALLOWED_SPEAKERS = {"female", "male"}
_ALLOWED_PURPOSES = {
    "hook", "fact", "reaction", "challenge", "rebuttal", "explanation",
    "counterpoint", "concession", "backchannel", "transition", "punchline",
    "callback", "closing",
}


def _mode(strategy: Dict[str, Any]) -> str:
    """Accept both the model's Turkish field and normalized strategy output."""
    raw = strategy or {}
    value = raw.get("uygunluk") or raw.get("anlatim_modu") or raw.get("mode") or "DUO"
    value = str(value).upper().strip()
    return value if value in _ALLOWED_MODES else "DUO"


def _duo_scaffold() -> List[Dict[str, Any]]:
    """Return a neutral two-speaker scaffold when the creative map is unusable."""
    return [
        {"sira": 1, "speaker": "female", "amac": "hook", "detay": "en güçlü Türkiye ilgi kancasıyla net iddia", "duygu": "natural"},
        {"sira": 2, "speaker": "male", "amac": "rebuttal", "detay": "ilk iddianın belirli noktasına kısa karşılık", "duygu": "natural"},
        {"sira": 3, "speaker": "male", "amac": "fact", "detay": "karşılığı destekleyen en güçlü doğrulanmış kanıt", "duygu": "natural"},
        {"sira": 4, "speaker": "female", "amac": "counterpoint", "detay": "kanıtın Türkiye'deki gerçek kullanım karşılığı", "duygu": "natural"},
        {"sira": 5, "speaker": "male", "amac": "concession", "detay": "hak verme ve asıl sürprize dönüş", "duygu": "natural"},
        {"sira": 6, "speaker": "female", "amac": "callback", "detay": "açılışa dönen net payoff", "duygu": "natural"},
    ]


def normalize_conversation_map(strategy: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return a safe, ordered conversation map without changing editorial content.

    DUO is a production contract: a malformed/solo creative map must be repaired
    here as well because this helper is also called directly by the pipeline and
    by the script contract builder. SOLO modes retain their single-speaker rules.
    """
    mode = _mode(strategy)
    selected = (strategy or {}).get("konusma_haritasi") or (strategy or {}).get("conversation_map") or []
    if not isinstance(selected, list):
        selected = []

    allowed = {"female"} if mode == "SOLO_FEMALE" else {"male"} if mode == "SOLO_MALE" else _ALLOWED_SPEAKERS

    normalized: List[Dict[str, Any]] = []
    for item in selected:
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

    if mode == "DUO":
        if not normalized:
            normalized = _duo_scaffold()
        elif len(normalized) == 1:
            # Preserve the only real editorial detail and add one neutral turn
            # for the missing voice. The script model supplies the wording from
            # Editorial + Fact Lock; this layer does not invent factual content.
            missing = "male" if normalized[0]["speaker"] == "female" else "female"
            normalized.append({
                "sira": 2,
                "speaker": missing,
                "amac": "closing",
                "detay": "ana çıkarım",
                "duygu": "natural",
            })
        elif not any(x["speaker"] == "female" for x in normalized):
            normalized[0]["speaker"] = "female"
        elif not any(x["speaker"] == "male" for x in normalized):
            normalized[1]["speaker"] = "male"

    return normalized


def validate_script_segments(segments: Any, mode: str = "DUO") -> List[Dict[str, str]]:
    """Validate generated speaker lines before any TTS use."""
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
        normalized = {"speaker": speaker, "text": text}
        purpose = str(item.get("purpose") or "").strip().lower()
        reply_anchor = str(item.get("reply_anchor") or "").strip()
        if purpose:
            normalized["purpose"] = purpose
        if reply_anchor:
            normalized["reply_anchor"] = reply_anchor
        result.append(normalized)
    return result
