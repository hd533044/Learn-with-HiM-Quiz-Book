from app.database import get_db, release_db
from psycopg2.extras import RealDictCursor

def get_overall_leaderboard(limit: int = 10):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
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
    cursor.close()
    release_db(conn)
    return [dict(r) for r in rows]

def calculate_user_percentile(user_id: int) -> float:
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    # Fast single-query percentile calculation
    query = """
    WITH user_avgs AS (
        SELECT user_id, AVG(score) as avg_s
        FROM quiz_attempts
        GROUP BY user_id
    ),
    target_user AS (
        SELECT avg_s FROM user_avgs WHERE user_id = %s
    )
    SELECT 
        (SELECT COUNT(*) FROM user_avgs) as total_users,
        (SELECT COUNT(*) FROM user_avgs WHERE avg_s < (SELECT avg_s FROM target_user)) as users_below;
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
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    query = """
    WITH user_ranks AS (
        SELECT user_id, 
               RANK() OVER (ORDER BY AVG(score) DESC) as pos
        FROM quiz_attempts
        GROUP BY user_id
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
               AVG(score) as avg_score
        FROM quiz_attempts
        WHERE user_id = %s
    """, (user_id,))
    row = cursor.fetchone()
    cursor.close()
    release_db(conn)
    return dict(row) if row else {}