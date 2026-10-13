"""Login-free classroom dashboard for Streamlit deployments."""
from __future__ import annotations

from datetime import datetime
from io import BytesIO
import html
from urllib.parse import urlencode, urlsplit, urlunsplit

import qrcode
import streamlit as st

from think_app.config import load_settings
from think_app.courses import CLASSES, COLORS, CourseManager
from think_app.storage import AppError, GitHubStore, LocalStore, MemoryStore


st.set_page_config(page_title="수업", page_icon="📚", layout="wide")


@st.cache_resource(show_spinner=False)
def persistent_store(mode: str, data_dir: str, repo: str, branch: str, token: str, key: str):
    if mode == "github":
        return GitHubStore(repo, branch, token, key)
    if mode == "local":
        return LocalStore(data_dir, key)
    raise AppError("THINK_MODE는 demo, local, github 중 하나로 설정하세요.")


@st.cache_resource(show_spinner=False)
def demo_store():
    return MemoryStore()


def get_store(settings):
    if settings.mode == "demo":
        return demo_store()
    return persistent_store(
        settings.mode,
        settings.data_dir,
        settings.repo,
        settings.branch,
        settings.github_token,
        settings.encryption_key,
    )


def style_page():
    st.markdown(
        """
        <style>
        .stApp { background: #f5f4f7; color: #171923; }
        .block-container { max-width: 1440px; padding-top: 2rem; }
        h1, h2, h3 { letter-spacing: -.035em; }
        div.stButton > button { border-radius: 9px; min-height: 42px; }
        div.stButton > button[kind="primary"] {
            background: #6948f5; border-color: #6948f5; color: white;
        }
        .course-row {
            border-top: 1px solid #e9e7ec; padding: 16px 0;
        }
        .course-title { font-size: 1.05rem; font-weight: 700; }
        .course-meta { color: #777783; font-size: .9rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def app_url(course_id: str) -> str:
    context_url = st.context.url
    parts = urlsplit(context_url)
    if not parts.scheme or not parts.netloc:
        raise AppError("현재 페이지 주소를 확인할 수 없습니다. 배포 주소에서 다시 시도하세요.")
    base_url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    return base_url + "?" + urlencode({"course_id": course_id})


def formatted_date(value: str) -> str:
    try:
        date = datetime.fromisoformat(value)
        return f"{date.year}년 {date.month}월 {date.day}일"
    except (TypeError, ValueError):
        return value


def share_view(manager: CourseManager, course_id: str):
    course = manager.get(course_id)
    if not course:
        st.error("공유한 수업을 찾을 수 없습니다. 삭제되었거나 보관된 수업일 수 있습니다.")
        if st.button("수업 목록으로"):
            st.query_params.clear()
            st.rerun()
        return
    st.title(course["title"])
    st.caption("수업 공유")
    st.write("참여할 수 있는 대상: " + ", ".join(course["classes"]))
    st.info("선생님이 공유한 수업 페이지입니다.")
    if st.button("수업 목록으로"):
        st.query_params.clear()
        st.rerun()


def render_share(course: dict):
    st.subheader("수업 공유")
    try:
        url = app_url(course["id"])
        st.caption("이 주소를 공유하면 수업 정보를 볼 수 있습니다.")
        st.code(url, language=None)
        qr = qrcode.make(url)
        image = BytesIO()
        qr.save(image, format="PNG")
        st.image(image.getvalue(), caption="QR 코드", width=220)
    except AppError as error:
        st.error(str(error))


def create_form(manager: CourseManager):
    with st.form("create_course"):
        title = st.text_input("수업 이름", max_chars=120)
        color = st.color_picker("수업 이름 앞에 표시할 색상", value=COLORS[0])
        selected_classes = st.multiselect("참여할 수 있는 대상", CLASSES, default=[CLASSES[0]])
        submitted = st.form_submit_button("수업 만들기", type="primary")
    if submitted:
        try:
            manager.create(title, color, selected_classes)
            st.session_state.pop("show_create_form", None)
            st.success("수업을 만들었습니다.")
            st.rerun()
        except AppError as error:
            st.error(str(error))


def edit_form(manager: CourseManager, course: dict):
    with st.form("edit_course_" + course["id"]):
        title = st.text_input("수업 이름", value=course["title"], max_chars=120)
        color = st.color_picker(
            "수업 이름 앞에 표시할 색상",
            value=course["color"],
            key="course_color_" + course["id"],
        )
        selected_classes = st.multiselect(
            "참여할 수 있는 대상",
            CLASSES,
            default=course["classes"],
            key="course_classes_" + course["id"],
        )
        left, right = st.columns(2)
        save = left.form_submit_button("수정 저장", type="primary")
        cancel = right.form_submit_button("취소")
    if save:
        try:
            manager.update(course["id"], title, color, selected_classes)
            st.session_state.pop("editing_course", None)
            st.success("수업 정보를 수정했습니다.")
            st.rerun()
        except AppError as error:
            st.error(str(error))
    if cancel:
        st.session_state.pop("editing_course", None)
        st.rerun()


def delete_confirmation(manager: CourseManager):
    pending = st.session_state.get("delete_course")
    course = manager.get(pending) if pending else None
    if not course:
        return
    st.warning(f"‘{course['title']}’ 수업을 삭제할까요? 삭제한 수업은 복구할 수 없습니다.")
    confirm, cancel = st.columns(2)
    if confirm.button("수업 삭제", type="primary", key="confirm_delete_course"):
        try:
            manager.delete(course["id"])
            st.session_state.pop("delete_course", None)
            st.success("수업을 삭제했습니다.")
            st.rerun()
        except AppError as error:
            st.error(str(error))
    if cancel.button("취소", key="cancel_delete_course"):
        st.session_state.pop("delete_course", None)
        st.rerun()


def archive_confirmation(manager: CourseManager):
    pending = st.session_state.get("archive_course")
    course = manager.get(pending) if pending else None
    if not course:
        return
    st.info(f"‘{course['title']}’ 수업을 GitHub 보관 공간에 저장합니다.")
    confirm, cancel = st.columns(2)
    if confirm.button("GitHub에 보관", type="primary", key="confirm_archive_course"):
        try:
            manager.archive(course["id"])
            st.session_state.pop("archive_course", None)
            st.success("수업 파일을 GitHub에 보관했습니다.")
            st.rerun()
        except AppError as error:
            st.error(str(error))
    if cancel.button("취소", key="cancel_archive_course"):
        st.session_state.pop("archive_course", None)
        st.rerun()


def active_courses(manager: CourseManager):
    courses = manager.list_courses()
    title, create = st.columns([5, 1])
    title.subheader("내 수업")
    if create.button("＋ 수업 만들기", type="primary", use_container_width=True):
        st.session_state.show_create_form = not st.session_state.get("show_create_form", False)
    if st.session_state.get("show_create_form"):
        create_form(manager)

    st.markdown("**이름**　　　　　　　　　　　　　　　　　　　　　　**마지막 업데이트**")
    for course in courses:
        name, updated, share, edit, menu = st.columns([4.6, 2.1, 0.7, 0.7, 0.7])
        name.markdown(
            f'<div class="course-title"><span style="color:{course["color"]}">●</span> '
            f'{html.escape(course["title"])}</div><div class="course-meta">'
            f'{html.escape(", ".join(course["classes"]))}</div>',
            unsafe_allow_html=True,
        )
        updated.markdown(formatted_date(course["updated"]))
        if share.button("공유", key="share_" + course["id"], help="수업 링크와 QR 코드"):
            st.session_state.sharing_course = course["id"]
        if edit.button("수정", key="edit_" + course["id"]):
            st.session_state.editing_course = course["id"]
        with menu.popover("⋯", help="수업 메뉴"):
            if st.button("수업 보관", key="archive_" + course["id"], use_container_width=True):
                st.session_state.archive_course = course["id"]
            if st.button("삭제", key="delete_" + course["id"], use_container_width=True):
                st.session_state.delete_course = course["id"]
        if st.session_state.get("sharing_course") == course["id"]:
            with st.expander("공유 주소 및 QR 코드", expanded=True):
                render_share(course)
        if st.session_state.get("editing_course") == course["id"]:
            edit_form(manager, course)
    delete_confirmation(manager)
    archive_confirmation(manager)


def archived_courses(manager: CourseManager):
    try:
        archives = manager.list_archives()
    except AppError as error:
        st.error(str(error))
        return
    if not archives:
        st.info("보관된 수업이 없습니다.")
        return
    for archive in archives:
        col, restore = st.columns([5, 1])
        course = archive["course"]
        col.markdown(
            f'<div class="course-title"><span style="color:{course["color"]}">●</span> '
            f'{html.escape(course["title"])}</div><div class="course-meta">'
            f'보관일 {formatted_date(archive["archived_at"])} · '
            f'{html.escape(", ".join(course["classes"]))}</div>',
            unsafe_allow_html=True,
        )
        if restore.button("불러오기", key="restore_" + archive["id"]):
            try:
                manager.restore(archive["id"])
                st.success("보관된 수업을 불러왔습니다.")
                st.rerun()
            except AppError as error:
                st.error(str(error))


def main():
    style_page()
    settings = load_settings()
    try:
        store = get_store(settings)
        manager = CourseManager(store)
        course_id = st.query_params.get("course_id")
        if course_id:
            share_view(manager, course_id)
            return
        st.title("수업")
        if settings.mode == "demo":
            st.caption("체험 모드 · 수업은 앱 프로세스가 유지되는 동안 임시 저장됩니다.")
        st.warning("로그인 없이 운영됩니다. 배포된 앱에 접근할 수 있는 사용자는 수업을 수정하거나 삭제할 수 있습니다.")
        current_tab, archive_tab = st.tabs(["현재 진행 중", "보관됨"])
        with current_tab:
            active_courses(manager)
        with archive_tab:
            archived_courses(manager)
    except AppError as error:
        st.error(str(error))


if __name__ == "__main__":
    main()
