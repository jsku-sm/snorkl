import base64
import hashlib
import io
import uuid
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from PIL import Image

from think_app.classroom import Classroom
from think_app.courses import CourseManager, now
from think_app.reference_images import ReferenceImages, owned_reference_images
from think_app.storage import AppError, GitHubStore, LocalStore, MemoryStore


def image_bytes(color="red", format="PNG"):
    image = Image.new("RGB", (420, 320), color)
    output = io.BytesIO()
    image.save(output, format=format)
    return output.getvalue()


def setup(store=None):
    store = store or MemoryStore()
    course_id = CourseManager(store).create("수학", "#6948f5", ["1반"])
    return store, course_id, Classroom(store), ReferenceImages(store)


def activity(room, course_id, expected="", references=None, aid=None):
    return room.save_activity(course_id, "직선 그래프", "그래프를 나타내세요", expected,
                              ["기울기를 설명한다"], 5, now()[:10], None, aid,
                              expected_images=references)


def save(service, course_id, raw=None, **kwargs):
    return service.save(course_id, image_bytes() if raw is None else raw, "모범 그래프.png", **kwargs)


def test_reference_and_image_only_activity_survive_restart_encrypted(tmp_path):
    key = Fernet.generate_key().decode()
    store, course_id, room, service = setup(LocalStore(tmp_path, key))
    original = image_bytes()
    record = save(service, course_id, original, source_url="https://example.org/graph.png",
                  page_url="https://example.org/math", title="일차함수 그래프")
    aid = activity(room, course_id, references=[record])
    restarted = LocalStore(tmp_path, key)
    task = Classroom(restarted).activity(aid)
    assert task["expected"] == "" and task["expected_images"] == [record]
    service = ReferenceImages(restarted)
    assert service.get(record["id"], course_id) == record
    assert service.read(record, course_id, original=True) == original
    normalized = service.read(record, course_id)
    assert len(normalized) <= 600000 and Image.open(io.BytesIO(normalized)).format == "JPEG"
    assert record["sha256"] == hashlib.sha256(normalized).hexdigest()
    for path in tmp_path.rglob("*.enc"):
        assert original not in path.read_bytes()
        assert "일차함수 그래프".encode() not in path.read_bytes()
        assert b"example.org" not in path.read_bytes()


@pytest.mark.parametrize("format,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg"),
                                          ("WEBP", "image/webp"), ("GIF", "image/gif")])
def test_reference_preserves_original_and_normalizes_supported_formats(format, mime):
    store, course_id, _, service = setup()
    raw = image_bytes(format=format)
    record = save(service, course_id, raw)
    assert record["original_mime"] == mime
    assert service.read(record, course_id, original=True) == raw
    assert Image.open(io.BytesIO(service.read(record, course_id))).format == "JPEG"


def test_reference_is_canonical_deduplicated_and_scoped_to_course():
    store, course_id, room, service = setup()
    first = save(service, course_id, source_url="https://example.org/first.png", title="첫 출처")
    assert save(service, course_id, source_url="https://example.org/second.png", title="두번째 출처") == first
    assert len(store.blobs) == 2 and len(store.read()["course_reference_images"]) == 1
    other_course = CourseManager(store).create("다른 수업", "#6948f5", ["1반"])
    other = save(service, other_course)
    assert first["id"] != other["id"] and first["blob_ref"] != other["blob_ref"]
    with pytest.raises(AppError, match="다른 수업"):
        activity(room, other_course, references=[first])
    with pytest.raises(AppError, match="저장 내용과 다릅니다"):
        activity(room, course_id, references=[dict(first, blob_ref=other["blob_ref"])])
    with pytest.raises(AppError, match="저장 내용과 다릅니다"):
        service.read(dict(first, source_url="https://fake.org/image.png"), course_id)
    with pytest.raises(AppError, match="중복"):
        owned_reference_images(store.read(), [first, first["id"]], course_id)
    with pytest.raises(AppError, match="식별자"):
        activity(room, course_id, references=["../../image"])
    with pytest.raises(AppError, match="최대 3개"):
        activity(room, course_id, references=[first] * 4)


def test_edit_preserves_images_and_explicit_empty_list_clears_them():
    store, course_id, room, service = setup()
    record = save(service, course_id)
    aid = activity(room, course_id, references=[record["id"]])
    activity(room, course_id, expected="설명 추가", aid=aid)
    assert room.activity(aid)["expected_images"] == [record]
    activity(room, course_id, expected="수식으로 비교", references=[], aid=aid)
    assert room.activity(aid)["expected_images"] == []
    with pytest.raises(AppError, match="모범답안 내용을 입력"):
        activity(room, course_id, expected="", references=[], aid=aid)
    assert room.activity(aid)["expected"] == "수식으로 비교"


def test_submission_snapshots_private_teacher_refs_separately_from_student_images():
    store, course_id, room, service = setup()
    record = save(service, course_id)
    aid = activity(room, course_id, references=[record])
    student_image = image_bytes("blue")
    attempt_id = room.submit(aid, "10101", "학생", "1반", "풀이 설명", [student_image],
                             True, uuid.uuid4().hex)
    activity(room, course_id, expected="새 모범답안", references=[], aid=aid)
    attempt = room.attempts(aid)[0]
    assert attempt["id"] == attempt_id
    assert attempt["activity_snapshot"]["expected_images"] == [record]
    assert record["blob_ref"] not in attempt["images"]
    assert record["original_ref"] not in attempt["images"]
    assert [store.get_blob(ref) for ref in attempt["images"]] == [student_image]
    # The persisted student's own assets contain only the student's answer.
    bundle = store.get_student_submission(attempt)
    assert set(bundle["assets"]) == set(attempt["images"])


@pytest.mark.parametrize("expected", ["", "y=2x+1"])
def test_image_reference_requires_teacher_review_without_any_ai_call(expected):
    store, course_id, room, service = setup()
    aid = activity(room, course_id, expected=expected, references=[save(service, course_id)])
    attempt_id = room.submit(aid, "10101", "학생", "1반", "풀이 설명", [], True, uuid.uuid4().hex)
    class NeverCalledAI:
        available = True
        def feedback(self, *args, **kwargs):
            pytest.fail("Teacher reference images must not be transmitted to the AI service.")
        def transcribe(self, *args, **kwargs):
            pytest.fail("Image-reference evaluation requires teacher review before any AI call.")
    with pytest.raises(AppError, match="교사가 직접 평가"):
        room.evaluate(attempt_id, NeverCalledAI())
    assert room.attempts(aid)[0]["feedback"] is None


def test_changed_reference_during_submission_is_rejected():
    class ChangingStore(MemoryStore):
        pending = None
        def transaction(self, change):
            if self.pending:
                aid, replacement = self.pending
                self.pending = None
                self.state["course_activities"][aid]["expected_images"] = [replacement]
            return super().transaction(change)
    store, course_id, room, service = setup(ChangingStore())
    first = save(service, course_id)
    second = save(service, course_id, image_bytes("blue"))
    aid = activity(room, course_id, references=[first])
    store.pending = (aid, second)
    with pytest.raises(AppError, match="활동 내용이 변경"):
        room.submit(aid, "10101", "학생", "1반", "설명", [], True, uuid.uuid4().hex)
    assert not room.attempts(aid)


def test_corrupted_normalized_image_cannot_be_attached_or_read():
    store, course_id, room, service = setup()
    record = save(service, course_id)
    store.blobs[record["blob_ref"]] = b"changed bytes"
    with pytest.raises(AppError, match="모범답안 이미지를 확인"):
        activity(room, course_id, references=[record])
    with pytest.raises(AppError, match="모범답안 이미지를 확인"):
        service.read(record, course_id)


@pytest.mark.parametrize("kwargs,error", [
    ({"source_url": "javascript:alert(1)"}, "출처 주소"),
    ({"page_url": "https://name:pass@example.org"}, "출처 주소"),
    ({"title": "a" * 501}, "500자"),
    ({"raw": b"bad image"}, "이미지를 읽지"),
    ({"raw": b"a" * 12_000_001}, "12MB"),
    ({"raw": image_bytes(format="TIFF")}, "PNG, JPG"),
])
def test_invalid_reference_does_not_publish_any_file_or_record(kwargs, error):
    store, course_id, _, service = setup()
    with pytest.raises(AppError, match=error):
        save(service, course_id, **kwargs)
    assert not store.blobs and not store.read().get("course_reference_images")


def test_failed_blob_write_does_not_publish_reference_metadata():
    class FullDisk(MemoryStore):
        def put_blob(self, name, data):
            raise OSError("disk full")
    store, course_id, _, service = setup(FullDisk())
    with pytest.raises(AppError, match="저장에 실패"):
        save(service, course_id)
    assert not store.read().get("course_reference_images")


class ReferenceGitHub:
    def __init__(self):
        self.headers, self.files, self.shas = {}, {}, {}
        self.writes = []
        self.fail_path = None

    def request(self, method, url, timeout, **kwargs):
        def response(status, payload):
            return SimpleNamespace(status_code=status, json=lambda: payload)
        if "/contents/" not in url:
            return response(200, {"private": True})
        assert url.startswith("https://api.github.com/repos/jsku-sm/class-dashboard-data/")
        path = url.split("/contents/", 1)[1]
        if method == "GET":
            if path not in self.files:
                return response(404, {})
            return response(200, {"content": base64.b64encode(self.files[path]).decode(),
                                  "encoding": "base64", "sha": self.shas[path]})
        body = kwargs["json"]
        self.writes.append(path)
        if self.fail_path and self.fail_path in path:
            self.fail_path = None
            return response(503, {})
        if body.get("sha") != self.shas.get(path):
            return response(409, {})
        raw = base64.b64decode(body["content"])
        self.files[path] = raw
        self.shas[path] = hashlib.sha256(raw).hexdigest()
        return response(200, {})


def test_github_reference_original_and_normalized_files_precede_encrypted_metadata():
    key, session = Fernet.generate_key().decode(), ReferenceGitHub()
    store = GitHubStore("jsku-sm/class-dashboard-data", "main", "fake", key, session=session)
    store, course_id, room, service = setup(store)
    original = image_bytes()
    before = len(session.writes)
    record = save(service, course_id, original)
    original_path = "think-learning-v1/images/" + record["original_ref"] + ".enc"
    normalized_path = "think-learning-v1/images/" + record["blob_ref"] + ".enc"
    assert session.writes[before:] == [original_path, normalized_path, "think-learning-v1/state.enc"]
    assert Fernet(key.encode()).decrypt(session.files[original_path]) == original
    assert all(original not in raw for raw in session.files.values())
    restarted = GitHubStore("jsku-sm/class-dashboard-data", "main", "fake", key, session=session)
    assert ReferenceImages(restarted).get(record["id"], course_id) == record
    assert ReferenceImages(restarted).read(record, course_id, original=True) == original
    aid = activity(room, course_id, references=[record])
    restarted.invalidate()
    assert Classroom(restarted).activity(aid)["expected_images"] == [record]


@pytest.mark.parametrize("failed_path", ["/images/", "/state.enc"])
def test_github_failed_write_leaves_no_saved_reference_and_can_retry(failed_path):
    key, session = Fernet.generate_key().decode(), ReferenceGitHub()
    store = GitHubStore("jsku-sm/class-dashboard-data", "main", "fake", key, session=session)
    store, course_id, _, service = setup(store)
    session.fail_path = failed_path
    with pytest.raises(AppError):
        save(service, course_id)
    assert not store.read().get("course_reference_images")
    record = save(service, course_id)
    assert record["saved"]
    assert service.get(record["id"], course_id) == record
