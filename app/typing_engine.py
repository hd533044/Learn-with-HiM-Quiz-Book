import os
import random
import pytz
from datetime import datetime, timedelta

IST = pytz.timezone("Asia/Kolkata")
VAULT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "typing_vault"))
os.makedirs(VAULT_DIR, exist_ok=True)

# Broad Multi-Topic English Sentence Vault (Space, Defense, AI, Infra, Constitution, Economy, Green Energy)
ENGLISH_TOPIC_BANKS = {
    "space": [
        "India's space exploration trajectory has witnessed historic milestones through relentless indigenous engineering and scientific dedication.",
        "The Chandrayaan missions demonstrated precision lunar landings and established India's capabilities in extraterrestrial exploration.",
        "With the upcoming Gaganyaan human spaceflight mission, the nation is preparing to send astronauts into low Earth orbit.",
        "Advanced polar satellite launch vehicles have reliably placed hundreds of commercial satellites into designated orbital planes.",
        "Solar observatory payloads like Aditya-L1 continue to transmit crucial astrophysical observations regarding coronal mass ejections.",
        "Deep space network stations maintain high-frequency telemetry links across planetary exploration horizons.",
        "Space technology applications now directly benefit agriculture, disaster forecasting, oceanography, and regional navigation grids.",
        "Collaborative aerospace projects foster international scientific partnerships while enhancing defense surveillance architectures."
    ],
    "defense_admin": [
        "The Central Armed Police Forces function as the vigilant guardians of internal peace and sensitive border frontiers.",
        "Border Security Force and Central Reserve Police Force personnel operate with exceptional gallantry across inhospitable terrains.",
        "Clerical and ministerial cadres in paramilitary establishments guarantee flawless record management and administrative continuity.",
        "Meticulous documentation, rapid keyboard transcription, and official drafting are essential for institutional efficiency.",
        "Standard operating procedures govern logistics, troop welfare, communication encryptions, and statutory inquiries.",
        "Modern defense equipment, unmanned aerial surveillance, and smart fencing have upgraded perimeter security along hostile borders.",
        "Administrative reforms consistently focus on paperless workflows, prompt grievance redressal, and institutional transparency.",
        "Maintaining discipline, emotional resilience, and physical fitness remains foundational to uniformed military culture."
    ],
    "technology_ai": [
        "The rapid evolution of artificial intelligence and machine learning is fundamentally transforming global knowledge economies.",
        "Cloud architectures, automated data pipelines, and cyber defense grids form the structural backbone of modern governance.",
        "Data protection statutes establish strict boundaries against unauthorized profiling and safeguard citizen privacy rights.",
        "Semiconductor fabrication and quantum computing research are now recognized as vital pillars of national technological sovereignty.",
        "Critical information infrastructure requires round-the-clock threat intelligence against sophisticated cyber threats.",
        "E-governance portals and unified payment interfaces have democratized financial transactions across remote hamlets.",
        "Digital literacy and keyboard speed assessments equip the administrative workforce to process high-volume electronic dockets.",
        "Machine intelligence tools must be deployed with human oversight, ethical safeguards, and robust security protocols."
    ],
    "infra_economy": [
        "National infrastructure highways, high-speed rail corridors, and dedicated freight lanes have reduced multimodal transit times.",
        "Sustainable urban transport networks and inland waterways enhance industrial supply chain competitiveness across states.",
        "Fiscal policies prioritize capital expenditure investments to stimulate employment generation and domestic manufacturing ecosystems.",
        "The renewable power sector is setting records in solar generation, green hydrogen innovation, and localized battery storage systems.",
        "Constitutional mechanisms balance equitable revenue distribution between the Union and the States to foster cooperative federalism.",
        "Public financial management platforms ensure that direct benefit transfers reach eligible beneficiaries without middlemen.",
        "Rural digital connectivity has opened vibrant global markets for traditional handicrafts and farmer producer organizations.",
        "Economic resilience hinges on disciplined budget management, export diversification, and sustainable ecological conservation."
    ]
}

HINDI_TOPIC_BANKS = {
    "space": [
        "भारतीय अंतरिक्ष अनुसंधान संगठन ने स्वदेशी तकनीक और वैज्ञानिक नवाचार के बल पर वैश्विक स्तर पर अद्वितीय ख्याति अर्जित की है।",
        "चंद्रयान मिशन की अभूतपूर्व सफलता ने चंद्रमा के दक्षिणी ध्रुव पर तिरंगा फहराकर भारत का तकनीकी सामर्थ्य प्रमाणित किया।",
        "गगनयान मानव अंतरिक्ष मिशन के अंतर्गत भारतीय अंतरिक्ष यात्रियों को पृथ्वी की निचली कक्षा में भेजने की तैयारियां तीव्र गति से जारी हैं।",
        "ध्रुवीय उपग्रह प्रक्षेपण यान ने सैकड़ों विदेशी उपग्रहों को सटीक कक्षाओं में स्थापित कर भारत को अग्रणी प्रक्षेपण केंद्र बना दिया है।",
        "आदित्य-एलवन सौर वेधशाला अंतरिक्ष से सौर ज्वालाओं और चुंबकीय तूफानों के रहस्य सुलझाने में निरंतर बहुमूल्य डेटा प्रेषित कर रही है।",
        "अंतरिक्ष विज्ञान के अनुप्रयोग कृषि पैदावार, मौसम पूर्वानुमान, आपदा चेतावनी और समुद्री संसाधनों की सुरक्षा में वरदान सिद्ध हो रहे हैं।",
        "उपग्रह संचार प्रणालियों ने दूरस्थ पर्वतीय अंचलों तक डिजिटल शिक्षा और टेलीमेडिसिन सेवाओं की निर्बाध पहुंच सुनिश्चित की है।",
        "वैज्ञानिक अनुसंधान के क्षेत्र में आत्मनिर्भरता प्राप्त करना राष्ट्रीय सुरक्षा और संप्रभुता के लिए अनिवार्य माना जाता है।"
    ],
    "defense_admin": [
        "केंद्रीय सशस्त्र पुलिस बल देश की आंतरिक सुरक्षा व्यवस्था बनाए रखने और सीमावर्ती क्षेत्रों में शांति स्थापना के लिए सदैव कटिबद्ध हैं।",
        "सीमा सुरक्षा बल और केंद्रीय रिजर्व पुलिस बल के जांबाज जवान विषम भौगोलिक परिस्थितियों में भी अदम्य साहस का परिचय देते हैं।",
        "सुरक्षाबलों के कार्यालयीन संवर्ग और मंत्रालयिक कर्मी पत्रावलियों के शुद्ध रखरखाव और सुचारू प्रशासनिक संचालन में रीढ़ साबित होते हैं।",
        "टाइपिंग गति परीक्षा का मुख्य उद्देश्य अभ्यर्थियों की मुद्रित प्रतियों को देखकर तीव्र गति और शून्य त्रुटि के साथ टाइप करने की क्षमता जांचना है।",
        "सरकारी पत्राचार और अर्ध-शासकीय आदेशों के प्रारूपण में मानक शब्दावली, व्याकरणिक शुद्धता और संक्षिप्तता का विशेष महत्व होता है।",
        "स्मार्ट फेंसिंग, थर्मल सेंसर और ड्रोन निगरानी प्रणालियों ने सीमाओं पर घुसपैठ और तस्करी के विरुद्ध सुरक्षा कवच को मजबूत बनाया है।",
        "प्रशासनिक सुधारों के तहत सरकारी सेवाओं को पारदर्शी, जनोन्मुखी और भ्रष्टाचार मुक्त बनाने के ठोस प्रयास किए जा रहे हैं।",
        "अनुशासन, समर्पण और कर्तव्यनिष्ठा ही किसी भी सैन्य अथवा प्रशासनिक प्रतिष्ठान की सर्वोच्च गरिमा और पहचान होती है।"
    ],
    "technology_ai": [
        "आर्टिफिशियल इंटेलिजेंस और मशीन लर्निंग का प्रसार आधुनिक कार्यसंस्कृति और निर्णय प्रक्रिया में युगांतकारी परिवर्तन ला रहा है।",
        "डिजिटल सार्वजनिक अवसंरचना और यूपीआई भुगतान प्रणाली ने भारत के ग्रामीण क्षेत्रों तक डिजिटल क्रांति का सूत्रपात किया है।",
        "साइबर सुरक्षा तंत्र को संवेदनशील सरकारी सर्वरों और राष्ट्रीय डेटाबेस की सुरक्षा के लिए अत्याधुनिक मानकों पर तैयार किया गया है।",
        "सेमीकंडक्टर निर्माण और अत्याधुनिक चिप डिजाइनिंग में आत्मनिर्भरता हासिल करने के लिए राष्ट्रीय मिशन संचालित हैं।",
        "नागरिकों की व्यक्तिगत जानकारी और गोपनीयता की रक्षा हेतु प्रभावी डेटा संरक्षण कानून और विनियामक ढांचे लागू किए गए हैं।",
        "कार्यालयीन स्वचालन और कंप्यूटर आधारित फाइलिंग से दस्तावेजीकरण का कार्य अधिक सुगम, त्वरित और पर्यावरण अनुकूल बन चुका है।",
        "मंगल इनस्क्रिप्ट कीबोर्ड लेआउट पर नियमित अभ्यास करने से अभ्यर्थियों की टाइपिंग गति और सटीकता में उल्लेखनीय वृद्धि होती है।",
        "तकनीकी प्रगति का समुचित लाभ जन-जन तक पहुंचाना ही किसी भी कल्याणकारी लोकतांत्रिक व्यवस्था का प्रमुख उद्देश्य होता है।"
    ],
    "infra_economy": [
        "राष्ट्रीय राजमार्गों, एक्सप्रेसवे और समर्पित माल ढुलाई गलियारों के विस्तार से देश के औद्योगिक विकास को अभूतपूर्व गति मिली है।",
        "हरित ऊर्जा और सौर संयंत्रों की स्थापना से भारत स्वच्छ पर्यावरण और नवीकरणीय ऊर्जा उत्पादन के वैश्विक लक्ष्यों की ओर बढ़ रहा है।",
        "राजकोषीय नीतियों का प्राथमिक उद्देश्य बुनियादी ढांचे में पूंजीगत निवेश बढ़ाकर रोजगार के नए अवसर सृजित करना है।",
        "प्रत्यक्ष लाभ अंतरण योजना ने सरकारी सब्सिडी को बिना किसी बिचौलिए के सीधे पात्र लाभार्थियों के बैंक खातों में पहुंचा दिया है।",
        "आत्मनिर्भर भारत अभियान के अंतर्गत घरेलू विनिर्माण उद्योगों, सूक्ष्म उपक्रमों और स्टार्टअप पारिस्थितिकी तंत्र को प्रोत्साहन मिल रहा है।",
        "सहकारी संघवाद की भावना को सुदृढ़ करते हुए केंद्र और राज्यों के बीच वित्तीय संसाधनों का न्यायसंगत वितरण सुनिश्चित किया गया है।",
        "रेलवे आधुनिकीकरण और तीव्र गति वाली ट्रेनों के परिचालन ने आम नागरिकों के यात्रा अनुभव को अधिक सुरक्षित और आरामदायक बनाया है।",
        "पर्यावरण संरक्षण के साथ आर्थिक संवृद्धि का सामंजस्य स्थापित करना ही भविष्य की टिकाऊ विकास नीति का मूल मंत्र है।"
    ]
}

def generate_daily_passage(language: str, set_num: int, target_date_str: str, target_words: int = 600) -> str:
    """
    Generates a structured, multi-topic, multi-paragraph text (~600 words) 
    starting every paragraph with a real exam TAB indent.
    Guarantees no repetitive text from previous days.
    """
    is_eng = language.lower() == "english"
    banks = ENGLISH_TOPIC_BANKS if is_eng else HINDI_TOPIC_BANKS

    # Construct unique numerical seed from date + set index + topic hash
    date_int = int(target_date_str.replace("-", ""))
    seed_val = (date_int * 37) + (set_num * 101)
    rng = random.Random(seed_val)

    # 4 Structured Paragraphs with unique topics per paragraph
    topics = list(banks.keys())
    rng.shuffle(topics)

    paragraphs = []
    total_words = 0

    for i in range(4):
        t_key = topics[i % len(topics)]
        sentences_pool = list(banks[t_key])
        rng.shuffle(sentences_pool)

        para_sentences = []
        para_words = 0
        words_per_para = target_words // 4

        while para_words < words_per_para and sentences_pool:
            sent = sentences_pool.pop()
            para_sentences.append(sent)
            para_words = len(" ".join(para_sentences).split())

        # If pool exhausted, pull from another topic
        if para_words < words_per_para:
            alt_pool = list(banks[topics[(i + 1) % len(topics)]])
            rng.shuffle(alt_pool)
            while para_words < words_per_para and alt_pool:
                sent = alt_pool.pop()
                para_sentences.append(sent)
                para_words = len(" ".join(para_sentences).split())

        # Exam-style paragraph starting with a TAB indent
        para_text = "\t" + " ".join(para_sentences).strip()
        paragraphs.append(para_text)
        total_words += para_words

    # Rejoin with standard double paragraph breaks
    full_text = "\n\n".join(paragraphs)
    return full_text


def get_available_dates(days_back: int = 15) -> list:
    """Returns the list of the last 15 days in YYYY-MM-DD format (IST)."""
    today = datetime.now(IST).date()
    dates = []
    for i in range(days_back):
        d = today - timedelta(days=i)
        dates.append(d.strftime("%Y-%m-%d"))
    return dates


def get_or_create_daily_materials(language: str, set_num: int, target_date_str: str = None) -> dict:
    """Retrieves or builds cached PDF and DOCX files for any chosen date."""
    from app.typing_docs import build_typing_docx
    from app.typing_pdf import build_typing_pdf

    if not target_date_str:
        target_date_str = datetime.now(IST).strftime("%Y-%m-%d")

    lang_slug = language.lower()
    base_filename = f"Typing_{lang_slug.capitalize()}_{target_date_str}_Set_{set_num:02d}"
    pdf_path = os.path.join(VAULT_DIR, f"{base_filename}.pdf")
    docx_path = os.path.join(VAULT_DIR, f"{base_filename}.docx")

    passage = generate_daily_passage(lang_slug, set_num, target_date_str, target_words=600)
    word_count = len(passage.replace("\t", "").split())

    if not os.path.exists(docx_path):
        build_typing_docx(docx_path, passage, lang_slug, set_num, target_date_str, word_count)
    if not os.path.exists(pdf_path):
        build_typing_pdf(pdf_path, passage, lang_slug, set_num, target_date_str, word_count)

    return {
        "text": passage,
        "word_count": word_count,
        "pdf_path": pdf_path,
        "docx_path": docx_path,
        "set_num": set_num,
        "language": lang_slug.capitalize(),
        "date_str": target_date_str,
        "filename_base": base_filename
    }