import random

# Fixed static lookup sets
PERCENTAGE_FRACTIONS = [
    ("1/2", "50%"),
    ("1/3", "33.33% / 33 1/3%"),
    ("1/4", "25%"),
    ("1/5", "20%"),
    ("1/6", "16.66% / 16 2/3%"),
    ("1/7", "14.28% / 14 2/7%"),
    ("1/8", "12.5% / 12 1/2%"),
    ("1/9", "11.11% / 11 1/9%"),
    ("1/10", "10%"),
    ("1/11", "9.09% / 9 1/11%"),
    ("1/12", "8.33% / 8 1/3%"),
    ("1/13", "7.69% / 7 9/13%"),
    ("1/14", "7.14% / 7 1/7%"),
    ("1/15", "6.66% / 6 2/3%"),
    ("1/16", "6.25% / 6 1/4%"),
    ("1/20", "5%"),
    ("1/25", "4%")
]

PYTHAGOREAN_TRIPLETS = [
    (3, 4, 5), (5, 12, 13), (7, 24, 25), (8, 15, 17), (9, 40, 41),
    (11, 60, 61), (12, 35, 37), (13, 84, 85), (16, 63, 65), (20, 21, 29),
    (28, 45, 53), (33, 56, 65), (36, 77, 85), (39, 80, 89), (48, 55, 73),
    (65, 72, 97), (20, 99, 101), (60, 91, 109), (15, 112, 113), (44, 117, 125)
]

BOOSTER_SETTINGS = {
    "easy": {"range": (10, 50), "timer": 6, "mult_max": 3, "add_max": 25},
    "medium": {"range": (50, 200), "timer": 6, "mult_max": 5, "add_max": 50},
    "hard": {"range": (100, 400), "timer": 6, "mult_max": 8, "add_max": 90},
    "extreme_hard": {"range": (200, 800), "timer": 6, "mult_max": 10, "add_max": 150},
    "topper": {"range": (400, 1500), "timer": 6, "mult_max": 12, "add_max": 250},
}


def get_clean_divisors(n: int, max_limit: int = 12) -> list[int]:
    """Returns valid integer divisors > 1 up to max_limit."""
    return [i for i in range(2, min(n, max_limit + 1)) if n % i == 0]


def generate_mental_chain(steps: int, difficulty: str) -> dict:
    config = BOOSTER_SETTINGS.get(difficulty.lower(), BOOSTER_SETTINGS["medium"])
    current = random.randint(*config["range"])
    
    chain = [{"step": 0, "instruction": f"🧠 Starting Base Number: {current}", "val": current}]
    prev_op = None

    for i in range(1, steps + 1):
        available_ops = ["add", "sub", "mult"]
        divisors = get_clean_divisors(current, config["mult_max"])
        if divisors:
            available_ops.append("div")

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
        "step_timer": 6,
        "total_steps": steps,
        "steps": chain,
        "final_answer": current
    }


def make_smart_numeric_options(ans: int, delta_bounds: tuple[int, int] = (1, 15)) -> list[str]:
    """Generates 4 distinct, plausible integer options avoiding any duplicate values."""
    options = {ans}
    
    offsets = [10, -10, 2, -2, 5, -5, 20, -20, 1, -1]
    random.shuffle(offsets)
    for off in offsets:
        cand = ans + off
        if cand > 0 and cand not in options:
            options.add(cand)
        if len(options) == 4:
            break

    low, high = delta_bounds
    attempts = 0
    while len(options) < 4 and attempts < 50:
        attempts += 1
        delta = random.randint(low, high) * random.choice([1, -1])
        cand = ans + delta
        if cand > 0:
            options.add(cand)

    # Deterministic fallback
    k = 1
    while len(options) < 4:
        if (ans + k) not in options:
            options.add(ans + k)
        elif (ans - k) > 0 and (ans - k) not in options:
            options.add(ans - k)
        k += 1

    opts_list = [str(x) for x in list(options)[:4]]
    random.shuffle(opts_list)
    return opts_list


def generate_operation_questions(op_type: str, difficulty: str = "medium", count: int = 10) -> list[dict]:
    questions = []
    seen_pairs = set()

    if op_type == "add":
        if difficulty == "easy":
            range_a, range_b = (10, 99), (10, 99)
        elif difficulty == "medium":
            range_a, range_b = (100, 999), (10, 99)
        elif difficulty == "hard":
            range_a, range_b = (100, 999), (100, 999)
        else:
            range_a, range_b = (1000, 9999), (100, 9999)

        attempts = 0
        while len(questions) < count and attempts < count * 40:
            attempts += 1
            a = random.randint(*range_a)
            b = random.randint(*range_b)
            pair_key = (min(a, b), max(a, b))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            
            ans = a + b
            opts_list = make_smart_numeric_options(ans, (5, 25))
            questions.append({
                "id": f"add_{a}_{b}_{random.randint(100, 999)}",
                "question": f"Calculate: {a} + {b} = ?",
                "options": opts_list,
                "correct_option": opts_list.index(str(ans)),
                "explanation": f"{a} + {b} = {ans}"
            })

    elif op_type == "sub":
        if difficulty == "easy":
            range_a, range_b = (15, 99), (10, 90)
        elif difficulty == "medium":
            range_a, range_b = (100, 999), (20, 300)
        elif difficulty == "hard":
            range_a, range_b = (200, 999), (100, 999)
        else:
            range_a, range_b = (1000, 9999), (200, 5000)

        attempts = 0
        while len(questions) < count and attempts < count * 40:
            attempts += 1
            a = random.randint(*range_a)
            b = random.randint(*range_b)
            if a <= b:
                a, b = b + random.randint(5, 50), b
            
            pair_key = (a, b)
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)

            ans = a - b
            opts_list = make_smart_numeric_options(ans, (5, 25))
            questions.append({
                "id": f"sub_{a}_{b}_{random.randint(100, 999)}",
                "question": f"Calculate: {a} − {b} = ?",
                "options": opts_list,
                "correct_option": opts_list.index(str(ans)),
                "explanation": f"{a} − {b} = {ans}"
            })

    elif op_type == "mult":
        if difficulty == "easy":
            range_a, range_b = (11, 25), (2, 9)
        elif difficulty == "medium":
            range_a, range_b = (12, 50), (6, 19)
        elif difficulty == "hard":
            range_a, range_b = (21, 99), (12, 49)
        else:
            range_a, range_b = (101, 999), (11, 35)

        attempts = 0
        while len(questions) < count and attempts < count * 40:
            attempts += 1
            a = random.randint(*range_a)
            b = random.randint(*range_b)
            pair_key = (min(a, b), max(a, b))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)

            ans = a * b
            opts_list = make_smart_numeric_options(ans, (10, 50))
            questions.append({
                "id": f"mul_{a}_{b}_{random.randint(100, 999)}",
                "question": f"Calculate: {a} × {b} = ?",
                "options": opts_list,
                "correct_option": opts_list.index(str(ans)),
                "explanation": f"{a} × {b} = {ans}"
            })

    elif op_type == "div":
        if difficulty == "easy":
            divisors = list(range(2, 12))
            quotient_range = (5, 30)
        elif difficulty == "medium":
            divisors = list(range(6, 25))
            quotient_range = (10, 60)
        elif difficulty == "hard":
            divisors = list(range(12, 45))
            quotient_range = (15, 100)
        else:
            divisors = list(range(20, 80))
            quotient_range = (30, 250)

        attempts = 0
        while len(questions) < count and attempts < count * 40:
            attempts += 1
            divisor = random.choice(divisors)
            quotient = random.randint(*quotient_range)
            dividend = divisor * quotient

            pair_key = (dividend, divisor)
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)

            opts_list = make_smart_numeric_options(quotient, (2, 10))
            questions.append({
                "id": f"div_{dividend}_{divisor}_{random.randint(100, 999)}",
                "question": f"Calculate: {dividend} ÷ {divisor} = ?",
                "options": opts_list,
                "correct_option": opts_list.index(str(quotient)),
                "explanation": f"{dividend} ÷ {divisor} = {quotient}"
            })

    return questions


def generate_static_recall_questions(category: str, count: int = 10) -> list[dict]:
    questions = []
    
    if category == "squares":
        pool = list(range(2, 51))
        random.shuffle(pool)
        selected_bases = pool[:count]

        for n in selected_bases:
            ans = n ** 2
            opts_list = make_smart_numeric_options(ans, (10, 40))
            questions.append({
                "id": f"sq_{n}_{random.randint(100, 999)}",
                "question": f"What is the square of {n} ({n}²)?",
                "options": opts_list,
                "correct_option": opts_list.index(str(ans)),
                "explanation": f"{n} × {n} = {ans}"
            })

    elif category == "cubes":
        pool = list(range(2, 31))
        random.shuffle(pool)
        selected_bases = pool[:count]

        for n in selected_bases:
            ans = n ** 3
            opts_list = make_smart_numeric_options(ans, (20, 80))
            questions.append({
                "id": f"cb_{n}_{random.randint(100, 999)}",
                "question": f"What is the cube of {n} ({n}³)?",
                "options": opts_list,
                "correct_option": opts_list.index(str(ans)),
                "explanation": f"{n} × {n} × {n} = {ans}"
            })

    elif category == "tables":
        seen_combos = set()
        attempts = 0
        while len(questions) < count and attempts < count * 30:
            attempts += 1
            t = random.randint(12, 50)
            m = random.randint(2, 20)
            if (t, m) in seen_combos:
                continue
            seen_combos.add((t, m))

            ans = t * m
            opts_list = make_smart_numeric_options(ans, (t, t * 2))
            questions.append({
                "id": f"tbl_{t}x{m}_{random.randint(100, 999)}",
                "question": f"Calculate: {t} × {m} = ?",
                "options": opts_list,
                "correct_option": opts_list.index(str(ans)),
                "explanation": f"{t} × {m} = {ans}"
            })

    elif category == "triplets":
        available_triplets = list(PYTHAGOREAN_TRIPLETS)
        random.shuffle(available_triplets)
        selected_triplets = available_triplets[:count]

        for trip in selected_triplets:
            missing_idx = random.randint(0, 2)
            ans = trip[missing_idx]
            disp = list(trip)
            disp[missing_idx] = "?"
            
            opts_list = make_smart_numeric_options(ans, (1, 10))
            questions.append({
                "id": f"trip_{trip[0]}_{trip[1]}_{random.randint(100, 999)}",
                "question": f"Identify the missing Pythagorean Triplet side: ({disp[0]}, {disp[1]}, {disp[2]})",
                "options": opts_list,
                "correct_option": opts_list.index(str(ans)),
                "explanation": f"Pythagorean triplet relation: {trip[0]}² + {trip[1]}² = {trip[2]}² ({trip[0]**2} + {trip[1]**2} = {trip[2]**2})"
            })

    elif category == "percentages":
        available_fractions = list(PERCENTAGE_FRACTIONS)
        random.shuffle(available_fractions)
        selected_items = available_fractions[:count]

        for frac, correct_pct in selected_items:
            distractor_pool = [pct for f, pct in PERCENTAGE_FRACTIONS if pct != correct_pct]
            chosen_distractors = random.sample(distractor_pool, 3)
            
            options_set = [correct_pct] + chosen_distractors
            random.shuffle(options_set)

            questions.append({
                "id": f"pct_{frac.replace('/', '_')}_{random.randint(100, 999)}",
                "question": f"Convert fraction to percentage: {frac} = ?",
                "options": options_set,
                "correct_option": options_set.index(correct_pct),
                "explanation": f"Fraction {frac} = {correct_pct}"
            })

    return questions