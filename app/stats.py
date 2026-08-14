from app.database import get_db, release_db
from psycopg2.extras import RealDictCursor

def get_overall_leaderboard(limit: int = 10):
    """
    Returns global leaderboard ranked by Normalized Accuracy Percentage 
    (Correct Answers / Questions Attempted * 100) and total tests completed.
    """
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    query = """
    SELECT u.full_name,
           CASE 
               WHEN SUM(q.questions_attempted) > 0 
               THEN ROUND(((SUM(q.correct_answers)::NUMERIC / SUM(q.questions_attempted)::NUMERIC) * 100.0), 2)
               ELSE 0.0 
           END as avg_score,
           COUNT(q.id) as total_tests
    FROM quiz_attempts q
    JOIN users u ON q.user_id = u.user_id
    GROUP BY u.user_id, u.full_name
    HAVING SUM(q.questions_attempted) > 0
    ORDER BY avg_score DESC, total_tests DESC
    LIMIT %s
    """
    cursor.execute(query, (limit,))
    rows = cursor.fetchall()
    cursor.close()
    release_db(conn)
    return [dict(r) for r in rows]

def calculate_user_percentile(user_id: int) -> float:
    """
    Fair Normalized Percentile Calculation:
    Normalizes every user's total score into an accuracy percentage rating
    (total_correct / total_attempted * 100) so test count does not inflate percentile.
    """
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    query = """
    WITH user_metrics AS (
        SELECT 
            user_id,
            CASE 
                WHEN SUM(questions_attempted) > 0 
                THEN (SUM(correct_answers)::FLOAT / SUM(questions_attempted)::FLOAT) * 100.0
                ELSE 0.0 
            END as normalized_accuracy
        FROM quiz_attempts
        GROUP BY user_id
    ),
    target_user AS (
        SELECT normalized_accuracy FROM user_metrics WHERE user_id = %s
    )
    SELECT 
        (SELECT COUNT(*) FROM user_metrics) as total_users,
        (SELECT COUNT(*) FROM user_metrics WHERE normalized_accuracy < (SELECT normalized_accuracy FROM target_user)) as users_below;
    """
    cursor.execute(query, (user_id,))
    row = cursor.fetchone()
    cursor.close()
    release_db(conn)

    if not row or not row['total_users'] or row['total_users'] <= 1:
        return 100.0

    total_users = row['total_users']
    below = row['users_below'] or 0
    percentile = (below / (total_users - 1)) * 100.0
    return round(percentile, 2)

def calculate_user_rank(user_id: int) -> str:
    """
    Calculates user rank based on fair Normalized Accuracy Rating.
    """
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    query = """
    WITH user_metrics AS (
        SELECT 
            user_id,
            CASE 
                WHEN SUM(questions_attempted) > 0 
                THEN (SUM(correct_answers)::FLOAT / SUM(questions_attempted)::FLOAT) * 100.0
                ELSE 0.0 
            END as normalized_accuracy,
            COUNT(id) as test_count
        FROM quiz_attempts
        GROUP BY user_id
    ),
    user_ranks AS (
        SELECT user_id, 
               RANK() OVER (ORDER BY normalized_accuracy DESC, test_count DESC) as pos
        FROM user_metrics
    )
    SELECT pos FROM user_ranks WHERE user_id = %s;
    """
    cursor.execute(query, (user_id,))
    row = cursor.fetchone()
    cursor.close()
    release_db(conn)

    if not row:
        return "N/A"

    pos = row['pos']
    badge = " 🥇" if pos == 1 else " 🥈" if pos == 2 else " 🥉" if pos == 3 else ""
    return f"#{pos}{badge}"

def get_user_performance_summary(user_id: int):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT COUNT(*) as total_tests,
               SUM(total_questions) as total_qs,
               SUM(correct_answers) as total_correct,
               SUM(wrong_answers) as total_wrong,
               SUM(skipped_count) as total_skipped,
               CASE 
                   WHEN SUM(questions_attempted) > 0 
                   THEN (SUM(correct_answers)::FLOAT / SUM(questions_attempted)::FLOAT) * 100.0
                   ELSE 0.0 
               END as avg_score
        FROM quiz_attempts
        WHERE user_id = %s
    """, (user_id,))
    row = cursor.fetchone()
    cursor.close()
    release_db(conn)
    return dict(row) if row else {}