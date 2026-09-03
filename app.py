import streamlit as st
import re
import json
import os
import subprocess
import time
import urllib.parse
import requests as req
import cv2
import numpy as np
from PIL import Image
import easyocr

ocr_reader = None


def get_ocr_reader():
    global ocr_reader
    if ocr_reader is None:
        ocr_reader = easyocr.Reader(["ko", "en"], gpu=False, verbose=False)
    return ocr_reader


def ensure_chromium():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch(headless=True)
    except Exception:
        subprocess.run(["playwright", "install", "chromium"], capture_output=True, timeout=120)


def extract_plate_number(image_bytes):
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    pil_img = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    reader = get_ocr_reader()
    results = reader.readtext(np.array(pil_img))
    all_digits = ""
    for bbox, text, conf in results:
        cleaned = re.sub(r"[^0-9]", "", text)
        all_digits += cleaned
    if len(all_digits) >= 4:
        return all_digits[-4:]
    return None


def check_with_playwright(note_url, plate_digits, cookies_json):
    from playwright.sync_api import sync_playwright
    cookies = json.loads(cookies_json)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        context.add_cookies(cookies)
        page = context.new_page()
        page.goto(note_url, wait_until="networkidle", timeout=30000)
        time.sleep(3)
        result = page.evaluate("""(plateDigits) => {
            const checkboxes = document.querySelectorAll('[role="checkbox"]');
            for (const cb of checkboxes) {
                const container = cb.closest('[data-list-id]')
                    || cb.closest('[data-note-id]')
                    || cb.parentElement?.parentElement;
                if (!container) continue;
                const text = container.textContent || '';
                if (text.includes(plateDigits)) {
                    const checked = cb.getAttribute('aria-checked');
                    if (checked !== 'true') {
                        cb.click();
                        return { found: true, wasChecked: false };
                    }
                    return { found: true, wasChecked: true };
                }
            }
            return { found: false };
        }""", plate_digits)
        browser.close()
        if result.get("found"):
            if result.get("wasChecked"):
                return True, "이미 체크되어 있었습니다."
            return True, "체크 완료!"
        return False, f"'{plate_digits}' 항목을 찾을 수 없습니다."


CONFIG_FILE = ".keep_config.json"


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {}


def save_config(data):
    existing = load_config()
    existing.update(data)
    with open(CONFIG_FILE, "w") as f:
        json.dump(existing, f)


def clear_config():
    for f in [CONFIG_FILE]:
        if os.path.exists(f):
            os.remove(f)


def init_session_state():
    defaults = {"page": "login", "logged_in": False}
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def login_page():
    st.title("🚗 차량 번호판 체커")
    config = load_config()
    if config.get("cookies") and config.get("email"):
        st.session_state.logged_in = True
        st.session_state.page = "note_url" if not config.get("note_url") else "camera"
        st.rerun()

    st.markdown("### 구글 킵 쿠키 설정")
    st.markdown("""
    **방법:**
    1. PC 브라우저에서 [keep.google.com](https://keep.google.com) 접속 (이미 로그인 상태)
    2. 개발자 도구 열기 (`F12`)
    3. **Application** → **Cookies** → `https://keep.google.com`
    4. 모든 쿠키를 JSON으로 복사하여 아래에 붙여넣기
    """)

    st.markdown("**간편 방법:** 아래 북마클릿을 브라우저 주소창에 붙여넣고 실행하면 쿠키가 복사됩니다:")
    st.code(
        "javascript:void(fetch('https://keep.google.com').then(r=>r.headers).catch(()=>{}));JSON.stringify(document.cookie.split('; ').map(c=>{const[n,...v]=c.split('=');return{name:n,value:v.join('='),domain:'.google.com',path:'/',secure:true}}))",
        language="javascript",
    )

    cookies_input = st.text_area(
        "쿠키 JSON",
        placeholder='[{"name":"SID","value":"...","domain":".google.com",...}]',
        key="cookies_input",
        height=150,
    )

    email = st.text_input("구글 이메일", key="login_email", placeholder="your@gmail.com")

    if st.button("🔑 로그인", type="primary"):
        if not cookies_input.strip():
            st.error("쿠키를 입력해주세요.")
            return
        if not email:
            st.error("이메일을 입력해주세요.")
            return
        try:
            parsed = json.loads(cookies_input)
            if not isinstance(parsed, list):
                st.error("JSON 배열이어야 합니다.")
                return
        except json.JSONDecodeError:
            st.error("쿠키 JSON 형식이 올바르지 않습니다.")
            return
        save_config({"cookies": cookies_input.strip(), "email": email})
        st.session_state.logged_in = True
        st.session_state.page = "note_url"
        st.rerun()


def note_url_page():
    st.title("📌 구글 킵 메모 설정")
    config = load_config()
    st.markdown(f"**로그인:** {config.get('email', '?')}")

    st.markdown("### 메모 URL 입력")
    st.markdown("PC 브라우저에서 Keep 메모를 열고 주소창 URL을 복사해서 붙여넣기 하세요.")

    note_url = st.text_input(
        "구글 킵 메모 URL",
        placeholder="https://keep.google.com/u/0/#LIST/abc123...",
        key="note_url_input",
    )

    if st.button("📌 설정 완료", type="primary"):
        if not note_url or "keep.google.com" not in note_url:
            st.error("구글 킵 URL을 입력해주세요.")
            return
        save_config({"note_url": note_url})
        st.session_state.page = "camera"
        st.rerun()

    if config.get("note_url"):
        st.info(f"**현재 메모:** {config['note_url']}")

    with st.sidebar:
        if st.button("로그아웃"):
            clear_config()
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            init_session_state()
            st.rerun()


def camera_page():
    st.title("📸 차량 번호판 촬영")
    config = load_config()
    st.markdown(f"**대상 메모:** {config.get('note_url', '미설정')}")

    with st.sidebar:
        if st.button("📌 메모 변경"):
            st.session_state.page = "note_url"
            st.rerun()
        if st.button("로그아웃"):
            clear_config()
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            init_session_state()
            st.rerun()

    st.markdown("### 번호판 촬영 또는 사진 업로드")
    col1, col2 = st.columns(2)
    with col1:
        img_data = st.camera_input("📷 카메라로 촬영", key="camera")
    with col2:
        img_upload = st.file_uploader("🖼️ 사진 업로드", type=["jpg", "jpeg", "png", "heic"], key="upload")

    img_bytes = None
    if img_data is not None:
        img_bytes = img_data.getvalue()
    elif img_upload is not None:
        img_bytes = img_upload.getvalue()

    plate_number = None
    if img_bytes is not None:
        with st.spinner("번호판 인식 중..."):
            plate_number = extract_plate_number(img_bytes)
        if plate_number:
            st.success(f"✅ 인식된 뒷 4자리: **{plate_number}**")
        else:
            st.warning("번호판을 인식하지 못했습니다.")

    manual = st.text_input("수동 입력", placeholder="예: 3682", key="manual_plate")
    input_plate = plate_number or (manual.strip() if manual.strip() else None)

    if input_plate:
        st.markdown("---")
        with st.spinner("서버에서 Keep 접속 중... 자동 체크합니다."):
            try:
                success, msg = check_with_playwright(
                    config["note_url"], input_plate, config["cookies"]
                )
                if success:
                    st.success(f"✅ {msg}")
                    st.balloons()
                else:
                    st.warning(f"⚠️ {msg}")
            except Exception as e:
                st.error(f"체크 실패: {e}")
                st.link_button("🔗 구글 킵 메모 열기 (수동 체크)", config["note_url"])


def main():
    st.set_page_config(page_title="차량 번호판 체커", page_icon="🚗")
    init_session_state()
    if st.session_state.page == "login":
        login_page()
    elif st.session_state.page == "note_url":
        note_url_page()
    elif st.session_state.page == "camera":
        camera_page()


if __name__ == "__main__":
    main()
