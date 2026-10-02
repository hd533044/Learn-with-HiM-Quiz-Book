import os
import xml.sax.saxutils as saxutils
import unicodedata
import weasyprint
from app.config import BASE_DIR

def clean_typing_str(text) -> str:
    if not text:
        return ""
    val_str = str(text).strip()
    normalized_str = unicodedata.normalize('NFC', val_str)
    return saxutils.escape(normalized_str)


def build_typing_pdf(filepath: str, text: str, language: str, set_num: int, date_str: str, word_count: int):
    font_family = "'Times New Roman', serif" if language.lower() == "english" else "'Mangal', 'Noto Sans Devanagari', sans-serif"
    
    logo_left_path = os.path.abspath(os.path.join(BASE_DIR, "assets", "logo.png"))
    logo_right_path = os.path.abspath(os.path.join(BASE_DIR, "assets", "logohim.png"))
    target_link = "https://t.me/learnwithhim"

    left_logo_html = f'<a href="{target_link}" target="_blank"><img src="file://{logo_left_path}" style="width: 58px; height: 58px; object-fit: contain; border: none;" /></a>' if os.path.exists(logo_left_path) else f'<a href="{target_link}"><b>Logo</b></a>'
    right_logo_html = f'<a href="{target_link}" target="_blank"><img src="file://{logo_right_path}" style="width: 58px; height: 58px; object-fit: contain; border: none;" /></a>' if os.path.exists(logo_right_path) else f'<a href="{target_link}"><b>@LearnwithHiM</b></a>'

    font_badge = "Times New Roman (12pt)" if language.lower() == "english" else "Mangal Inscript (12pt)"

    # Format real paragraphs with standard exam TAB indent
    raw_paras = text.split("\n\n")
    rendered_paras = []
    for p in raw_paras:
        clean_p = clean_typing_str(p.replace("\t", "").strip())
        if clean_p:
            # &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; simulates a real 0.5 inch / 8-space tab indent
            rendered_paras.append(f"<p class='passage-para'><span class='tab-indent'>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</span>{clean_p}</p>")
            
    body_html = "".join(rendered_paras)

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
    <meta charset="utf-8"/>
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+Devanagari:wght@400;600;700&display=swap');
    @page {{
        size: A4 portrait;
        margin: 18mm 15mm 20mm 15mm;
        @bottom-right {{
            content: "Page " counter(page);
            font-size: 8.5pt;
            font-family: 'Times New Roman', serif;
            color: #64748B;
        }}
    }}
    body {{
        font-family: 'Noto Sans Devanagari', 'Times New Roman', Helvetica, Arial, sans-serif;
        margin: 0;
        padding: 0;
        color: #0F172A;
        font-size: 11pt;
        line-height: 1.45;
        background-color: #ffffff;
    }}
    a {{
        color: inherit;
        text-decoration: none;
    }}
    .watermark-container {{
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        z-index: -1000;
        overflow: hidden;
        pointer-events: none;
    }}
    .wm-text {{
        position: absolute;
        font-family: 'Times New Roman', serif;
        font-weight: bold;
        font-size: 24px;
        color: #94A3B8;
        opacity: 0.13;
        transform: rotate(30deg);
        white-space: nowrap;
    }}
    .header-table {{
        width: 100%;
        border-collapse: collapse;
        margin-bottom: 6px;
    }}
    .header-title {{
        text-align: center;
        color: #1E3A8A;
        font-size: 20px;
        font-weight: bold;
        font-family: 'Times New Roman', serif;
    }}
    .sub-title {{
        color: #16A34A;
        font-size: 11px;
        text-align: center;
        font-weight: bold;
        margin-top: 3px;
        font-family: 'Times New Roman', serif;
    }}
    .exam-bar {{
        background-color: #F1F5F9;
        border: 0.5px solid #CBD5E1;
        padding: 5px 10px;
        margin-top: 4px;
        margin-bottom: 12px;
        font-family: 'Times New Roman', serif;
        font-size: 8.8pt;
        color: #334155;
    }}
    .exam-bar table {{
        width: 100%;
        border-collapse: collapse;
    }}
    .exam-bar td {{
        padding: 1px 4px;
    }}
    .badge-primary {{
        font-weight: bold;
        color: #1E3A8A;
    }}
    .passage-body {{
        margin-top: 10px;
        margin-bottom: 15px;
    }}
    .passage-para {{
        font-family: {font_family};
        font-size: 12pt;
        line-height: 1.5;
        text-align: justify;
        text-justify: inter-word;
        margin: 0 0 12px 0;
        color: #0F172A;
    }}
    .tab-indent {{
        display: inline;
    }}
    .eval-box {{
        border: 0.5px dashed #94A3B8;
        background-color: #F8FAFC;
        padding: 7px 12px;
        margin-top: 12px;
        font-family: 'Times New Roman', serif;
        font-size: 8.8pt;
        font-weight: bold;
        color: #1E293B;
    }}
    .pdf-footer {{
        position: fixed;
        bottom: -13mm;
        left: 0;
        width: 100%;
        text-align: center;
        border-top: 0.5px solid #CBD5E1;
        padding-top: 5px;
        font-size: 8.2pt;
        font-family: 'Times New Roman', serif;
        white-space: nowrap;
        z-index: 1000;
    }}
    .footer-link {{
        color: #0284C7;
        font-weight: bold;
        text-decoration: underline;
        margin: 0 3px;
        display: inline-block;
        vertical-align: middle;
    }}
    .footer-icon {{
        width: 11px;
        height: 11px;
        vertical-align: middle;
        margin-right: 2px;
        display: inline-block;
    }}
    .pipe {{
        color: #94A3B8;
        margin: 0 3px;
    }}
    </style>
    </head>
    <body>
    <div class="watermark-container">
        <div class="wm-text" style="top: 8%; left: 8%;">Learn with HiM</div>
        <div class="wm-text" style="top: 10%; left: 60%;">Typing with HiM</div>
        <div class="wm-text" style="top: 28%; left: 18%;">Typing with HiM</div>
        <div class="wm-text" style="top: 32%; left: 65%;">Learn with HiM</div>
        <div class="wm-text" style="top: 50%; left: 8%;">Learn with HiM</div>
        <div class="wm-text" style="top: 53%; left: 58%;">Typing with HiM</div>
        <div class="wm-text" style="top: 70%; left: 22%;">Typing with HiM</div>
        <div class="wm-text" style="top: 74%; left: 68%;">Learn with HiM</div>
        <div class="wm-text" style="top: 88%; left: 12%;">Learn with HiM</div>
        <div class="wm-text" style="top: 90%; left: 60%;">Typing with HiM</div>
    </div>

    <div class="pdf-footer">
        <a href="https://instagram.com/Learnwithhimm" class="footer-link" target="_blank"><svg class="footer-icon" viewBox="0 0 24 24" fill="none" stroke="#0284C7" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2" width="20" height="20" rx="5" ry="5"></rect><path d="M16 11.37A4 4 0 1 1 12.63 8 4 4 0 0 1 16 11.37z"></path><line x1="17.5" y1="6.5" x2="17.51" y2="6.5"></line></svg>Insta: @Learnwithhimm</a>
        <span class="pipe">|</span>
        <a href="https://youtube.com/@LearnwithHiM" class="footer-link" target="_blank"><svg class="footer-icon" viewBox="0 0 24 24" fill="none" stroke="#0284C7" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M22.54 6.42a2.78 2.78 0 0 0-1.95-1.96C18.88 4 12 4 12 4s-6.88 0-8.59.46a2.78 2.78 0 0 0-1.95 1.96A29 29 0 0 0 1 12a29 29 0 0 0 .46 5.58 2.78 2.78 0 0 0 1.95 1.96C5.12 20 12 20 12 20s6.88 0 8.59-.46a2.78 2.78 0 0 0 1.95-1.96A29 29 0 0 0 23 12a29 29 0 0 0-.46-5.58z"></path><polygon points="9.75 15.02 15.5 12 9.75 8.98 9.75 15.02" fill="#0284C7"></polygon></svg>YT: @LearnwithHiM</a>
        <span class="pipe">|</span>
        <a href="https://t.me/learnwithhim" class="footer-link" target="_blank"><svg class="footer-icon" viewBox="0 0 24 24" fill="none" stroke="#0284C7" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg>TG: @Learnwithhim</a>
        <span class="pipe">|</span>
        <a href="https://t.me/Learnwithhimm" class="footer-link" target="_blank"><svg class="footer-icon" viewBox="0 0 24 24" fill="none" stroke="#0284C7" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg>TG Chat: @Learnwithhimm</a>
        <span class="pipe">|</span>
        <a href="https://t.me/learnwithhim?direct" class="footer-link" target="_blank"><svg class="footer-icon" viewBox="0 0 24 24" fill="none" stroke="#0284C7" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg>Direct DM</a>
    </div>

    <table class="header-table">
        <tr>
            <td style="width: 15%; text-align: left;">{left_logo_html}</td>
            <td class="header-title">
                Learn with HiM Typing Book
                <div class="sub-title">Type Daily! Type Smartly! Daily Free Relevant Typing Material!</div>
            </td>
            <td style="width: 15%; text-align: right;">{right_logo_html}</td>
        </tr>
    </table>

    <div class="exam-bar">
        <table>
            <tr>
                <td><b>Assessment:</b> <span class="badge-primary">CAPF HCM / ASI STENO PAPER-TO-SCREEN TEST</span></td>
                <td style="text-align: right;"><b>Assessment Set:</b> <span class="badge-primary">SET #{set_num:02d}</span></td>
            </tr>
            <tr>
                <td><b>Language & Font:</b> {language.upper()} ({font_badge})</td>
                <td style="text-align: right;"><b>Date:</b> {date_str} &nbsp;|&nbsp; <b>Target Words:</b> ~{word_count} Wds</td>
            </tr>
        </table>
    </div>

    <div class="passage-body">
        {body_html}
    </div>

    <div class="eval-box">
        Candidate Name: ___________________________ &nbsp;&nbsp;&nbsp;&nbsp; Roll Number: _________________ &nbsp;&nbsp;&nbsp;&nbsp; Net WPM: _______ &nbsp;&nbsp;&nbsp;&nbsp; Accuracy: _______%
    </div>
    </body>
    </html>
    """

    weasyprint.HTML(string=html_content).write_pdf(filepath)
    return filepath