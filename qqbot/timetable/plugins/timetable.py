# 1. 检查暂存区
git diff --cached --name-only

# 2. 若无输出（文件不在暂存区），加入暂存区
git add qqbot/timetable/plugins/timetable.py

# 3. 生成提交信息并提交（使用之前的 hash 27a3487 作为参考，或新提交）
git commit -m "feat(timetable): add /today command with auto week/day from 2026-09-07"

# 4. 推送
git push origin $(git branch --show-current)
