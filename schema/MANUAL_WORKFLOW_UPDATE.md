# GitHub Workflow dosyaları — manuel güncelleme

Arena GitHub App `workflows` yetkisine sahip olmadığı için aşağıdaki değişiklikler PR'a eklenemez. PR merge edildikten sonra GitHub web arayüzünden bu adımları uygulayın.

## 1. Silinecek dosya

Aşağıdaki kullanılmayan eski workflow'u silin:

```text
.github/workflows/telegram-video.yml
```

## 2. Güncellenecek dosya

`.github/workflows/telegram-video-optimized.yml` dosyasının tamamını aşağıdaki içerikle değiştirin:

```yaml
name: Telegram Video Intake Optimized

on:
  workflow_dispatch:
    inputs:
      telegram_file_id:
        description: Telegram file_id (video mode)
        required: false
        default: ""
        type: string
      telegram_text:
        description: Telegram text input (text-only mode)
        required: false
        default: ""
        type: string
      telegram_chat_id:
        description: Telegram chat_id
        required: true
        type: string
      telegram_filename:
        description: Video filename
        required: false
        default: telegram_video.mp4
        type: string
      telegram_update_id:
        description: Telegram update_id
        required: false
        default: ""
        type: string
      video_note:
        description: Telegram video caption / user analysis note
        required: false
        default: ""
        type: string
      content_tone:
        description: Selected content tone
        required: true
        default: dengeli
        type: choice
        options:
          - eglence
          - dengeli
          - bilgi
          - teknik

permissions:
  contents: read

concurrency:
  group: telegram-video-${{ inputs.telegram_chat_id }}
  cancel-in-progress: true

jobs:
  telegram-video:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - name: Validate input mode
        env:
          TELEGRAM_FILE_ID: ${{ inputs.telegram_file_id }}
          TELEGRAM_TEXT: ${{ inputs.telegram_text }}
        run: |
          set -euo pipefail
          has_video=0
          has_text=0
          [ -n "$TELEGRAM_FILE_ID" ] && has_video=1
          [ -n "$TELEGRAM_TEXT" ] && has_text=1
          if [ "$has_video" -eq 1 ] && [ "$has_text" -eq 1 ]; then
            echo "Video and text inputs cannot be used together."
            exit 1
          fi
          if [ "$has_video" -eq 0 ] && [ "$has_text" -eq 0 ]; then
            echo "Either telegram_file_id or telegram_text is required."
            exit 1
          fi

      - name: Checkout
        uses: actions/checkout@v4
        with:
          fetch-depth: 1
          sparse-checkout: |
            .github/
            cloudflare/
            data/pending/
            core/
            duo/
            telegram/
            requirements.txt
          sparse-checkout-cone-mode: false

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: "pip"
          cache-dependency-path: requirements.txt

      - name: Download Telegram video
        if: ${{ inputs.telegram_file_id != '' }}
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ inputs.telegram_chat_id }}
          TELEGRAM_FILE_ID: ${{ inputs.telegram_file_id }}
          TELEGRAM_FILENAME: ${{ inputs.telegram_filename }}
        run: python -m telegram.telegram_webhook_intake

      - name: Install Python dependencies
        run: pip install --disable-pip-version-check -r requirements.txt

      - name: Verify ffmpeg binary from imageio-ffmpeg
        run: |
          python - << 'PY'
          import imageio_ffmpeg
          p = imageio_ffmpeg.get_ffmpeg_exe()
          print("imageio ffmpeg:", p)
          PY

      - name: Compile check
        run: python -m compileall -q core duo telegram

      - name: Run Reels pipeline
        env:
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
          GEMINI_API_KEYS: ${{ secrets.GEMINI_API_KEYS }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ inputs.telegram_chat_id }}
          TEXT_INPUT: ${{ inputs.telegram_text }}
          VIDEO_ANALYSIS_NOTE: ${{ inputs.video_note }}
          CONTENT_TONE: ${{ inputs.content_tone }}
          PYTHONUNBUFFERED: "1"
        run: |
          echo "::group::Reels pipeline ayrıntılı zamanlama"
          /usr/bin/time -v python -m telegram.telegram_pipeline_social_entry
          echo "::endgroup::"

      - name: Publish diagnostic summary
        if: ${{ always() }}
        run: |
          {
            echo "## Reels pipeline diagnostics"
            echo "- Workflow: $GITHUB_WORKFLOW"
            echo "- Run ID: $GITHUB_RUN_ID"
            echo "- Attempt: $GITHUB_RUN_ATTEMPT"
            echo "- Result JSON exists: $([ -f pipeline_result.json ] && echo yes || echo no)"
            if [ -f pipeline_result.json ]; then
              python - <<'PY'
          import json
          from pathlib import Path
          p = Path("pipeline_result.json")
          data = json.loads(p.read_text(encoding="utf-8"))
          print(f"- Result size: {p.stat().st_size} bytes")
          print(f"- QA pass: {data.get('qa_pass')}")
          print(f"- QA regeneration rounds: {data.get('qa_regeneration_rounds', 0)}")
          print(f"- Voice mode: {data.get('voice_mode', 'unknown')}")
          print(f"- Warnings: {len(data.get('warnings') or [])}")
          print(f"- Errors: {len(data.get('errors') or [])}")
          PY
            fi
          } >> "$GITHUB_STEP_SUMMARY"
```

> Önemli: `Run Reels pipeline` adımına eski `VIDEO_FILES: ...telegram_filename...` satırını geri eklemeyin. İndirilen ve sanitize edilmiş gerçek dosya yolu `telegram_webhook_intake.py` tarafından `GITHUB_ENV` üzerinden aktarılır.

## 3. Oluşturulacak dosya

`.github/workflows/ci.yml` dosyasını oluşturup aşağıdaki içeriği yapıştırın:

```yaml
name: CI

on:
  pull_request:
  push:
    branches:
      - main

permissions:
  contents: read

jobs:
  test:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    env:
      GEMINI_API_KEY: test-only-not-a-real-key
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
          cache-dependency-path: requirements.txt

      - name: Install dependencies
        run: |
          pip install --disable-pip-version-check -r requirements.txt
          pip install --disable-pip-version-check pytest ruff pyyaml

      - name: Compile Python
        run: python -m compileall -q core duo telegram tests

      - name: Unit tests
        run: pytest -q

      - name: Fatal lint checks
        run: ruff check core duo telegram tests --select F,E9

      - name: Validate workflow YAML
        run: |
          python - <<'PY'
          from pathlib import Path
          import yaml
          for path in Path('.github/workflows').glob('*.yml'):
              yaml.safe_load(path.read_text(encoding='utf-8'))
              print('OK', path)
          PY

      - name: Validate Cloudflare Worker syntax
        run: node --check cloudflare/telegram-webhook.js
```
