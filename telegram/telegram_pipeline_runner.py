import os
import sys

# Add repository root to search path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telegram import telegram_pipeline_worker as worker

if __name__ == "__main__":
    worker.main()
