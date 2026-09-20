from typing import Any, Dict, List
from duo.duo_strategy import normalize_duo_strategy

# TEST VE ESKİ BAĞIMLILIKLAR İÇİN KÖPRÜ (WRAPPER)
def normalize_conversation_map(strategy: Dict[str, Any]) -> List[Dict[str, Any]]:
    # Eski testler yanlış speaker'ı tamamen silmemizi bekliyor.
    # Bu yüzden normalize_duo_strategy'yi değil, kendi mantığımızı kullanıyoruz.
    mode = str(strategy.get("mode") or strategy.get("anlatim_modu") or "DUO").upper().strip()
    allowed = {"female"} if mode == "SOLO_FEMALE" else {"male"} if mode == "SOLO_MALE" else {"female", "male"}
    
    raw_map = strategy.get("conversation_map") or strategy.get("konusma_haritasi", [])
    result = []
    
    for item in raw_map:
        if not isinstance(item, dict): continue
        speaker = str(item.get("speaker", "")).lower().strip()
        detail = str(item.get("detay", "")).strip()
        
        # Eski davranış: Yanlış speaker'ı tamamen filtrele
        if speaker not in allowed or not detail:
            continue
            
        purpose = str(item.get("amac") or "transition").strip().lower()
        emotion = str(item.get("duygu") or "natural").strip()
        if not emotion: emotion = "natural"
        
        result.append({
            "sira": len(result) + 1,
            "speaker": speaker,
            "amac": purpose,
            "detay": detail,
            "duygu": emotion,
        })
    
    return result

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
