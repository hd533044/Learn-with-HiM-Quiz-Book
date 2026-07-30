from app.database import get_db

def get_overall_leaderboard(limit: int = 10):
    """Calculates privacy-safe leaderboard showing ONLY user full name and average score."""
    with get_db() as conn:
        query = """
        SELECT u.full_name, AVG(q.score) as avg_score, COUNT(q.id) as total_tests
        FROM quiz_attempts q
        JOIN users u ON q.user_id = u.user_id
        GROUP BY q.user_id
        ORDER BY avg_score DESC
        LIMIT ?
        """
        rows = conn.execute(query, (limit,)).fetchall()
        return [dict(r) for r in rows]

def calculate_user_percentile(user_id: int) -> float:
    """Calculates accurate student percentile based on average score compared to total students."""
    with get_db() as conn:
        all_scores = conn.execute("""
            SELECT user_id, AVG(score) as avg_score 
            FROM quiz_attempts 
            GROUP BY user_id 
            ORDER BY avg_score ASC
        """).fetchall()
        
        if not all_scores:
            return 100.0

        total_students = len(all_scores)
        user_avg = 0.0
        students_below = 0

        for row in all_scores:
            if row['user_id'] == user_id:
                user_avg = row['avg_score']
                break

        for row in all_scores:
            if row['avg_score'] < user_avg:
                students_below += 1

        if total_students == 1:
            return 100.0

        percentile = (students_below / (total_students - 1)) * 100.0
        return round(percentile, 2)

def calculate_user_rank(user_id: int) -> str:
    with get_db() as conn:
        rows = conn.execute("""
            SELECT user_id, AVG(score) as avg_score,
                   RANK() OVER (ORDER BY AVG(score) DESC) as rank_pos
            FROM quiz_attempts
            GROUP BY user_id
        """).fetchall()
        
        for row in rows:
            if row['user_id'] == user_id:
                pos = row['rank_pos']
                badge = " 🥇" if pos == 1 else " 🥈" if pos == 2 else " 🥉" if pos == 3 else ""
                return f"#{pos}{badge}"
        return "N/A"

def get_user_performance_summary(user_id: int):
    with get_db() as conn:
        row = conn.execute("""
            SELECT COUNT(*) as total_tests,
                   SUM(total_questions) as total_qs,
                   SUM(correct_answers) as total_correct,
                   SUM(wrong_answers) as total_wrong,
                   SUM(skipped_count) as total_skipped,
                   AVG(score) as avg_score
            FROM quiz_attempts
            WHERE user_id = ?
        """, (user_id,)).fetchone()
        
        return dict(row) if row else {}