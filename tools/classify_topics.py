import os
import json
import re

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
QB_DIR = os.path.join(DATA_DIR, "question_bank")
TOPIC_DIR = os.path.join(DATA_DIR, "topics")

os.makedirs(TOPIC_DIR, exist_ok=True)

# Keyword dictionary for accurate classification
TOPIC_RULES = {
    "MS_Excel": [
        "excel", "formula", "sum(", "average", "ceiling", "floor", "mod(", 
        "sumsq", "datevalue", "sparkline", "cell", "worksheet", "workbook", 
        "autofill", "flash fill", "formula bar", "absolute reference", "$", 
        "countif", "counta", "eomonth", "row", "column letter", "एक्सेल", "स्प्रेडशीट", "सेल"
    ],
    "MS_Word": [
        "word", "ms-word", "docm", "docx", "mail merge", "drop cap", 
        "watermark", "margin", "toggle case", "paragraph", "clipboard", 
        "spelling and grammar", "page setup", "bookmark", "exclusion dictionary", "वर्ड", "दस्तावेज़"
    ],
    "MS_PowerPoint_365": [
        "powerpoint", "presentation", "slide", "pptx", "pptm", "onenote", "publisher", "access", "पावरपॉइंट", "स्लाइड"
    ],
    "Networking_Internet": [
        "network", "lan", "wan", "man", "pan", "internet", "intranet", "extranet", 
        "router", "switch", "hub", "gateway", "repeater", "url", "protocol", "http", 
        "tcp", "ip", "ipv4", "ipv6", "dns", "domain", "tld", "icann", "adsl", "modem", 
        "broadband", "browser", "chrome", "mosaic", "search engine", "email", "gmail", 
        "gprs", "ndp", "simplex", "half-duplex", "full-duplex", "nas", "fddi", "cdma",
        "नेटवर्क", "इंटरनेट", "ब्राउज़र", "राउटर", "स्विच", "प्रोटोकॉल"
    ],
    "Computer_Hardware_Architecture": [
        "cpu", "alu", "control unit", "processor", "motherboard", "register", 
        "port", "serial port", "parallel port", "usb", "firewire", "eniac", "edvac", 
        "univac", "generation", "vacuum tube", "transistor", "vlsi", "ulsi", 
        "microprocessor", "supercomputer", "param", "intel", "babbage", "हार्डवेयर", "प्रोसेसर", "पीढ़ी", "पोर्ट"
    ],
    "Memory_Storage": [
        "ram", "rom", "cache", "prom", "eprom", "sram", "dram", "volatile", 
        "secondary memory", "hard disk", "solid - state", "ssd", "magnetic tape", 
        "floppy", "pen drive", "virtual memory", "associative memory", "cam", "mar", "मेमोरी", "स्टोरेज", "डिस्क"
    ],
    "Cybersecurity_Malware": [
        "malware", "virus", "trojan", "ransomware", "keylogger", "logic bomb", 
        "hacker", "cracker", "white hat", "black hat", "ethical", "digital signature", 
        "authentication", "crypto locker", "spam", "phishing", "मैलवेयर", "वायरस", "हैकिंग", "रैनसमवेयर"
    ],
    "Operating_Systems_CLI": [
        "operating system", "os", "ms-dos", "command interpreter", "booting", 
        "hanging", "deadlock", "cli", "gui", "windows", "desktop", "taskbar", 
        "cursor", "recycle bin", "ऑपरेटिंग सिस्टम", "विंडोज", "बूटिंग"
    ],
    "Number_Systems": [
        "binary", "octal", "hexadecimal", "decimal", "ascii", "powers of 8", 
        "powers of 16", "msd", "lsd", "ddl", "बाइनरी", "हेक्साडेसिमल", "दशमलव", "ऑक्टल"
    ]
}

def detect_topic(question_text: str, explanation_text: str = "") -> str:
    combined = f"{question_text} {explanation_text}".lower()
    for topic, keywords in TOPIC_RULES.items():
        for kw in keywords:
            if re.search(r'\b' + re.escape(kw) + r'\b', combined):
                return topic
    return "General_Computer_Awareness"

def process_and_categorize(file_path: str, lang: str):
    if not os.path.exists(file_path):
        print(f"[-] File not found: {file_path}")
        return

    with open(file_path, "r", encoding="utf-8") as f:
        questions = json.load(f)

    categorized_data = {}
    for q in questions:
        q_text = q.get("question", "")
        exp_text = q.get("explanation", "")
        topic = detect_topic(q_text, exp_text)
        
        q["chapter"] = topic.replace("_", " ")
        if topic not in categorized_data:
            categorized_data[topic] = []
        categorized_data[topic].append(q)

    lang_dir = os.path.join(TOPIC_DIR, lang)
    os.makedirs(lang_dir, exist_ok=True)
    
    print(f"\n[*] --- Category Breakdown ({lang.upper()}) ---")
    for topic, q_list in sorted(categorized_data.items()):
        topic_file = os.path.join(lang_dir, f"{topic}_{lang}.json")
        with open(topic_file, "w", encoding="utf-8") as tf:
            json.dump(q_list, tf, indent=4, ensure_ascii=False)
        print(f"  [✓] {topic}: Created -> {os.path.basename(topic_file)}")

if __name__ == "__main__":
    print("=" * 60)
    print("  QUIZ WITH HIM - TOPIC CLASSIFIER (913 QUESTIONS)")
    print("=" * 60)

    eng_file = os.path.join(QB_DIR, "all_questions_english.json")
    hi_file = os.path.join(QB_DIR, "hindi", "all_questions_hindi.json")

    process_and_categorize(eng_file, "en")
    process_and_categorize(hi_file, "hi")
    print("\n[SUCCESS] All topics cleanly created inside data/topics/ folder!")