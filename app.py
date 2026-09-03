import streamlit as st
import re
import json
import os
import time
import urllib.parse
import requests as req
import cv2
import numpy as np
from PIL import Image
import easyocr
from playwright.sync_api import sync_playwright

SESSION_FILE = ".keep_session.json"
_ocr_reader = None


def get_ocr_reader():
    global _ocr_reader
    if _ocr_reader is None:
        _ocr_reader = easyocr.Reader(["ko", "en"], gpu=False, verbose=False)
    return _ocr_reader


# ── Playwright 구글 로그인 ─────────────────────────────────
def login_with_playwright():
    """브라우저 창을 열어서 사용자가 직접 구글 로그인"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://accounts.google.com/o/oauth2/auth?"
                   + urllib.parse.urlencode({
                       "client_id": st.secrets["google_oauth"]["client_id"],
                       "redirect_uri": "https://keep.google.com/",
                       "response_type": "token",
                       "scope": "openid email profile",
                       "prompt": "select_account",
                   }))

        try:
            page.wait_for_url("https://keep.google.com/**", timeout=120000)
            cookies = context.cookies()
            with open(SESSION_FILE, "w") as f:
                json.dump(cookies, f)
            browser.close()
            return True
        except Exception:
            browser.close()
            return False


def is_logged_in():
    return os.path.exists(SESSION_FILE)


def clear_session():
    if os.path.exists(SESSION_FILE):
        os.remove(SESSION_FILE)


# ── Playwright Keep 자동 체크 ──────────────────────────────
def check_item_in_keep(note_url, plate_digits):
    """Playwright로 Keep에 접속하여 항목 자동 체크"""
    if not os.path.exists(SESSION_FILE):
        return False, "로그인이 필요합니다."

    with open(SESSION_FILE) as f:
        cookies = json.load(f)

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
                        return { found: true, wasChecked: false, text: text.trim().substring(0, 50) };
                    }
                    return { found: true, wasChecked: true, text: text.trim().substring(0, 50) };
                }
            }
            return { found: false };
        }""", plate_digits)

        browser.close()

        if result.get("found"):
            if result.get("wasChecked"):
                return True, f"이미 체크되어 있었습니다."
            return True, f"체크 완료!"
        return False, f"'{plate_digits}' 항목을 찾을 수 없습니다."


# ── 세션 관리 ──────────────────────────────────────────────
def init_session_state():
    defaults = {
        "logged_in": False,
        "email": None,
        "note_url": None,
        "page": "login",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def load_cached_data():
    if not os.path.exists(".keep_cookies.json"):
        return None
    try:
        with open(".keep_cookies.json") as f:
            return json.load(f)
    except Exception:
        return None


def save_cached_data(data):
    existing = load_cached_data()
    if existing and isinstance(existing, dict):
        existing.update(data)
        data = existing
    with open(".keep_cookies.json", "w") as f:
        json.dump(data, f)


def clear_cached_data():
    for f in [".keep_cookies.json", SESSION_FILE]:
        if os.path.exists(f):
            os.remove(f)


def do_logout():
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    clear_cached_data()
    init_session_state()


# ── 로그인 페이지 ──────────────────────────────────────────
def login_page():
    st.title("🚗 차량 번호판 체커")

    if is_logged_in():
        cached = load_cached_data()
        if cached and isinstance(cached, dict) and cached.get("email"):
            st.session_state.logged_in = True
            st.session_state.email = cached["email"]
            if cached.get("note_url"):
                st.session_state.note_url = cached["note_url"]
                st.session_state.page = "camera"
            else:
                st.session_state.page = "note_url"
            st.rerun()

    st.markdown("### 구글 로그인")
    st.markdown("아래 버튼을 클릭하면 브라우저 창이 열립니다. 구글 계정으로 로그인하세요.")

    if st.button("🔑 구글 로그인 (브라우저 창 열기)", type="primary"):
        with st.spinner("브라우저에서 로그인 중..."):
            success = login_with_playwright()
        if success:
            st.success("로그인 완료!")
            email = st.text_input("이메일 입력", key="login_email", placeholder="your@gmail.com")
            if st.button("다음"):
                if email:
                    save_cached_data({"email": email})
                    st.session_state.logged_in = True
                    st.session_state.email = email
                    st.session_state.page = "note_url"
                    st.rerun()
        else:
            st.error("로그인 실패. 다시 시도해주세요.")


# ── 메모 URL 입력 페이지 ────────────────────────────────────
def note_url_page():
    st.title("📌 구글 킵 메모 설정")
    st.markdown(f"**로그인:** {st.session_state.email}")

    st.markdown("---")
    st.markdown("### 메모 URL 입력 방법")
    st.markdown(
        "1. 브라우저에서 **keep.google.com** 접속\n"
        "2. 체크리스트 메모를 엽니다\n"
        "3. 주소창의 URL을 복사합니다\n"
        "4. 아래에 붙여넣기 합니다"
    )

    note_url = st.text_input(
        "구글 킵 메모 URL",
        placeholder="https://keep.google.com/#LIST/abc123...",
        key="note_url_input",
    )

    if st.button("📌 메모 설정 완료", type="primary"):
        if not note_url:
            st.error("메모 URL을 입력해주세요.")
            return
        if "keep.google.com" not in note_url:
            st.error("구글 킵 URL이 아닙니다.")
            return
        st.session_state.note_url = note_url
        save_cached_data({"note_url": note_url})
        st.session_state.page = "camera"
        st.rerun()

    if st.session_state.get("note_url"):
        st.markdown("---")
        st.info(f"**현재 설정된 메모:** {st.session_state.note_url}")

    with st.sidebar:
        if st.button("로그아웃"):
            do_logout()
            st.rerun()


# ── 카메라 페이지 ──────────────────────────────────────────
def camera_page():
    st.title("📸 차량 번호판 촬영")
    st.markdown(f"**대상 메모:** {st.session_state.get('note_url', '미설정')}")

    with st.sidebar:
        if st.button("📌 메모 변경"):
            st.session_state.page = "note_url"
            st.rerun()
        if st.button("로그아웃"):
            do_logout()
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
            st.warning("번호판을 인식하지 못했습니다. 수동으로 입력해주세요.")

    manual = st.text_input("수동 입력", placeholder="예: 3682", key="manual_plate")
    input_plate = plate_number or (manual.strip() if manual.strip() else None)

    if input_plate:
        st.markdown("---")
        with st.spinner("Keep에서 자동 체크 중..."):
            success, msg = check_item_in_keep(st.session_state.note_url, input_plate)
        if success:
            st.success(f"✅ {msg}")
            st.balloons()
        else:
            st.warning(f"⚠️ {msg}")
            st.link_button("🔗 구글 킵 메모 열기 (수동 체크)", st.session_state.note_url)


# ── OCR ───────────────────────────────────────────────────
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


# ── 메인 ──────────────────────────────────────────────────
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
