import random

# Static Rapid-Recall Banks
PERCENTAGE_FRACTIONS = [
    ("1/2", "50%"), ("1/3", "33.33% / 33 1/3%"), ("1/4", "25%"),
    ("1/5", "20%"), ("1/6", "16.66% / 16 2/3%"), ("1/7", "14.28% / 14 2/7%"),
    ("1/8", "12.5% / 12 1/2%"), ("1/9", "11.11% / 11 1/9%"), ("1/10", "10%"),
    ("1/11", "9.09% / 9 1/11%"), ("1/12", "8.33% / 8 1/3%"), ("1/13", "7.69% / 7 9/13%"),
    ("1/14", "7.14% / 7 1/7%"), ("1/15", "6.66% / 6 2/3%"), ("1/16", "6.25% / 6 1/4%")
]

PYTHAGOREAN_TRIPLETS = [
    (3, 4, 5), (5, 12, 13), (7, 24, 25), (8, 15, 17), (9, 40, 41),
    (11, 60, 61), (12, 35, 37), (13, 84, 85), (16, 63, 65), (20, 21, 29),
    (28, 45, 53), (33, 56, 65), (36, 77, 85), (39, 80, 89), (48, 55, 73),
    (65, 72, 97), (20, 99, 101), (60, 91, 109), (15, 112, 113), (44, 117, 125)
]

BOOSTER_SETTINGS = {
    "easy": {"range": (10, 50), "timer": 8, "mult_max": 3, "add_max": 25},
    "medium": {"range": (50, 200), "timer": 6, "mult_max": 5, "add_max": 50},
    "hard": {"range": (100, 400), "timer": 4, "mult_max": 8, "add_max": 90},
    "extreme_hard": {"range": (200, 800), "timer": 3, "mult_max": 10, "add_max": 150},
    "topper": {"range": (400, 1500), "timer": 2, "mult_max": 12, "add_max": 250},
}


def get_clean_divisors(n: int, max_limit: int = 12) -> list[int]:
    """Returns valid integer divisors > 1 up to max_limit."""
    return [i for i in range(2, min(n, max_limit + 1)) if n % i == 0]


def generate_mental_chain(steps: int, difficulty: str) -> dict:
    """
    Generates dynamic step-by-step arithmetic chain guaranteeing:
    - Pure integer values at every step (no floats/decimals).
    - No direct repetitive operation back-to-back.
    - Positive non-zero numbers throughout.
    """
    config = BOOSTER_SETTINGS.get(difficulty.lower(), BOOSTER_SETTINGS["medium"])
    current = random.randint(*config["range"])
    
    chain = [{"step": 0, "instruction": f"🧠 Starting Base Number: {current}", "val": current}]
    prev_op = None

    for i in range(1, steps + 1):
        available_ops = ["add", "sub", "mult"]
        divisors = get_clean_divisors(current, config["mult_max"])
        if divisors:
            available_ops.append("div")

        # Avoid same operation type sequentially
        if prev_op in available_ops and len(available_ops) > 1:
            available_ops.remove(prev_op)

        chosen_op = random.choice(available_ops)
        prev_op = chosen_op

        if chosen_op == "add":
            delta = random.randint(5, config["add_max"])
            current += delta
            instr = f"+ {delta}"
        elif chosen_op == "sub":
            max_sub = min(current - 1, config["add_max"])
            delta = random.randint(5, max(5, max_sub)) if max_sub >= 5 else 1
            current -= delta
            instr = f"- {delta}"
        elif chosen_op == "mult":
            factor = random.randint(2, config["mult_max"])
            current *= factor
            instr = f"× {factor}"
        elif chosen_op == "div":
            divisor = random.choice(divisors)
            current = current // divisor
            instr = f"÷ {divisor}"

        chain.append({
            "step": i,
            "instruction": instr,
            "val": current
        })

    return {
        "difficulty": difficulty,
        "step_timer": config["timer"],
        "total_steps": steps,
        "steps": chain,
        "final_answer": current
    }


def generate_static_recall_questions(category: str, count: int = 10) -> list[dict]:
    """Generates standard speed-math MCQ flashcards."""
    questions = []
    
    if category == "squares":
        for _ in range(count):
            n = random.randint(2, 50)
            ans = n ** 2
            opts = {ans, (n + 1)**2, (n - 1)**2, ans + 10}
            while len(opts) < 4:
                opts.add(ans + random.randint(-15, 15))
            opts_list = [str(x) for x in opts]
            random.shuffle(opts_list)
            questions.append({
                "id": f"sq_{n}_{random.randint(100,999)}",
                "question": f"What is the square of {n} ({n}²)?",
                "options": opts_list,
                "correct_option": opts_list.index(str(ans)),
                "explanation": f"{n} × {n} = {ans}"
            })

    elif category == "cubes":
        for _ in range(count):
            n = random.randint(2, 30)
            ans = n ** 3
            opts = {ans, (n + 1)**3, ans + 20, ans - 20}
            while len(opts) < 4:
                opts.add(ans + random.randint(-30, 30))
            opts_list = [str(x) for x in opts]
            random.shuffle(opts_list)
            questions.append({
                "id": f"cb_{n}_{random.randint(100,999)}",
                "question": f"What is the cube of {n} ({n}³)?",
                "options": opts_list,
                "correct_option": opts_list.index(str(ans)),
                "explanation": f"{n} × {n} × {n} = {ans}"
            })

    elif category == "tables":
        for _ in range(count):
            t = random.randint(12, 50)
            m = random.randint(2, 20)
            ans = t * m
            opts = {ans, ans + t, ans - t, ans + 10}
            while len(opts) < 4:
                opts.add(ans + random.randint(-20, 20))
            opts_list = [str(x) for x in opts]
            random.shuffle(opts_list)
            questions.append({
                "id": f"tbl_{t}x{m}_{random.randint(100,999)}",
                "question": f"Calculate: {t} × {m} = ?",
                "options": opts_list,
                "correct_option": opts_list.index(str(ans)),
                "explanation": f"{t} × {m} = {ans}"
            })

    elif category == "triplets":
        for _ in range(count):
            trip = random.choice(PYTHAGOREAN_TRIPLETS)
            missing_idx = random.randint(0, 2)
            ans = trip[missing_idx]
            disp = list(trip)
            disp[missing_idx] = "?"
            opts = {ans, ans + 2, max(1, ans - 2), ans + 4}
            while len(opts) < 4:
                opts.add(ans + random.randint(1, 10))
            opts_list = [str(x) for x in opts]
            random.shuffle(opts_list)
            questions.append({
                "id": f"trip_{trip[0]}_{trip[1]}_{random.randint(100,999)}",
                "question": f"Identify the missing Pythagorean Triplet side: ({disp[0]}, {disp[1]}, {disp[2]})",
                "options": opts_list,
                "correct_option": opts_list.index(str(ans)),
                "explanation": f"Pythagorean triplet relation: {trip[0]}² + {trip[1]}² = {trip[2]}² ({trip[0]**2} + {trip[1]**2} = {trip[2]**2})"
            })

    elif category == "percentages":
        for _ in range(count):
            frac, pct = random.choice(PERCENTAGE_FRACTIONS)
            opts = {pct, "14.28%", "16.66%", "11.11%", "9.09%"}
            opts_list = list(opts)[:4]
            if pct not in opts_list:
                opts_list[0] = pct
            random.shuffle(opts_list)
            questions.append({
                "id": f"pct_{frac.replace('/', '_')}_{random.randint(100,999)}",
                "question": f"Convert fraction to percentage: {frac} = ?",
                "options": opts_list,
                "correct_option": opts_list.index(pct),
                "explanation": f"Fraction {frac} = {pct}"
            })

    return questions