from typing import Any, Dict, List
from duo.duo_strategy import normalize_duo_strategy

# TEST VE ESKİ BAĞIMLILIKLAR İÇİN KÖPRÜ (WRAPPER)
def normalize_conversation_map(strategy: Dict[str, Any]) -> List[Dict[str, Any]]:
    # Testler strategy'yi doğrudan veriyor, onu reels_state formatına çeviriyoruz
    fake_reels_state = {
        "duo_stratejisi": strategy,
        "konusma_haritasi": strategy.get("conversation_map") or strategy.get("konusma_haritasi", []),
        "_explicit_voice_mode": strategy.get("mode") or strategy.get("anlatim_modu", ""),
        "anlatim_modu": strategy.get("anlatim_modu", "")
    }
    normalized = normalize_duo_strategy(fake_reels_state)
    return normalized.get("conversation_map", [])

_ALLOWED_MODES = {"SOLO_FEMALE", "SOLO_MALE", "DUO"}
_ALLOWED_SPEAKERS = {"female", "male"}

def validate_script_segments(segments: Any, mode: str = "DUO") -> List[Dict[str, str]]:
    mode = str(mode or "DUO").upper()
    if mode not in _ALLOWED_MODES: mode = "DUO"
    allowed = {"female"} if mode == "SOLO_FEMALE" else {"male"} if mode == "SOLO_MALE" else _ALLOWED_SPEAKERS

    if not isinstance(segments, list): return []

    result: List[Dict[str, str]] = []
    for item in segments:
        if not isinstance(item, dict): continue
        speaker = str(item.get("speaker", "")).lower().strip()
        text = str(item.get("text", "")).strip()
        if speaker not in allowed or not text: continue
        normalized = {"speaker": speaker, "text": text}
        purpose = str(item.get("purpose") or "").strip().lower()
        reply_anchor = str(item.get("reply_anchor") or "").strip()
        if purpose: normalized["purpose"] = purpose
        if reply_anchor: normalized["reply_anchor"] = reply_anchor
        result.append(normalized)
    return result
