import os
import random
import pytz
from datetime import datetime
from app.typing_docx import build_typing_docx
from app.typing_pdf import build_typing_pdf

IST = pytz.timezone("Asia/Kolkata")
VAULT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "typing_vault"))
os.makedirs(VAULT_DIR, exist_ok=True)

# Curated thematic blocks to assemble balanced 600-word passages
ENGLISH_THEMES = [
    [
        "Public administration in modern governance plays a pivotal role in formulating and executing key national initiatives. ",
        "The civil services and paramilitary establishments constitute the permanent operational framework of the state. ",
        "Constitutional integrity demands that institutions function with transparency, efficiency, and dedication to justice. ",
        "Technological integration in document verification and communication networks has revolutionized service delivery. ",
        "Every official document requires precision, standard grammar, and careful scrutiny before formal execution. ",
        "Effective coordination between ministerial departments ensures systematic execution of social welfare programs. ",
        "The security personnel deployed in border outposts maintain constant readiness under demanding territorial terrain. ",
        "Economic modernization requires sustainable planning, disciplined fiscal policies, and strategic infrastructure projects. ",
        "A nation's administrative strength lies in upholding procedural accountability and equal treatment under the rule of law. ",
        "Systematic record management, keyboard proficiency, and typing accuracy prevent transcription errors in state records. "
    ],
    [
        "India's defense framework operates on the foundational principles of vigilance, operational unity, and integrity. ",
        "The Central Armed Police Forces render round-the-clock service to preserve internal security and public order. ",
        "Discipline in official correspondence requires clarity of thought, objective terminology, and meticulous grammar. ",
        "Modern clerical cadres serve as the backbone of departmental correspondence, documentation, and office automation. ",
        "A candidate preparing for competitive assessment must prioritize high typographic accuracy over reckless speed. ",
        "Technological developments in office management have streamlined dispatch systems, digital archiving, and public tenders. ",
        "Administrative reforms periodically reinforce ethics, prompt resolution of public grievances, and institutional vigilance. ",
        "The judicial mechanisms establish comprehensive precedents ensuring that fundamental rights are protected and upheld. ",
        "Environmental stewardship and renewable power transitions represent strategic national commitments for future stability. ",
        "Consistent daily practice with authentic printed paragraphs enables aspirants to attain the prescribed speed standards. "
    ]
]

HINDI_THEMES = [
    [
        "आधुनिक प्रशासनिक व्यवस्था में लोक कल्याणकारी योजनाओं का समयबद्ध क्रियान्वयन अत्यंत महत्वपूर्ण माना जाता है। ",
        "संवैधानिक मर्यादाओं का निष्ठापूर्वक पालन करना प्रत्येक सरकारी सेवक और अधिकारी का सर्वोपरि कर्तव्य है। ",
        "केंद्रीय सशस्त्र पुलिस बल देश की आंतरिक सुरक्षा और सीमाओं की अक्षुण्णता बनाए रखने में अग्रणी भूमिका निभाते हैं। ",
        "कार्यालयीन कार्यप्रणाली में गति, स्वच्छता, और अभिलेखों का शुद्ध रखरखाव प्रशासनिक दक्षता को प्रदर्शित करता है। ",
        "कंप्यूटर और डिजिटल प्रारूपों के व्यापक उपयोग ने सरकारी प्रक्रियाओं को अधिक पारदर्शी और परिणामोन्मुखी बना दिया है। ",
        "आधिकारिक पत्राचार और प्रारूपण करते समय व्याकरण संबंधी नियमों और सटीक शब्दावली का विशेष ध्यान रखना आवश्यक है। ",
        "परीक्षा केंद्र पर दिए गए मुद्रित पैराग्राफ को बिना त्रुटि के तीव्र गति से टाइप करना अभ्यास पर निर्भर करता है। ",
        "राष्ट्र निर्माण में समर्पित युवा वर्ग की भूमिका निरंतर सकारात्मक दिशा में अग्रसर हो रही है। ",
        "विभागीय नियमों, सेवा संहिताओं और सुरक्षा मानकों का समुचित ज्ञान कार्यकुशलता को नई दिशा प्रदान करता है। ",
        "नियमित और अनुशासित अभ्यास से ही मंगल इनस्क्रिप्ट कीबोर्ड लेआउट पर वांछित गति और शुद्धता अर्जित की जा सकती है। "
    ],
    [
        "भारतीय गणतंत्र की लोकतांत्रिक परंपराएं नागरिकों के मूलभूत अधिकारों और कर्तव्यों के संतुलन पर आधारित हैं। ",
        "प्रशासनिक सुधार आयोग द्वारा अनुशंसित नीतियां कार्यसंस्कृति में गुणवत्ता और उत्तरदायित्व को सुदृढ़ करती हैं। ",
        "सार्वजनिक व्यय और विकास परियोजनाओं का निष्पादन करते समय वित्तीय नियमों का अक्षरशः अनुपालन अपेक्षित होता है। ",
        "सुरक्षाबलों के जवान कठिन भौगोलिक परिस्थितियों में भी देश की अखंडता की रक्षा के लिए सतत तत्पर रहते हैं। ",
        "सरकारी पत्रावलियों में प्रयुक्त होने वाली राजभाषा का स्वरूप सहज, स्पष्ट, और मानक होना चाहिए। ",
        "टाइपिंग कौशल परीक्षा का मुख्य उद्देश्य अभ्यर्थी की एकाग्रता, शुद्धता, और समय प्रबंधन की परख करना है। ",
        "डिजिटल इंडिया मिशन के माध्यम से सार्वजनिक सेवाओं का लाभ प्रत्येक नागरिक तक सुगमता से पहुंच रहा है। ",
        "अनुशासन और निरंतर अभ्यास ही किसी भी प्रतियोगी परीक्षा में सफलता प्राप्त करने का अचूक मार्ग है। ",
        "आधुनिक संचार तंत्र और सूचना प्रौद्योगिकी के समन्वित उपयोग से कार्यालयीन उत्पादकता में वृद्धि हुई है। ",
        "परीक्षार्थियों को चाहिए कि वे प्रतिदिन निर्धारित शब्द सीमा का मुद्रित अभ्यास पत्र लेकर परीक्षा कक्ष जैसे माहौल में टाइप करें। "
    ]
]

def generate_daily_passage(language: str, set_num: int, target_words: int = 600) -> str:
    """Generates an exam-standard, exactly calibrated ~600-word typing passage."""
    pool = ENGLISH_THEMES if language.lower() == "english" else HINDI_THEMES
    # Seed based on date + set_num to ensure all users receive identical passages on a given day
    today_key = int(datetime.now(IST).strftime("%Y%m%d")) + set_num
    rng = random.Random(today_key)

    chosen_theme = rng.choice(pool)
    assembled_sentences = []
    current_words = 0

    while current_words < target_words:
        sentence = rng.choice(chosen_theme)
        assembled_sentences.append(sentence)
        current_words = len("".join(assembled_sentences).split())

    return "".join(assembled_sentences).strip()

def get_or_create_daily_materials(language: str, set_num: int) -> dict:
    """Retrieves or builds cached PDF and DOCX files for the day."""
    date_str = datetime.now(IST).strftime("%Y-%m-%d")
    lang_slug = language.lower()
    
    base_filename = f"Typing_{lang_slug.capitalize()}_{date_str}_Set_{set_num:02d}"
    pdf_path = os.path.join(VAULT_DIR, f"{base_filename}.pdf")
    docx_path = os.path.join(VAULT_DIR, f"{base_filename}.docx")

    passage = generate_daily_passage(lang_slug, set_num, target_words=600)
    word_count = len(passage.split())

    if not os.path.exists(docx_path):
        build_typing_docx(docx_path, passage, lang_slug, set_num, date_str, word_count)
    if not os.path.exists(pdf_path):
        build_typing_pdf(pdf_path, passage, lang_slug, set_num, date_str, word_count)

    return {
        "text": passage,
        "word_count": word_count,
        "pdf_path": pdf_path,
        "docx_path": docx_path,
        "set_num": set_num,
        "language": lang_slug.capitalize(),
        "date_str": date_str
    }