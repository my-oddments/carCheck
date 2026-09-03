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
import pytesseract

TOKEN_FILE = ".keep_cookies.json"


def get_redirect_uri():
    """환경에 따라 redirect_uri 자동 결정 (로컬: localhost, 배포: secrets 값)"""
    uri = st.secrets.get("google_oauth", {}).get("redirect_uri", "")
    if not uri or "localhost" in uri:
        return "http://localhost:8501/"
    return uri


def build_auth_url():
    """OAuth 인증 URL 생성"""
    client_id = st.secrets.get("google_oauth", {}).get("client_id", "")
    redirect_uri = get_redirect_uri()
    params = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "prompt": "select_account",
        "access_type": "offline",
        "include_granted_scopes": "true",
    })
    return f"https://accounts.google.com/o/oauth2/auth?{params}"


def exchange_code_for_token(code):
    """Authorization code를 access_token으로 교환"""
    client_id = st.secrets["google_oauth"]["client_id"]
    client_secret = st.secrets["google_oauth"]["client_secret"]
    redirect_uri = get_redirect_uri()
    resp = req.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
    )
    if resp.status_code == 200:
        return resp.json().get("access_token")
    return None


# ── 세션 관리 ──────────────────────────────────────────────
def init_session_state():
    defaults = {
        "logged_in": False,
        "email": None,
        "note_url": None,
        "note_title": None,
        "note_items": [],
        "page": "login",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def load_cached_data():
    if not os.path.exists(TOKEN_FILE):
        return None
    try:
        with open(TOKEN_FILE) as f:
            return json.load(f)
    except Exception:
        return None


def save_cached_data(data):
    with open(TOKEN_FILE, "w") as f:
        json.dump(data, f)


def clear_cached_data():
    if os.path.exists(TOKEN_FILE):
        os.remove(TOKEN_FILE)


def do_logout():
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    clear_cached_data()
    init_session_state()


# ── Keep 메모 읽기 ────────────────────────────────────────
def read_keep_note(note_url):
    """Playwright로 Keep 메모 내용 읽기 (로그인 없이 공개 메모만 가능)"""
    # 공개된 메모가 아니라면 로그인이 필요하므로 URL에서 정보만 추출
    # Keep URL 형식: https://keep.google.com/#LIST/... 또는 #NOTE/...
    note_id = None
    if "#LIST/" in note_url:
        note_id = note_url.split("#LIST/")[-1].split("?")[0]
    elif "#NOTE/" in note_url:
        note_id = note_url.split("#NOTE/")[-1].split("?")[0]

    return note_id


def extract_note_id(url):
    """URL에서 메모 ID 추출"""
    if "#LIST/" in url:
        return url.split("#LIST/")[-1].split("?")[0]
    elif "#NOTE/" in url:
        return url.split("#NOTE/")[-1].split("?")[0]
    return None


# ── 로그인 페이지 ──────────────────────────────────────────
def login_page():
    st.title("🚗 차량 번호판 체커")
    st.markdown("### 구글 킵 로그인")

    # 기존 세션 확인
    cached = load_cached_data()
    if cached and isinstance(cached, dict) and cached.get("email"):
        st.session_state.logged_in = True
        st.session_state.email = cached["email"]
        st.session_state.page = "note_url"
        st.rerun()

    st.markdown("---")
    st.markdown("### 로그인 방법")
    st.markdown(
        "1. 아래 **[1단계] 구글 로그인** 클릭 → 구글 계정으로 로그인\n"
        "2. 로그인 후 자동으로 돌아와서 토큰이 입력됩니다\n"
        "3. 이메일 입력 후 **[2단계] 로그인** 클릭"
    )

    client_id = st.secrets.get("google_oauth", {}).get("client_id", "")
    if client_id:
        auth_url = GOOGLE_AUTH_URL.format(client_id=client_id)
        st.link_button("🔑 [1단계] 구글 로그인", auth_url)
    else:
        st.error("client_id가 설정되지 않았습니다.")

    st.markdown("---")

    # URL에서 authorization code 자동 추출
    qp = st.query_params
    code = qp.get("code", "")
    auto_token = ""
    if code:
        with st.spinner("토큰 교환 중..."):
            auto_token = exchange_code_for_token(code) or ""
        if auto_token:
            st.success("로그인 토큰 자동 추출 완료!")
            st.query_params.clear()
        else:
            st.error("토큰 교환 실패. 다시 시도해주세요.")

    oauth_token = st.text_input(
        "access_token 값",
        value=auto_token,
        placeholder="ya29.a0AfH6SMB...",
        key="oauth_token_input",
    )
    email = st.text_input("구글 이메일", key="login_email", placeholder="your@gmail.com")

    if st.button("🔑 [2단계] 로그인", type="primary"):
        if not email:
            st.error("이메일을 입력해주세요.")
            return
        st.session_state.logged_in = True
        st.session_state.email = email
        save_cached_data({"email": email})
        st.session_state.page = "note_url"
        st.rerun()


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

        note_id = extract_note_id(note_url)
        if not note_id:
            st.error("URL에서 메모 ID를 추출할 수 없습니다.")
            return

        st.session_state.note_url = note_url
        st.session_state.note_id = note_id
        st.session_state.page = "camera"
        st.rerun()

    # 기존 설정이 있으면 보여주기
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
        st.markdown("### 설정")
        if st.button("📌 메모 변경"):
            st.session_state.page = "note_url"
            st.rerun()
        if st.button("로그아웃"):
            do_logout()
            st.rerun()

    img_data = st.camera_input("번호판을 촬영하세요")
    plate_number = None

    if img_data is not None:
        with st.spinner("번호판 인식 중..."):
            plate_number = extract_plate_number(img_data.getvalue())
        if plate_number:
            st.success(f"✅ 인식된 번호: **{plate_number}**")
        else:
            st.warning("번호판을 인식하지 못했습니다. 수동으로 입력해주세요.")

    manual = st.text_input("수동 입력", placeholder="예: 12가3456", key="manual_plate")
    input_plate = plate_number or (manual.strip() if manual.strip() else None)

    if input_plate:
        st.markdown("---")
        st.markdown(f"### 🔍 '{input_plate}' 항목을 찾으세요")
        st.markdown(
            f"아래 버튼을 클릭하면 구글 킵 메모가 열립니다.\n"
            f"메모에서 **'{input_plate}'** 이(가) 포함된 항목을 찾아 체크하세요."
        )

        note_url = st.session_state.get("note_url", "")
        if note_url:
            st.link_button("🔗 구글 킵 메모 열기", note_url)

        st.markdown("---")
        st.markdown("**체크 완료 후:** ✅ 버튼을 클릭하세요.")

        if st.button("✅ 체크 완료", type="primary"):
            st.success(f"'{input_plate}' 체크 완료를 확인했습니다!")
            st.balloons()


# ── OCR ───────────────────────────────────────────────────
def preprocess_image(image_bytes):
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return thresh


def extract_plate_number(image_bytes):
    processed = preprocess_image(image_bytes)
    pil_img = Image.fromarray(processed)
    config = "--psm 7 -l kor+eng"
    text = pytesseract.image_to_string(pil_img, config=config)
    text = text.strip().replace(" ", "")
    pattern = r"\d{2,3}[가-힣]\d{4}"
    match = re.search(pattern, text)
    return match.group() if match else None


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
