from duo_script import normalize_conversation_map
from duo_script_engine import build_duo_generation_contract, validate_generated_duo
from duo_strategy import normalize_duo_strategy


def test_duo_map_repairs_single_speaker_without_losing_details():
    state = {
        "anlatim_modu": "DUO",
        "konusma_haritasi": [
            {"sira": 1, "speaker": "female", "amac": "hook", "detay": "Güçlü açılış"},
            {"sira": 2, "speaker": "female", "amac": "fact", "detay": "Doğrulanmış teknik detay"},
        ],
    }
    result = normalize_conversation_map(state)
    assert {item["speaker"] for item in result} == {"female", "male"}
    assert [item["detay"] for item in result] == ["Güçlü açılış", "Doğrulanmış teknik detay"]


def test_duo_map_does_not_disappear_when_raw_map_has_empty_detail_rows():
    state = {
        "anlatim_modu": "DUO",
        "konusma_haritasi": [
            {"speaker": "female", "amac": "hook", "detay": ""},
            {"speaker": "female", "amac": "fact", "detay": "Gerçek detay"},
        ],
    }
    result = normalize_conversation_map(state)
    assert result
    assert {item["speaker"] for item in result} == {"female", "male"}
    assert result[0]["detay"] == "Gerçek detay"


def test_duo_empty_map_gets_two_speaker_scaffold():
    result = normalize_conversation_map({"anlatim_modu": "DUO", "konusma_haritasi": []})
    assert len(result) >= 2
    assert {item["speaker"] for item in result} == {"female", "male"}


def test_duo_strategy_and_contract_preserve_two_speakers():
    state = {
        "anlatim_modu": "SOLO_FEMALE",
        "duo_stratejisi": {"uygunluk": "SOLO_FEMALE"},
        "konusma_haritasi": [
            {"speaker": "female", "amac": "hook", "detay": "Açılış"},
            {"speaker": "female", "amac": "closing", "detay": "Sonuç"},
        ],
    }
    plan = normalize_duo_strategy(state)
    contract = build_duo_generation_contract(plan)
    assert contract["mode"] == "DUO"
    assert {item["speaker"] for item in contract["conversation_map"]} == {"female", "male"}


def test_generated_duo_script_repairs_single_speaker_before_tts():
    contract = build_duo_generation_contract({
        "mode": "DUO",
        "conversation_map": [
            {"speaker": "female", "amac": "hook", "detay": "Açılış"},
            {"speaker": "male", "amac": "fact", "detay": "Detay"},
        ],
        "min_words": 2,
        "max_words": 50,
    })
    generated = {
        "segments": [
            {"speaker": "female", "text": "İlk cümle."},
            {"speaker": "female", "text": "İkinci cümle."},
        ]
    }
    result = validate_generated_duo(contract, generated)
    assert {item["speaker"] for item in result} == {"female", "male"}
    assert [item["text"] for item in result] == ["İlk cümle.", "İkinci cümle."]


def test_solo_validation_contract_remains_single_speaker():
    contract = build_duo_generation_contract({
        "mode": "SOLO_FEMALE",
        "conversation_map": [
            {"speaker": "female", "amac": "hook", "detay": "Açılış"},
        ],
    })
    result = validate_generated_duo(contract, {
        "segments": [{"speaker": "female", "text": "Tek ses."}]
    })
    assert result == [{"speaker": "female", "text": "Tek ses."}]
