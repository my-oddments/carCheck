import streamlit as st
import re
import json
import os
import cv2
import numpy as np
from PIL import Image
import easyocr

ocr_reader = None
CONFIG_FILE = ".keep_config.json"


def get_ocr_reader():
    global ocr_reader
    if ocr_reader is None:
        ocr_reader = easyocr.Reader(["ko", "en"], gpu=False, verbose=False)
    return ocr_reader


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
    if os.path.exists(CONFIG_FILE):
        os.remove(CONFIG_FILE)


def init_session_state():
    defaults = {"page": "setup"}
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def setup_page():
    st.title("🚗 차량 번호판 체커")
    config = load_config()

    if config.get("email") and config.get("note_url"):
        st.session_state.page = "camera"
        st.rerun()

    st.markdown("### 설정 (최초 1회만)")

    email = st.text_input(
        "이메일",
        value=config.get("email", ""),
        placeholder="your@gmail.com",
    )

    st.markdown("---")
    st.markdown("### 구글 킵 메모 URL")
    st.markdown("PC/모바일 브라우저에서 **keep.google.com** 접속 → 메모 열어 → URL 복사")

    note_url = st.text_input(
        "메모 URL",
        value=config.get("note_url", ""),
        placeholder="https://keep.google.com/u/0/#LIST/abc123...",
    )

    if st.button("설정 완료", type="primary"):
        if not email:
            st.error("이메일을 입력해주세요.")
            return
        if not note_url or "keep.google.com" not in note_url:
            st.error("구글 킵 URL을 입력해주세요.")
            return
        save_config({"email": email, "note_url": note_url})
        st.session_state.page = "camera"
        st.rerun()

    if config.get("email"):
        with st.sidebar:
            if st.button("설정 초기화"):
                clear_config()
                st.rerun()


def camera_page():
    st.title("📸 차량 번호판 촬영")
    config = load_config()
    st.markdown(f"**이메일:** {config.get('email', '?')}")
    st.markdown(f"**메모:** {config.get('note_url', '미설정')}")

    with st.sidebar:
        if st.button("설정 변경"):
            st.session_state.page = "setup"
            st.rerun()

    st.markdown("### 번호판 촬영 또는 사진 업로드")
    col1, col2 = st.columns(2)
    with col1:
        img_data = st.camera_input("📷 카메라", key="camera")
    with col2:
        img_upload = st.file_uploader("🖼️ 사진 업로드", type=["jpg", "jpeg", "png"], key="upload")

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
        st.markdown(f"### 🔍 '{input_plate}' 항목을 체크하세요")
        st.markdown("아래 버튼을 누르면 구글 킵 메모가 열립니다.")
        st.link_button("🔗 구글 킵 메모 열기", config.get("note_url", ""))


def main():
    st.set_page_config(page_title="차량 번호판 체커", page_icon="🚗")
    init_session_state()
    if st.session_state.page == "setup":
        setup_page()
    elif st.session_state.page == "camera":
        camera_page()


if __name__ == "__main__":
    main()
