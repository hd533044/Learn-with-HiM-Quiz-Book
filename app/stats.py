from app.database import get_db

def get_overall_leaderboard(limit: int = 10):
    with get_db() as conn:
        with conn.cursor() as cursor:
            query = """
            SELECT u.full_name, AVG(q.score) as avg_score, COUNT(q.id) as total_tests
            FROM quiz_attempts q
            JOIN users u ON q.user_id = u.user_id
            GROUP BY u.full_name
            ORDER BY avg_score DESC
            LIMIT %s
            """
            cursor.execute(query, (limit,))
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

def calculate_user_percentile(user_id: int) -> float:
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT user_id, AVG(score) as avg_score 
                FROM quiz_attempts 
                GROUP BY user_id 
                ORDER BY avg_score ASC
            """)
            all_scores = cursor.fetchall()
            
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
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT user_id, AVG(score) as avg_score,
                       RANK() OVER (ORDER BY AVG(score) DESC) as rank_pos
                FROM quiz_attempts
                GROUP BY user_id
            """)
            rows = cursor.fetchall()
            
            for row in rows:
                if row['user_id'] == user_id:
                    pos = row['rank_pos']
                    badge = " 🥇" if pos == 1 else " 🥈" if pos == 2 else " 🥉" if pos == 3 else ""
                    return f"#{pos}{badge}"
            return "N/A"

def get_user_performance_summary(user_id: int):
    with get_db() as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT COUNT(*) as total_tests,
                       SUM(total_questions) as total_qs,
                       SUM(correct_answers) as total_correct,
                       SUM(wrong_answers) as total_wrong,
                       SUM(skipped_count) as total_skipped,
                       AVG(score) as avg_score
                FROM quiz_attempts
                WHERE user_id = %s
            """, (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else {}