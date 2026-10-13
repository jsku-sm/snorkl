# 구쌤 · 생각을 말해요

수식·그래프, 퀴즈, 손글씨 풀이, 음성 녹음으로 수업 활동을 만들고 학생 답안을 확인하는 Streamlit 앱입니다. 데스모스 수업을 선택하면 현재 화면에서 계산기를 열며, GitHub 모드에서는 학생별 제출 파일을 암호화 저장합니다.

```bash
python3 -m pip install -r requirements.txt
python3 -m streamlit run streamlit_app.py
```

설치·Desmos API 키·학생 참여·GitHub 저장 설정은 [시작하기 안내](README_시작하기.md)를 참고하세요. 실제 토큰과 암호화키는 `secrets.toml.example`을 참고하여 Streamlit Secrets 또는 `.streamlit/secrets.toml`에 입력합니다.

모범답안은 문서 파일을 불러오거나 AI 웹 검색으로 출처와 함께 가져와 수정할 수 있습니다. 마감일은 시작일과 같은 날짜가 기본이며, 교사는 배포 시 풀이 시간을 설정하고 진행 중에도 남은 시간을 조정합니다. 학생 화면에는 공통 타이머가 표시됩니다. 데스모스는 API 키 없이도 현재 화면에서 열립니다.

데스모스의 **캡처하기**로 화면을 선택하고 영역을 드래그해 PNG를 저장합니다. 파일을 업로드하면 GitHub 저장 모드에서 `jsku-sm/class-dashboard-data`에 원본을 즉시 암호화 보관하며, 답안 제출에는 저장된 파일을 연결합니다.

현재 기본 화면은 로그인 없는 수업 대시보드입니다. `think_app/ui.py`의 기존 로그인 기반 모듈과 데이터·사용 방법을 구분한 설명은 시작하기 안내에 있습니다.
