import sqlite3
from app.database import get_db_connection

def get_overall_leaderboard(limit: int = 10):
    """Calculates top scholars based on average quiz score."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT u.full_name, AVG(CAST(q.score AS FLOAT)) as avg_score, COUNT(q.id) as total_tests
        FROM quiz_attempts q
        JOIN user_profiles u ON q.user_id = u.user_id
        GROUP BY q.user_id
        HAVING total_tests >= 1
        ORDER BY avg_score DESC, total_tests DESC
        LIMIT ?
    """, (limit,))
    
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def calculate_user_rank(user_id: int) -> str:
    """Calculates user's global leaderboard rank position."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT q.user_id, AVG(CAST(q.score AS FLOAT)) as avg_score
        FROM quiz_attempts q
        GROUP BY q.user_id
        ORDER BY avg_score DESC
    """)
    
    all_users = cursor.fetchall()
    conn.close()

    for rank, row in enumerate(all_users, start=1):
        if row["user_id"] == user_id:
            return f"#{rank}"
            
    return "Unranked"

def calculate_user_percentile(user_id: int) -> float:
    """Calculates user performance percentile against all registered scholars."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT AVG(CAST(score AS FLOAT)) as user_avg FROM quiz_attempts WHERE user_id = ?", (user_id,))
    u_row = cursor.fetchone()
    
    if not u_row or u_row["user_avg"] is None:
        conn.close()
        return 0.0

    user_avg = u_row["user_avg"]

    cursor.execute("SELECT AVG(CAST(score AS FLOAT)) as avg_score FROM quiz_attempts GROUP BY user_id")
    all_avgs = [r["avg_score"] for r in cursor.fetchall()]
    conn.close()

    if not all_avgs:
        return 100.0

    below_count = sum(1 for a in all_avgs if a < user_avg)
    percentile = (below_count / len(all_avgs)) * 100
    return round(percentile, 1)

def get_user_performance_summary(user_id: int):
    """Retrieves total tests and questions attempted by user."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT COUNT(id) as total_tests, SUM(total_questions) as total_qs, SUM(score) as total_score
        FROM quiz_attempts
        WHERE user_id = ?
    """, (user_id,))
    
    row = cursor.fetchone()
    conn.close()
    
    if not row or not row["total_tests"]:
        return {"total_tests": 0, "total_qs": 0, "total_score": 0}
        
    return {
        "total_tests": row["total_tests"],
        "total_qs": row["total_qs"] or 0,
        "total_score": row["total_score"] or 0
    }