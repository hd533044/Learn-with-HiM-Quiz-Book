import sqlite3
import logging
from app.config import SQLITE_DB_PATH

logger = logging.getLogger(__name__)

def get_db_connection():
    conn = sqlite3.connect(SQLITE_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_user_performance_summary(user_id: int) -> dict:
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT 
            COUNT(*) as total_tests,
            SUM(questions_attempted) as total_qs,
            SUM(correct_answers) as total_correct,
            SUM(wrong_answers) as total_wrong,
            AVG(score) as avg_score,
            MAX(score) as max_score
        FROM quiz_attempts
        WHERE user_id = ?
    """, (user_id,))
    
    row = cursor.fetchone()
    conn.close()

    if not row or row['total_tests'] == 0:
        return {
            'total_tests': 0,
            'total_qs': 0,
            'total_correct': 0,
            'total_wrong': 0,
            'avg_score': 0.0,
            'max_score': 0.0
        }

    return {
        'total_tests': row['total_tests'],
        'total_qs': row['total_qs'] or 0,
        'total_correct': row['total_correct'] or 0,
        'total_wrong': row['total_wrong'] or 0,
        'avg_score': row['avg_score'] or 0.0,
        'max_score': row['max_score'] or 0.0
    }

def get_datewise_quiz_history(user_id: int) -> list:
    """Fetches full date-wise performance breakdown for a student."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            attempt_date,
            COUNT(*) as tests_count,
            SUM(questions_attempted) as qs_count,
            SUM(correct_answers) as correct_count,
            SUM(wrong_answers) as wrong_count,
            AVG(score) as avg_score
        FROM quiz_attempts
        WHERE user_id = ?
        GROUP BY attempt_date
        ORDER BY attempt_date DESC
    """, (user_id,))
    rows = cursor.fetchall()
    conn.close()
    
    history = []
    for r in rows:
        history.append({
            'date': r['attempt_date'],
            'tests': r['tests_count'],
            'qs': r['qs_count'],
            'correct': r['correct_count'],
            'wrong': r['wrong_count'],
            'avg_score': round(r['avg_score'] or 0.0, 2)
        })
    return history

def get_user_badges(user_id: int) -> list:
    """
    Gamified Badge Logic:
    • Bronze:  >= 10 quizzes with >= 50% average score
    • Silver:  >= 12 quizzes with >= 60% average score
    • Gold:    >= 15 quizzes with >= 70% average score
    • Diamond: >= 15 quizzes with >= 75% average score
    """
    perf = get_user_performance_summary(user_id)
    total_tests = perf['total_tests']
    total_qs = perf['total_qs']
    total_correct = perf['total_correct']

    if total_qs == 0:
        return ["🌱 Beginner Scholar"]

    avg_pct = (total_correct / total_qs) * 100.0
    badges = []

    if total_tests >= 15 and avg_pct >= 75.0:
        badges.append("💎 Diamond Scholar Badge")
    if total_tests >= 15 and avg_pct >= 70.0:
        badges.append("🥇 Gold Scholar Badge")
    if total_tests >= 12 and avg_pct >= 60.0:
        badges.append("🥈 Silver Scholar Badge")
    if total_tests >= 10 and avg_pct >= 50.0:
        badges.append("🥉 Bronze Scholar Badge")

    if not badges:
        badges.append("🎯 Active Practitioner")

    return badges

def calculate_user_rank(user_id: int) -> str:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT user_id, AVG(score) as avg_score 
        FROM quiz_attempts 
        GROUP BY user_id 
        ORDER BY avg_score DESC
    """)
    rows = cursor.fetchall()
    conn.close()

    for rank, row in enumerate(rows, start=1):
        if row['user_id'] == user_id:
            return f"#{rank}"
    return "N/A"

def calculate_user_percentile(user_id: int) -> float:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT user_id, AVG(score) as avg_score 
        FROM quiz_attempts 
        GROUP BY user_id 
        ORDER BY avg_score ASC
    """)
    rows = cursor.fetchall()
    conn.close()

    total_users = len(rows)
    if total_users <= 1:
        return 100.0

    user_index = -1
    for idx, row in enumerate(rows):
        if row['user_id'] == user_id:
            user_index = idx
            break

    if user_index == -1:
        return 0.0

    percentile = (user_index / (total_users - 1)) * 100.0
    return round(percentile, 1)

def get_overall_leaderboard(limit: int = 10) -> list:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            u.full_name,
            u.user_id,
            AVG(a.score) as avg_score,
            COUNT(a.id) as tests_count
        FROM quiz_attempts a
        JOIN users u ON a.user_id = u.user_id
        GROUP BY a.user_id
        ORDER BY avg_score DESC
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]