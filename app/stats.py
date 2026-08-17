from app.database import get_db, release_db
from psycopg2.extras import RealDictCursor

def get_overall_leaderboard(limit: int = 10):
    """
    Returns global leaderboard ranked fairly by Normalized Accuracy Percentage 
    (Correct Answers / Questions Attempted * 100) and average quiz score.
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
    Calculates percentile based on normalized accuracy percentage per question/test,
    ensuring a user with 5 attempts is evaluated on the exact same scale as a user with 100 attempts.
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
        HAVING SUM(questions_attempted) > 0
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

    if not row or not row.get('total_users') or row['total_users'] <= 1:
        return 100.0

    total_users = row['total_users']
    below = row['users_below'] or 0
    percentile = (below / (total_users - 1)) * 100.0
    return round(percentile, 2)

def calculate_user_rank(user_id: int) -> str:
    """
    Calculates user rank based strictly on normalized performance accuracy.
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
        HAVING SUM(questions_attempted) > 0
    ),
    user_ranks AS (
        SELECT user_id, 
               DENSE_RANK() OVER (ORDER BY normalized_accuracy DESC) as pos
        FROM user_metrics
    )
    SELECT pos FROM user_ranks WHERE user_id = %s;
    """
    cursor.execute(query, (user_id,))
    row = cursor.fetchone()
    cursor.close()
    release_db(conn)

    if not row or row.get('pos') is None:
        return "N/A"

    pos = row['pos']
    badge = " 🥇" if pos == 1 else " 🥈" if pos == 2 else " 🥉" if pos == 3 else ""
    return f"#{pos}{badge}"

def get_user_performance_summary(user_id: int):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT COUNT(*) as total_tests,
               COALESCE(SUM(total_questions), 0) as total_qs,
               COALESCE(SUM(correct_answers), 0) as total_correct,
               COALESCE(SUM(wrong_answers), 0) as total_wrong,
               COALESCE(SUM(skipped_count), 0) as total_skipped,
               CASE 
                   WHEN SUM(questions_attempted) > 0 
                   THEN ROUND(((SUM(correct_answers)::FLOAT / SUM(questions_attempted)::FLOAT) * 100.0)::NUMERIC, 2)
                   ELSE 0.0 
               END as avg_score
        FROM quiz_attempts
        WHERE user_id = %s
    """, (user_id,))
    row = cursor.fetchone()
    cursor.close()
    release_db(conn)
    return dict(row) if row else {}

def get_quiz_performance_trend(user_id: int, current_quiz_acc: float) -> dict:
    """
    Compares the current quiz score percentage against the user's historical previous quizzes.
    Returns calculated historical average, trend message, and status tag.
    """
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT questions_attempted, correct_answers
        FROM quiz_attempts
        WHERE user_id = %s
        ORDER BY id DESC
        LIMIT 10
    """, (user_id,))
    past_quizzes = cursor.fetchall()
    cursor.close()
    release_db(conn)

    if not past_quizzes:
        return {
            "past_quizzes_count": 0,
            "historical_avg": round(current_quiz_acc, 2),
            "diff": 0.0,
            "trend_label": "🌟 First Quiz Attempt",
            "trend_desc": "Great job on your first quiz! Keep practicing to track your learning curve."
        }

    total_past_qs = sum(r['questions_attempted'] for r in past_quizzes if r.get('questions_attempted'))
    total_past_corr = sum(r['correct_answers'] for r in past_quizzes if r.get('correct_answers'))
    
    historical_avg = round((total_past_corr / total_past_qs) * 100.0, 2) if total_past_qs > 0 else 0.0
    diff = round(current_quiz_acc - historical_avg, 2)

    if diff >= 10.0:
        trend_label = "🔥 Significantly Better / Outstanding"
        trend_desc = f"You scored **+{diff}% higher** than your previous average ({historical_avg}%). Fantastic progress! 🚀"
    elif 2.0 <= diff < 10.0:
        trend_label = "📈 Better than Previous Quizzes"
        trend_desc = f"You improved by **+{diff}%** compared to your past average ({historical_avg}%). Keep it up! 👏"
    elif -2.0 <= diff < 2.0:
        trend_label = "⚖️ Consistent / Equal Performance"
        trend_desc = f"Your performance matches your usual consistency at **~{historical_avg}%**."
    elif -10.0 < diff < -2.0:
        trend_label = "📉 Slightly Below Average"
        trend_desc = f"You scored **{abs(diff)}% lower** than your historical average ({historical_avg}%). Review your mistakes to rebound! 💡"
    else:
        trend_label = "🔻 Going Down / Needs Improvement"
        trend_desc = f"Performance dropped by **{abs(diff)}%** below your average ({historical_avg}%). Recommend revisiting weak topics and bookmarked questions. 🎯"

    return {
        "past_quizzes_count": len(past_quizzes),
        "historical_avg": historical_avg,
        "diff": diff,
        "trend_label": trend_label,
        "trend_desc": trend_desc
    }