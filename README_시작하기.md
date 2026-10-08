# Streamlit Cloud 직접 업로드 수정본 v2

**GitHub에는 `app.py`와 `requirements.txt` 두 파일을 같은 위치에 올리면 됩니다.**
이 수정본의 `app.py`에는 교사·학생 화면, 저장 기능, AI 연결 코드, 풀이판이 모두 포함되어 있습니다. 별도의 `think_app` 폴더나 HTML 파일이 없어도 실행됩니다.

## 지금 적용하는 순서

1. 다운로드한 ZIP을 **압축 해제**합니다. ZIP 파일 자체를 GitHub에 올리는 것이 아닙니다.
2. 이 설명학습 앱과 연결한 GitHub 저장소를 엽니다.
3. **Add file → Upload files**에서 수정본의 `app.py`와 `requirements.txt`를 올립니다. 기존 동명 파일을 교체하고 **Commit changes**를 누릅니다.
4. 두 파일이 저장소 첫 화면에 나란히 보이는지 확인합니다. `app(1).py`, `requirements(1).txt`처럼 이름이 바뀌면 안 됩니다.
5. [share.streamlit.io](https://share.streamlit.io)에서 앱이 바라보는 **Repository·Branch**를 확인합니다. 방금 수정한 저장소·브랜치여야 합니다.
6. 실행 파일 **Main file path는 `app.py`**입니다. 이전 `ui.py`, `generate_key.py` 같은 파일을 실행 파일로 선택하면 안 됩니다. 기존 앱의 실행 경로가 다르면 배포 경로를 맞춰 새로 배포해야 할 수 있습니다.
7. 앱 옆 **⋮ → Reboot**를 누릅니다. 의존성 설치와 앱 재시작이 완료될 때까지 기다립니다.
8. 화면 또는 왼쪽 아래에 **실행본 v2 · 2026.10.08**이 표시되면 수정본이 실행 중입니다.

VS Code나 개인 컴퓨터에서 Python을 설치할 필요 없이 위 과정으로 실행할 수 있습니다.
새 앱을 배포한다면 Python 3.12 사용을 권장합니다. 수정본은 Python 3.12에서 검증했습니다.

## 처음 나타나야 하는 화면

Secrets를 아직 설정하지 않았다면 **‘생각을 말해요.’ 로그인 화면**과 **교사로 체험하기 / 학생으로 체험하기** 버튼이 나타납니다.

- 체험 모드는 현재 접속 세션의 임시 기록입니다. 실제 학생 운영에는 아래의 GitHub 저장 설정이 필요합니다.
- AI 키가 없어도 첫 화면, 교사·학생 체험, 풀이 제출, 교사 피드백은 사용할 수 있습니다.
- 실제 AI 분석과 음성 전사는 API 설정 후 사용할 수 있습니다.
- 기존에 저장소·암호화키·교사 계정을 설정했다면 **기존 Secrets를 지우거나 암호화키를 새로 바꾸지 마세요.**

## 달라진 점

- `app.py`를 단일 실행 파일로 바꿔 폴더 누락·이동 문제를 없앴습니다.
- 풀이판 HTML을 `app.py`에 포함했습니다. 실행할 때 서버의 임시 폴더에 자동 준비합니다. 학생 기록 저장 경로는 기존과 같습니다.
- 앱 초기화 중이라는 메시지를 먼저 표시합니다.
- 의존성 누락이나 시작 오류가 나면 화면에 안내와 오류 유형·위치를 표시합니다. 비밀키나 예외의 원문은 표시하지 않습니다.
- 잘못 작성한 Secrets를 ‘설정 없음’으로 처리해 체험 모드로 넘어가던 문제를 고쳤습니다.
- 외부 글꼴 사이트를 불러오지 않고 기본 한글 글꼴을 사용합니다.
- 기존의 과제 등록 → 학생 풀이·음성 설명 → AI 피드백 → 재제출 → 교사 분석 흐름을 유지했습니다.

## GitHub 영구 저장과 실제 AI 사용

처음 배포한다면 별도의 **Private 데이터 저장소**를 만들고 README를 추가해 `main` 브랜치를 생성합니다. 해당 저장소의 Contents Read and write 권한이 있는 fine-grained token이 필요합니다.

Streamlit 앱의 **Settings → Secrets**에 다음 값을 설정합니다. `secrets.toml.example`은 입력 형식 참고용이며, 실제 값이 들어간 파일은 GitHub에 올리지 않습니다.

```toml
THINK_MODE = "github"
GITHUB_REPO = "내아이디/비공개데이터저장소"
GITHUB_BRANCH = "main"
GITHUB_TOKEN = "본인의토큰"
ENCRYPTION_KEY = "기존에사용하던Fernet키또는처음만든키"
THINK_TEACHER_ID = "teacher"
THINK_TEACHER_PASSWORD = "처음정한10자이상비밀번호"

AI_ENABLED = true
OPENAI_API_KEY = "본인의OpenAI_API키"
OPENAI_MODEL = "gpt-4.1-mini"
TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"
```

AI 연결을 나중에 하려면 `AI_ENABLED = false`로 둡니다. API 키는 이 대화에 붙여넣지 말고 Secrets에 직접 입력하세요.

처음 새 암호화키를 만들어야 하고 로컬 Python을 사용할 수 있다면 다음 명령을 사용합니다. 기존 데이터가 있는 경우 새 키를 만들지 않습니다.

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

교사 로그인 후 **학급·학생**에서 가입코드를 발급합니다. 학생은 **학생 가입** 탭에서 학번·이름·학급·가입코드를 입력합니다. 교사와 학생 계정은 이전 버전과 같은 방식입니다.

음성은 2분 이내로 녹음 → AI 사용 선택 → **녹음한 말을 글로 바꾸기** → 전사 확인 → 제출 순서로 사용합니다. 음성 원본은 저장하지 않고, 최종 풀이 이미지와 글 설명·확인한 전사·성찰·피드백을 저장합니다.

## 적용 후에도 빈 화면이라면

실제 앱 주소와 배포 로그를 아직 확인하지 못했으므로, 이번 수정만으로 모든 클라우드 문제의 해결을 보장하지는 않습니다.

1. **v2 표시가 보이지 않으면** 저장소·브랜치·실행 파일 경로가 수정본을 가리키는지 확인합니다.
2. 앱에서 **Manage app → 로그**를 열어 오류 부분을 확인합니다. 파일 설치 단계에서 실패하면 Python 앱의 안내 화면이 표시되기 전일 수 있습니다.
3. 여전히 비어 있으면 **앱 주소(`…streamlit.app`) 또는 로그의 마지막 오류 부분**을 보내 주세요. 비밀키·토큰·비밀번호는 가려 주세요.

공식 안내:

- [파일과 requirements.txt 배치](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/file-organization)
- [의존성 설치](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies)
- [앱 재부팅](https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app/reboot-your-app)
