@echo off
cd /d "D:\Work\Projects\Web\process-safety-monitor"

:: Run the news monitor
python main.py

:: Push updated data to GitHub
git add docs/data/events.json
git diff --cached --quiet || (
    git commit -m "Update events data"
    git push
)
