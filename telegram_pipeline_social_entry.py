"""Single Telegram pipeline entrypoint.

Social delivery is intentionally owned by telegram_pipeline_worker.  The old
monkey-patch layer tried to reconstruct the Instagram caption from Telegram's
video-caption field, which is limited to 1024 characters, and could therefore
truncate or duplicate social output.  Keep this entrypoint as a thin launcher
so the existing workflow path remains unchanged.
"""

import telegram_pipeline_guard  # noqa: F401,E402
