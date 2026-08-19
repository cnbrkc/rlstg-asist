"""DUO/SOLO script generation and validation layer.

Turns the normalized voice plan into a strict script contract. DUO requires
both speakers; SOLO follows either an explicit user override or the model's
content-based editorial mode decision.
"""

from typing import Any, Dict, List

from duo.duo_script import normalize_conversation_map, validate_script_segments


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
    plan = plan or {}
    mode = str(plan.get("mode") or plan.get("uygunluk") or plan.get("anlatim_modu") or "DUO").upper().strip()
    if mode not in {"SOLO_FEMALE", "SOLO_MALE", "DUO"}:
        mode = "DUO"

    conversation_map = normalize_conversation_map({**plan, "anlatim_modu": mode})
    allowed = {"female"} if mode == "SOLO_FEMALE" else {"male"} if mode == "SOLO_MALE" else {"female", "male"}
    conversation_map = [x for x in conversation_map if x["speaker"] in allowed]

    hook = str(plan.get("hook_speaker") or "").lower().strip()
    ending = str(plan.get("ending_speaker") or "").lower().strip()
    if hook not in allowed:
        hook = "female" if mode != "SOLO_MALE" else "male"
    if ending not in allowed:
        ending = "male" if mode != "SOLO_FEMALE" else "female"

    speakers = []
    for speaker in ("female", "male"):
        if speaker in allowed:
            profile = CHARACTER_ROLES[speaker]
            speakers.append({"speaker": speaker, "voice": profile["name"], "role": profile["role"]})

    target_words = plan.get("target_words", plan.get("hedef_kelime"))
    min_words = plan.get("min_words", plan.get("minimum_kelime"))
    max_words = plan.get("max_words", plan.get("maksimum_kelime"))

    def _number(value):
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    target_words = _number(target_words)
    min_words = _number(min_words)
    max_words = _number(max_words)
    if target_words is not None:
        min_words = min_words if min_words is not None else max(5, round(target_words * 0.90))
        max_words = max_words if max_words is not None else max(min_words, round(target_words * 1.10))

    content_tone = str(plan.get("content_tone") or plan.get("icerik_tonu") or "dengeli").strip().lower() or "dengeli"

    return {
        "mode": mode,
        "speakers": speakers,
        "hook_speaker": hook,
        "ending_speaker": ending,
        "female_weight": float(plan.get("female_weight", plan.get("female_agirligi", 0.0)) or 0.0),
        "male_weight": float(plan.get("male_weight", plan.get("male_agirligi", 0.0)) or 0.0),
        "interaction_level": float(plan.get("interaction_level", 0.5) or 0.5),
        "humor_level": float(plan.get("humor_level", 0.3) or 0.3),
        "tension_level": float(plan.get("tension_level", 0.2) or 0.2),
        "selected_detail": str(plan.get("selected_detail", "")).strip(),
        "content_tone": content_tone,
        "target_words": target_words,
        "min_words": min_words,
        "max_words": max_words,
        "conversation_map": conversation_map,
        "rules": [
            "Yalnızca planlanmış speaker'ları kullan.",
            "Fact Lock dışına çıkma; kullanıcı notunu değiştirme.",
            "Karakterler birbirinin son fikrine doğal biçimde tepki verirken konuya yeni değer katsın.",
            "En az bir anlamlı itiraz, düzeltme, şüphe veya karşı görüş bulunsun; sahte kavga üretme.",
            "İki makul tercih tarafı oluşabiliyorsa fiyat-değer, teknik-pratik veya tasarım-kullanım ekseninde ölçülü gerilim kur.",
            "Gereksiz ping-pong, mekanik soru-cevap ve aynı fikrin tekrarını engelle.",
            "Diyalog doğal konuşma ritminde olsun; sırf iki ses var diye tek monoloğu cümle ortasından bölme.",
            "Marka/üretici hedefleme, hakaret veya düşmanca ifade üretme.",
            "Her replik videoya veya otomobil tartışmasına yeni bir değer katmalı.",
            "Seslendirme süresini korumak için hedef kelime aralığı verildiyse bunun dışına çıkma.",
            "İçerik tonunu değiştirme; seçilen ton yalnızca bilgi/yorum dengesini ve anlatım sertliğini yönetsin.",
        ],
    }


def build_generation_prompt(contract: Dict[str, Any], editorial_context: str = "", fact_lock: str = "", regeneration_instruction: str = "") -> str:
    """Return a strict JSON-only generation prompt for the model call."""
    length_rule = ""
    if contract.get("min_words") is not None and contract.get("max_words") is not None:
        length_rule = (
            f"\nKELİME/SÜRE KİLİDİ: Toplam script {contract['min_words']}-{contract['max_words']} kelime arasında olmalı "
            f"(hedef {contract.get('target_words')}). Bu sınırı aşma. Teknik bilgi yığma; en güçlü detayları seç.\n"
        )
    tone_rule = (
        f"\nİÇERİK TONU KİLİDİ: {contract.get('content_tone', 'dengeli')}. "
        "Bu runtime değerini başka bir varsayılan tonla ezme. Bilgi/yorum dengesi ve anlatım sertliği seçilen tona uygun kalmalı.\n"
    )
    if regeneration_instruction and regeneration_instruction.strip():
        length_rule += f"\n🚨 YENİDEN ÜRETİM TALİMATI:\n{regeneration_instruction.strip()}\n"

    return (
        "Sen otoXtra'nın iki karakterli otomobil anlatım yazarı olarak çalışıyorsun.\n"
        "Aşağıdaki sözleşmeye göre yalnızca JSON üret.\n\n"
        "HEDEF: Doğal, samimi ve son derece canlı bir eş/partner otomobil sohbeti. "
        "Diyaloglar yapay bir tiyatro sahnesi veya haber spikerlerinin düeti gibi asla olmamalıdır.\n\n"
        "MUTLAK KURALLAR (SAMİMİ SOHBET, MUHABBET VE ATIŞMA DİLİ):\n"
        "1. GÜNLÜK TÜRKÇE: Yazı dili veya spiker tonu kullanma. Günlük reaksiyonlar doğal yerde çıkabilir ama aynı 'yok artık/hadi canım/valla' kalıplarını kontrol listesi gibi dizme.\n"
        "2. GERÇEK KARŞILIKLILIK: Her karakter öncekinin söylediği belirli bir noktaya temas etsin ve ardından yeni bilgi, itiraz, düzeltme, kullanım örneği veya espri eklesin. Yan yana iki bağımsız monolog yazma.\n"
        "3. ÖLÇÜLÜ ÇEKİŞME: Fact Lock'un desteklediği gerçek bir tercih ekseninde en az bir anlamlı karşı görüş kur. İzleyici iki makul taraftan birini seçebilsin; fakat sahte kavga, marka/fan küçümsemesi veya dayanaksız iddia üretme.\n"
        "4. MONOLOG BÖLMEYE SON: Sırf iki kişi konuşsun diye tek monoloğu bölme. Her satır kendi amacı olan gerçek bir konuşma sırası olsun; mekanik soru-cevap ve sürekli sırayla konuşma zorunluluğu yok.\n"
        "5. İNSANİ DEĞİŞİM: Karakterlerden biri uygun bir noktada karşı tarafın hakkını verebilir, fikrini yumuşatabilir veya 'orada haklısın ama...' diyerek karşı argümana geçebilir. Her turda espri yapma.\n"
        "6. KONUŞMA HARİTASINDAKİ HER GEÇERLİ SEGMENT İÇİN BİR REPLİK ÜRET; haritayı boş bırakma.\n"
        f"{tone_rule}"
        f"{length_rule}\n"
        f"SÖZLEŞME:\n{contract}\n\n"
        f"EDITORIAL CONTEXT:\n{editorial_context}\n\n"
        f"FACT LOCK:\n{fact_lock}\n\n"
        "ÇIKTI ŞEMASI:\n"
        "{\"segments\":[{\"speaker\":\"female|male\",\"text\":\"...\"}]}\n"
        "Sadece bu JSON'u döndür."
    )


def _segment_word_count(segments: Any) -> int:
    import re
    total = 0
    for segment in segments or []:
        if isinstance(segment, dict):
            total += len(re.findall(r"\b\w+(?:[-']\w+)*\b", str(segment.get("text", "")), re.UNICODE))
    return total


def validate_generated_duo(contract: Dict[str, Any], generated: Any) -> List[Dict[str, str]]:
    """Validate the model's structured response before any TTS use.

    A DUO script is structurally invalid unless both speakers are present. This
    validator deliberately does not rewrite speaker identity: doing so could
    change the intended dramaturgy. The caller's existing regeneration/QA path
    must regenerate the script instead of silently producing a single-speaker
    result.
    """
    if isinstance(generated, dict):
        generated = generated.get("segments", [])
    mode = str(contract.get("mode", "DUO") or "DUO").upper()
    segments = validate_script_segments(generated, mode)
    if not segments:
        return []

    if mode == "DUO":
        speakers_present = {segment["speaker"] for segment in segments}
        if not {"female", "male"}.issubset(speakers_present):
            return []

    # Kelime sayısı yalnızca AŞIRI uçları yakalamak için kullanılır. Eski sürüm,
    # hedefe ±%10 uymayan (ör. 198 hedefe karşın 190 kelime) geçerli iki sesli
    # bir script'i tamamen reddedip split_for_speakers kural tabanlı yedeğine
    # (daha kısa ve daha düşük kaliteli) düşürüyordu; bu da fazladan bir
    # LLM+TTS yeniden üretim döngüsüne mal oluyordu. Süre/oran denetimi
    # pipeline'ın FFmpeg senkron katmanında yapıldığı için burada yalnızca
    # belirgin şekilde kırık/çok kısa/çok şişirilmiş çıktıları ele.
    count = _segment_word_count(segments)
    min_words = contract.get("min_words")
    max_words = contract.get("max_words")
    hard_min = 1
    if min_words is not None:
        hard_min = max(1, int(int(min_words) * 0.4))   # hedefin ~%40'ının altı = kırık
    if count < hard_min:
        return []
    if max_words is not None and count > int(max_words) * 3:   # 3 kat aşırı şişme
        return []
    return segments
