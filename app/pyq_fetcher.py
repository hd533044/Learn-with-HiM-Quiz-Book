import os
import json
import random
import logging

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUESTION_BANK_DIR = os.path.join(BASE_DIR, "data", "question_bank")

def load_all_question_batches() -> list:
    """Loads all questions from JSON files inside data/question_bank/ directory."""
    questions = []
    if not os.path.exists(QUESTION_BANK_DIR):
        logger.warning(f"Question bank directory missing at: {QUESTION_BANK_DIR}")
        return questions

    try:
        files = [f for f in os.listdir(QUESTION_BANK_DIR) if f.endswith('.json')]
        for file in files:
            file_path = os.path.join(QUESTION_BANK_DIR, file)
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    questions.extend(data)
                elif isinstance(data, dict) and 'questions' in data:
                    questions.extend(data['questions'])
    except Exception as e:
        logger.error(f"Error loading question batches: {e}")

    return questions

def get_pyq_questions(count: int = 5) -> list:
    """Fetches a randomized set of PYQ questions of size 'count'."""
    all_questions = load_all_question_batches()
    if not all_questions:
        # Fallback question bank if JSON files are missing
        fallback_questions = [
            {
                "question_text": "What is the primary function of the ALU in a Computer Processor?",
                "options": ["Arithmetic & Logic operations", "Data storage", "Power regulation", "Signal routing"],
                "correct_option_id": 0,
                "explanation": "ALU (Arithmetic Logic Unit) executes arithmetic (+, -) and logical operations."
            },
            {
                "question_text": "Which memory is non-volatile and retains data after power loss?",
                "options": ["RAM", "ROM", "Cache", "Register"],
                "correct_option_id": 1,
                "explanation": "ROM (Read-Only Memory) is non-volatile memory."
            },
            {
                "question_text": "Which OSI layer is responsible for IP addressing and routing?",
                "options": ["Physical Layer", "Network Layer", "Transport Layer", "Session Layer"],
                "correct_option_id": 1,
                "explanation": "The Network Layer (Layer 3) handles IP addressing and packet routing."
            },
            {
                "question_text": "What type of malware disguises itself as legitimate software?",
                "options": ["Worm", "Trojan Horse", "Ransomware", "Keylogger"],
                "correct_option_id": 1,
                "explanation": "A Trojan Horse misleads users of its true intent by posing as standard software."
            },
            {
                "question_text": "In SQL, which clause is used to filter records in a aggregate query?",
                "options": ["WHERE", "HAVING", "GROUP BY", "ORDER BY"],
                "correct_option_id": 1,
                "explanation": "HAVING filters aggregated groups, whereas WHERE filters individual rows."
            }
        ]
        random.shuffle(fallback_questions)
        return fallback_questions[:count]

    random.shuffle(all_questions)
    return all_questions[:min(count, len(all_questions))]