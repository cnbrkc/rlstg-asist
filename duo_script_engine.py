"""Safe Duo script preparation layer.

This module turns the already-normalized Duo plan into a strict generation
contract for a future multi-speaker TTS stage. It does not call the model or
change the legacy single-speaker production path yet.
"""

from typing import Any, Dict, List

from duo_script import normalize_conversation_map, validate_script_segments


CHARACTER_ROLES = {
    "female": {
        "name": "Autonoe",
        "role": "eş/partner karakteri; doğal, zeki, gerektiğinde esprili ve hafif meydan okuyucu",
    },
    "male": {
        "name": "Charon",
        "role": "eş/partner karakteri; otomobil meraklısı, sakin ama gerektiğinde net ve hafif iddialı",
    },
}


def build_duo_generation_contract(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Build a model-independent contract for generating speaker dialogue."""
    mode = str((plan or {}).get("mode") or "DUO").upper()
    if mode not in {"SOLO_FEMALE", "SOLO_MALE", "DUO"}:
        mode = "DUO"

    conversation_map = normalize_conversation_map(plan or {})
    allowed = {"female"} if mode == "SOLO_FEMALE" else {"male"} if mode == "SOLO_MALE" else {"female", "male"}
    conversation_map = [x for x in conversation_map if x["speaker"] in allowed]

    hook = str((plan or {}).get("hook_speaker") or "").lower().strip()
    ending = str((plan or {}).get("ending_speaker") or "").lower().strip()
    if hook not in allowed:
        hook = "female" if mode != "SOLO_MALE" else "male"
    if ending not in allowed:
        ending = "male" if mode != "SOLO_FEMALE" else "female"

    speakers = []
    for speaker in ("female", "male"):
        if speaker in allowed:
            profile = CHARACTER_ROLES[speaker]
            speakers.append({"speaker": speaker, "voice": profile["name"], "role": profile["role"]})

    return {
        "mode": mode,
        "speakers": speakers,
        "hook_speaker": hook,
        "ending_speaker": ending,
        "female_weight": float((plan or {}).get("female_weight", 0.0)),
        "male_weight": float((plan or {}).get("male_weight", 0.0)),
        "interaction_level": float((plan or {}).get("interaction_level", 0.5)),
        "humor_level": float((plan or {}).get("humor_level", 0.3)),
        "tension_level": float((plan or {}).get("tension_level", 0.2)),
        "conversation_map": conversation_map,
        "rules": [
            "Yalnızca planlanmış speaker'ları kullan.",
            "Fact Lock dışına çıkma; kullanıcı notunu değiştirme.",
            "Karakterler birbirine değil konuya tepki versin.",
            "Gereksiz ping-pong ve aynı fikrin tekrarını engelle.",
            "Diyalog doğal konuşma ritminde olsun; sırf iki ses var diye cümleleri bölme.",
            "Marka/üretici hedefleme, hakaret veya düşmanca ifade üretme.",
            "Her replik videoya veya otomobil tartışmasına yeni bir değer katmalı.",
        ],
    }


def build_generation_prompt(contract: Dict[str, Any], editorial_context: str = "", fact_lock: str = "") -> str:
    """Return a strict JSON-only generation prompt for a later model call."""
    return (
        "Sen otoXtra'nın iki karakterli otomobil anlatım yazarı olarak çalışıyorsun.\n"
        "Aşağıdaki sözleşmeye göre yalnızca JSON üret.\n\n"
        "HEDEF: Doğal bir eş/partner otomobil sohbeti. Diyalog yapay skeç gibi olmayacak. "
        "İki kişi sırf konuşsun diye gereksiz replik eklenmeyecek.\n\n"
        f"SÖZLEŞME:\n{contract}\n\n"
        f"EDITORIAL CONTEXT:\n{editorial_context}\n\n"
        f"FACT LOCK:\n{fact_lock}\n\n"
        "ÇIKTI ŞEMASI:\n"
        "{\"segments\":[{\"speaker\":\"female|male\",\"text\":\"...\"}]}\n"
        "Sadece bu JSON'u döndür."
    )


def validate_generated_duo(contract: Dict[str, Any], generated: Any) -> List[Dict[str, str]]:
    """Validate model output against the selected mode before any TTS use."""
    return validate_script_segments(generated, contract.get("mode", "DUO"))
