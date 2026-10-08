import base64
import copy
import io
import json
import threading
import uuid
import wave
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import pytest
from cryptography.fernet import Fernet
from PIL import Image
from think_app.config import Settings
from think_app.storage import MemoryStore, LocalStore, GitHubStore, AppError, blank
from think_app.service import Service, report_rows, safe_csv
from think_app.ai import AI, Feedback, normalized_image, board_image


@pytest.fixture
def classroom():
    svc = Service(MemoryStore(), Settings(mode="local", teacher_password="teacher-password"))
    svc.bootstrap()
    teacher = svc.login("teacher", "teacher-password")
    code = svc.class_code(teacher, "1-1")
    for sid in ["10101", "10102"]:
        svc.register(sid, "학생"+sid[-1], "1-1", code, "student-password")
    other = svc.class_code(teacher, "1-2")
    svc.register("10201", "다른반", "1-2", other, "student-password")
    a = svc.login("10101", "student-password")
    b = svc.login("10102", "student-password")
    c = svc.login("10201", "student-password")
    aid = svc.create_activity(teacher, "원의 중심", "(x-2)²+(y+1)²=9의 중심과 반지름은?",
                              "중심 (2,-1), 반지름 3", ["중심을 근거로 설명한다", "반지름을 설명한다"], ["1-1"], 3, "공통수학2")
    return svc, teacher, a, b, c, aid


def submit(svc, student, aid, text="중심은 (2,-1), 반지름 3입니다.", consent=False, images=None, rid=None):
    return svc.submit(student, aid, text, "", images or [], "", consent, rid or uuid.uuid4().hex)


def feedback_data(criteria):
    return dict(correctness="correct", explanation_score=3, praise="표준형을 잘 비교했어요.", observation="부호를 설명했어요.",
                hint="반지름의 제곱을 확인해 봐요.", next_question="중심이 이동하면 식은 어떻게 변할까요?",
                criteria=[{"criterion": c, "level": "met", "evidence": "중심 (2,-1)이라고 설명함"} for c in criteria],
                misconceptions=[], needs_teacher_review=False, uncertainty_reason="")


class GoodAI:
    available = True
    calls = 0
    def feedback(self, task, attempt, images):
        self.calls += 1
        return feedback_data(task["criteria"])


def test_full_teacher_student_revision_review(classroom):
    svc, teacher, a, b, c, aid = classroom
    first = submit(svc, a, aid, text="중심은 (2,1)인 것 같아요", consent=True)
    ai = GoodAI()
    svc.evaluate(a, first, ai)
    svc.review(teacher, first, "incorrect", 1, "y+1을 y-(-1)로 비교해 볼까요?")
    second = submit(svc, a, aid, consent=True)
    svc.evaluate(a, second, ai)
    svc.review(teacher, second, "correct", 4, "중심의 부호와 반지름을 근거와 함께 설명했어요.")
    report = report_rows(svc.snapshot(teacher), aid)
    row = next(r for r in report if r["학번"] == "10101")
    assert (row["제출횟수"], row["변화"], row["최근설명점수"]) == (2, 3, 4)
    assert len(svc.snapshot(a)["attempts"]) == 2
    assert svc.snapshot(a)["attempts"][-1]["review"]["score"] == 4
    assert len(report) == 2  # Other class is excluded.


def test_role_class_and_submission_isolation(classroom):
    svc, teacher, a, b, c, aid = classroom
    attempt = submit(svc, a, aid, images=[b"image-test-bytes"])
    assert not svc.snapshot(b)["attempts"]
    assert not svc.snapshot(c)["activities"]
    assert "expected" not in svc.snapshot(a)["activities"][0]
    assert "activity_snapshot" not in svc.snapshot(a)["attempts"][0]
    for action in [lambda: svc.class_code(a, "1-1"), lambda: svc.image(b, attempt, 0),
                   lambda: submit(svc, c, aid), lambda: svc.review(a, attempt, "correct", 4, "fake")]:
        with pytest.raises(AppError): action()


def test_missing_ai_is_not_fake_feedback(classroom):
    svc, teacher, a, _, _, aid = classroom
    attempt = submit(svc, a, aid)
    with pytest.raises(AppError): svc.evaluate(a, attempt, AI(Settings()))
    record = svc.snapshot(a)["attempts"][0]
    assert record["status"] == "pending" and record["feedback"] is None


def test_ai_error_preserves_work_and_can_retry(classroom):
    svc, teacher, a, _, _, aid = classroom
    attempt = submit(svc, a, aid, consent=True)
    class Broken:
        available = True
        def feedback(self, *args): raise AppError("테스트 연결 실패")
    with pytest.raises(AppError): svc.evaluate(a, attempt, Broken())
    record = svc.snapshot(a)["attempts"][0]
    assert record["text"] and record["status"] == "error"
    good = GoodAI()
    svc.evaluate(a, attempt, good)
    svc.evaluate(a, attempt, good)
    assert good.calls == 1
    assert svc.snapshot(a)["attempts"][0]["status"] == "ready"


def test_no_consent_blocks_api_and_teacher_cannot_grant(classroom):
    svc, teacher, a, _, _, aid = classroom
    attempt = submit(svc, a, aid)
    ai = GoodAI()
    with pytest.raises(AppError): svc.evaluate(teacher, attempt, ai)
    with pytest.raises(AppError): svc.allow_ai(teacher, attempt)
    assert ai.calls == 0
    svc.allow_ai(a, attempt)
    svc.evaluate(a, attempt, ai)
    assert ai.calls == 1


def test_duplicate_submit_attempt_cap_and_closed_task(classroom):
    svc, teacher, a, _, _, aid = classroom
    rid = uuid.uuid4().hex
    assert submit(svc,a,aid,rid=rid) == submit(svc,a,aid,rid=rid)
    assert len(svc.snapshot(a)["attempts"]) == 1
    submit(svc,a,aid); submit(svc,a,aid)
    with pytest.raises(AppError): submit(svc,a,aid)
    svc.toggle_activity(teacher,aid)
    with pytest.raises(AppError): submit(svc,a,aid)


def test_password_change_revokes_old_sessions_and_login_lock(classroom):
    svc, teacher, a, _, _, aid = classroom
    svc.change_password(a, "student-password", "new-password-123")
    with pytest.raises(AppError): svc.snapshot(a)
    for _ in range(5):
        with pytest.raises(AppError): svc.login("10101", "wrong")
    with pytest.raises(AppError, match="5분"): svc.login("10101", "new-password-123")
    svc.store.transaction(lambda s: s["login_failures"].clear())
    assert svc.snapshot(svc.login("10101", "new-password-123"))["user"]["id"] == "10101"


def test_25_concurrent_submissions_preserve_all(classroom):
    svc, teacher, a, _, _, aid = classroom
    # Test isolated documents/CAS service under simultaneous authenticated users,
    # not real GitHub throughput.
    svc.store.transaction(lambda s: s["activities"][aid].update(max_attempts=30))
    with ThreadPoolExecutor(max_workers=25) as pool:
        ids = list(pool.map(lambda _: submit(svc,a,aid), range(25)))
    attempts = svc.snapshot(a)["attempts"]
    assert len(set(ids)) == len(attempts) == 25
    assert sorted(x["number"] for x in attempts) == list(range(1,26))


def test_encrypted_restart_and_wrong_key(tmp_path):
    key = Fernet.generate_key().decode()
    store = LocalStore(tmp_path,key)
    store.transaction(lambda s: s["users"].update({"123": {"name": "비공개학생"}}))
    assert "비공개학생".encode() not in (tmp_path/"state.enc").read_bytes()
    assert LocalStore(tmp_path,key).read()["users"]["123"]["name"] == "비공개학생"
    with pytest.raises(AppError): LocalStore(tmp_path,Fernet.generate_key().decode()).read()


class FakeGitHub:
    def __init__(self, private=True):
        self.headers={}; self.private=private; self.raw=None; self.sha=None; self.puts=0; self.gets=0; self.conflict=True
    def request(self,method,url,timeout,**kwargs):
        def response(code,data): return SimpleNamespace(status_code=code,json=lambda:data)
        if "/contents/" not in url: return response(200,{"private":self.private})
        if method=="GET":
            self.gets+=1
            if self.raw is None: return response(404,{})
            return response(200,{"content":base64.b64encode(self.raw).decode(),"encoding":"base64","sha":self.sha})
        self.puts+=1
        if self.conflict:
            self.conflict=False
            concurrent=blank(); concurrent["classes"]["1-2"]={"created":"other writer"}
            self.raw=self.cipher.encrypt(json.dumps(concurrent).encode()); self.sha="concurrent-sha"
            return response(409,{})
        body=kwargs["json"]
        if body.get("sha") != self.sha: return response(409,{})
        self.raw=base64.b64decode(body["content"]); self.sha="new-sha"
        return response(200,{})


def test_github_conflict_reloads_and_preserves_other_writer():
    key=Fernet.generate_key().decode(); fake=FakeGitHub(); fake.cipher=Fernet(key.encode())
    store=GitHubStore("owner/repo","main","fake-token",key,session=fake)
    store.transaction(lambda s:s["classes"].update({"1-1":{"created":"this writer"}}))
    assert set(store.read()["classes"]) == {"1-1","1-2"}
    assert fake.puts == 2


def test_public_github_refused_before_writes():
    fake=FakeGitHub(private=False)
    with pytest.raises(AppError,match="비공개"):
        GitHubStore("owner/repo","main","fake",Fernet.generate_key().decode(),session=fake)
    assert fake.puts == 0


def test_board_reruns_share_read_cache_but_writes_refresh():
    key=Fernet.generate_key().decode(); fake=FakeGitHub(); fake.cipher=Fernet(key.encode()); fake.conflict=False
    store=GitHubStore("owner/repo","main","fake",key,session=fake)
    for _ in range(25): store.read()
    assert fake.gets == 1
    store.transaction(lambda s:s["classes"].update({"1-1":{}}))
    assert "1-1" in store.read()["classes"]
    assert fake.gets == 3  # Fresh transactional GET plus refreshed read.


def test_api_request_image_schema_and_uncertainty(classroom):
    svc,teacher,a,_,_,aid=classroom
    task=svc.store.read()["activities"][aid]
    data=feedback_data(task["criteria"]); data.update(correctness="uncertain",explanation_score=3)
    capture={}
    def parse(**kwargs):
        capture.update(kwargs)
        return SimpleNamespace(output_parsed=Feedback(**data))
    client=SimpleNamespace(responses=SimpleNamespace(parse=parse))
    ai=AI(Settings(api_key="fake",ai_enabled=True),client=client)
    result=ai.feedback(task,{"text":"설명", "transcript":"목소리", "reflection":"성찰"},[b"jpeg"])
    assert result["explanation_score"] is None and result["needs_teacher_review"]
    assert capture["store"] is False and capture["text_format"] is Feedback
    assert capture["input"][1]["content"][1]["type"] == "input_image"
    assert "student_id" not in json.dumps(capture["input"],ensure_ascii=False)


def test_audio_transcription_and_image_normalization():
    wav=io.BytesIO()
    with wave.open(wav,"wb") as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b"\0"*32000)
    capture={}
    def transcribe(**kwargs):
        capture.update(kwargs);return SimpleNamespace(text="중심은 이, 마이너스 일입니다.")
    client=SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=transcribe)))
    ai=AI(Settings(api_key="fake",ai_enabled=True),client=client)
    assert ai.transcribe(wav.getvalue()).startswith("중심은")
    assert capture["language"] == "ko" and capture["response_format"] == "json"
    image=Image.new("RGBA",(1900,1200),(255,0,0,0));out=io.BytesIO();image.save(out,"PNG")
    normalized=normalized_image(out.getvalue())
    result=Image.open(io.BytesIO(normalized))
    assert max(result.size)<=1500 and result.mode == "RGB"
    assert board_image({"hasInk":False}) is None
    with pytest.raises(AppError): normalized_image(b"not an image")


def test_csv_formula_escape():
    data=safe_csv([{"설명":"=HYPERLINK(\"bad\")","이름":"학생"}]).decode("utf-8-sig")
    assert "'=HYPERLINK" in data
