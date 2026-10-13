import base64
import hashlib
import io
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from PIL import Image, ImageDraw

from think_app.classroom import Classroom
from think_app.courses import CourseManager, now
from think_app.storage import AppError, GitHubStore, LocalStore, MemoryStore, uploaded_file_path
from think_app.uploads import UploadedFiles, owned_uploads


def setup(store=None):
    store = store or MemoryStore()
    course_id = CourseManager(store).create("데스모스", "#6948f5", ["1반", "2반"])
    room = Classroom(store)
    activity_id = room.save_activity(course_id, "데스모스 그래프", "직선을 나타내세요", "y=2x+1",
                                     ["기울기 설명"], 5, now()[:10], None, mode="graph")
    return room, activity_id, UploadedFiles(store)


def capture(format="PNG"):
    image = Image.new("RGB", (600, 400), "white")
    draw = ImageDraw.Draw(image)
    draw.line([(0, 320), (600, 0)], fill="red", width=5)
    raw = io.BytesIO()
    image.save(raw, format=format)
    return raw.getvalue()


def save(service, aid, raw=None, **kwargs):
    values = dict(student_id="10101", name="김학생", class_id="1반", filename="그래프.png")
    values.update(kwargs)
    return service.save(aid, raw=capture() if raw is None else raw, **values)


@pytest.mark.parametrize("format,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg")])
def test_upload_is_durable_before_submission_preserves_original_and_normalizes_answer(tmp_path, format, mime):
    key = Fernet.generate_key().decode()
    room, aid, service = setup(LocalStore(tmp_path, key))
    original = capture(format)
    upload = save(service, aid, original)
    assert upload["saved"] is True
    assert not room.attempts(aid)
    assert upload["original_mime"] == mime and upload["mime"] == "image/jpeg"
    assert upload["original_sha256"] == hashlib.sha256(original).hexdigest()
    restarted = LocalStore(tmp_path, key)
    assert UploadedFiles(restarted).get(upload["id"], aid, "10101", "1반") == upload
    assert restarted.get_uploaded_file(upload) == upload
    assert restarted.get_blob(upload["original_ref"]) == original
    normalized = restarted.get_blob(upload["blob_ref"])
    assert normalized != original and len(normalized) < 600000
    assert Image.open(io.BytesIO(normalized)).format == "JPEG"
    assert hashlib.sha256(normalized).hexdigest() == upload["sha256"]
    assert "10101" not in upload["upload_file"] and "김학생" not in upload["upload_file"]
    assert "그래프" not in upload["upload_file"]
    for path in tmp_path.rglob("*.enc"):
        assert "김학생".encode() not in path.read_bytes()
        assert original not in path.read_bytes()


def test_deduplication_is_scoped_to_activity_student_class_and_preview():
    room, aid, service = setup()
    first = save(service, aid)
    duplicate = save(service, aid, filename="other.png", name="수정한 이름")
    assert duplicate == first
    other_activity = room.save_activity(first["course_id"], "다른 활동", "문제", "답", ["설명"],
                                        5, now()[:10], None)
    uploads = [first, save(service, aid, student_id="10102"), save(service, aid, class_id="2반"),
               save(service, aid, preview=True), save(service, other_activity)]
    assert len({upload["id"] for upload in uploads}) == 5
    assert len({upload["original_ref"] for upload in uploads}) == 5
    assert len({upload["upload_file"] for upload in uploads}) == 5
    assert len(room.store.read()["course_uploads"]) == 5
    assert service.for_student(aid, "10101", "1반") == [first]
    assert service.for_student(aid, "", "1반") == []
    for activity, student, class_id, preview in [(aid, "10102", "1반", False),
                                               (aid, "10101", "2반", False),
                                               (aid, "10101", "1반", True),
                                               (other_activity, "10101", "1반", False)]:
        with pytest.raises(AppError, match="다른 학생이나 활동"):
            service.get(first["id"], activity, student, class_id, preview)
    with pytest.raises(AppError, match="중복"):
        owned_uploads(room.store.read(), [first["id"], first["id"]], aid, "10101", "1반")
    with pytest.raises(AppError, match="식별자"):
        owned_uploads(room.store.read(), [{}], aid, "10101", "1반")
    with pytest.raises(AppError, match="파일 경로"):
        uploaded_file_path(dict(first, upload_file="../../identity.json"))


@pytest.mark.parametrize("values,error", [
    ({"student_id": ""}, "학번과 이름"), ({"name": ""}, "학번과 이름"),
    ({"class_id": "7반"}, "참여 대상"), ({"raw": b"not an image"}, "이미지를 읽지"),
    ({"raw": capture("GIF")}, "PNG 또는 JPG"), ({"filename": ""}, "파일 이름"),
    ({"raw": b"a" * 12_000_001}, "12MB"),
])
def test_invalid_uploads_leave_no_files_or_saved_record(values, error):
    room, aid, service = setup()
    with pytest.raises(AppError, match=error):
        save(service, aid, **values)
    assert not room.store.blobs and not room.store.uploads
    assert not room.store.read().get("course_uploads")


def test_drafts_can_be_saved_after_timer_expiry_but_hidden_future_activities_reject():
    room, aid, service = setup()
    room.start_timer(aid, 1)
    room.adjust_timer(aid, 0)
    assert save(service, aid)["saved"]
    room.set_status(aid, "숨김")
    with pytest.raises(AppError, match="현재 파일"):
        save(service, aid, student_id="10102")
    room.set_status(aid, "진행 중")
    future = (date.fromisoformat(now()[:10]) + timedelta(days=1)).isoformat()
    room.store.transaction(lambda state: state["course_activities"][aid].update(start=future))
    with pytest.raises(AppError, match="현재 파일"):
        save(service, aid, student_id="10102")


class UploadGitHub:
    def __init__(self):
        self.headers, self.files, self.shas = {}, {}, {}
        self.writes, self.urls = [], []
        self.fail_path = None
        self.conflict_path = None
        self.lock = threading.RLock()

    @staticmethod
    def response(status, payload):
        return SimpleNamespace(status_code=status, json=lambda: payload)

    def request(self, method, url, timeout, **kwargs):
        with self.lock:
            self.urls.append(url)
            if "/contents/" not in url:
                return self.response(200, {"private": True})
            path = url.split("/contents/", 1)[1]
            if method == "GET":
                if path not in self.files:
                    return self.response(404, {})
                return self.response(200, {"content": base64.b64encode(self.files[path]).decode(),
                                           "encoding": "base64", "sha": self.shas[path]})
            body = kwargs["json"]
            self.writes.append(path)
            if self.fail_path and self.fail_path in path:
                self.fail_path = None
                return self.response(503, {})
            if self.conflict_path and self.conflict_path in path:
                self.conflict_path = None
                return self.response(409, {})
            if body.get("sha") != self.shas.get(path):
                return self.response(409, {})
            raw = base64.b64decode(body["content"])
            self.files[path] = raw
            self.shas[path] = hashlib.sha256(raw).hexdigest()
            return self.response(200, {})


def github_setup():
    key, session = Fernet.generate_key().decode(), UploadGitHub()
    store = GitHubStore("jsku-sm/class-dashboard-data", "main", "fake-token", key, session=session)
    room, aid, service = setup(store)
    return room, aid, service, session, key


def test_github_upload_saves_encrypted_original_metadata_and_normalized_file_before_state():
    room, aid, service, session, key = github_setup()
    before = len(session.writes)
    original = capture()
    upload = save(service, aid, original)
    assert all(url.startswith("https://api.github.com/repos/jsku-sm/class-dashboard-data")
               for url in session.urls)
    writes = session.writes[before:]
    original_path = f"think-learning-v1/images/{upload['original_ref']}.enc"
    normalized_path = f"think-learning-v1/images/{upload['blob_ref']}.enc"
    metadata_path = "think-learning-v1/" + upload["upload_file"]
    assert writes == [original_path, normalized_path, metadata_path, "think-learning-v1/state.enc"]
    assert Fernet(key.encode()).decrypt(session.files[original_path]) == original
    assert all("김학생".encode() not in raw and original not in raw for raw in session.files.values())
    restarted = GitHubStore("jsku-sm/class-dashboard-data", "main", "fake-token", key, session=session)
    assert restarted.get_uploaded_file(upload) == upload
    assert restarted.get_blob(upload["original_ref"]) == original
    assert not room.attempts(aid)
    writes_before_retry = len(session.writes)
    assert save(service, aid, original, filename="other.png") == upload
    assert len(session.writes) == writes_before_retry


@pytest.mark.parametrize("failed_path", ["/images/", "/course-uploads/", "/state.enc"])
def test_github_failure_does_not_publish_saved_upload_and_retry_completes(failed_path):
    room, aid, service, session, key = github_setup()
    session.fail_path = failed_path
    with pytest.raises(AppError):
        save(service, aid)
    assert not room.store.read().get("course_uploads")
    assert service.for_student(aid, "10101", "1반") == []
    metadata_before_retry = [path for path in session.files if "/course-uploads/" in path]
    before = len(session.writes)
    upload = save(service, aid, filename="다시올린파일.png")
    assert upload["saved"] and len(room.store.read()["course_uploads"]) == 1
    assert room.store.get_uploaded_file(upload) == upload
    assert room.store.get_blob(upload["original_ref"]) == capture()
    if failed_path == "/state.enc":
        assert metadata_before_retry
        assert upload["filename"] == "그래프.png"
        assert session.writes[before:] == ["think-learning-v1/state.enc"]


def test_blob_and_metadata_conflicts_retry_without_replacing_existing_content():
    room, aid, service, session, key = github_setup()
    session.conflict_path = "/images/"
    first = save(service, aid)
    session.conflict_path = "/course-uploads/"
    second = save(service, aid, student_id="10102")
    assert first["saved"] and second["saved"]
    with pytest.raises(AppError, match="기존 이미지"):
        room.store.put_blob(first["original_ref"], capture("JPEG"))
    assert room.store.get_blob(first["original_ref"]) == capture()


def test_concurrent_identical_uploads_reuse_original_and_canonical_metadata():
    room, aid, service, session, key = github_setup()
    other_store = GitHubStore("jsku-sm/class-dashboard-data", "main", "fake-token", key, session=session)
    other_service = UploadedFiles(other_store)
    other_store.read()
    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(lambda item: save(item[0], aid, filename=item[1]),
                                [(service, "첫파일.png"), (other_service, "두번째파일.png")]))
    assert records[0] == records[1]
    room.store.invalidate()
    assert len(room.store.read()["course_uploads"]) == 1
    assert len([path for path in session.files if "/images/" in path]) == 2
    assert len([path for path in session.files if "/course-uploads/" in path]) == 1


def test_local_write_error_is_reported_without_saved_record():
    class FullDisk(MemoryStore):
        def put_blob(self, name, data):
            raise OSError("disk full")

    room, aid, service = setup(FullDisk())
    with pytest.raises(AppError, match="저장에 실패"):
        save(service, aid)
    assert not room.store.read().get("course_uploads")
