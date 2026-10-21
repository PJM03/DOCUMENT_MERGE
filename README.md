# 문서 병합 프로그램 (PDF / HWPX Merger)

Windows 기본(Native) GUI 디자인을 적용한 간편하고 직관적인 **PDF 및 HWPX 문서 병합(Merge) 프로그램**입니다.  
상단 카테고리 탭(`ttk.Notebook`)을 통해 **[PDF 병합]** 화면과 **[HWPX 병합]** 화면이 완전히 분리되어 있으며, 각 포맷의 파일들을 드래그 앤 드롭하거나 파일 선택 창으로 추가하고 원하는 순서로 배치하여 하나의 문서로 병합할 수 있습니다.

> **개발 정보 (Built with AI)**  
> 본 프로젝트는 **Google Antigravity** 환경에서 **Gemini 4 Argon** 모델을 사용하여 설계·개발 및 패키징되었습니다.

---

## 주요 기능

- **카테고리별 전용 화면 분리 (`[PDF 병합]` / `[HWPX 병합]`)**
  - 상단 탭을 통해 **PDF 전용 병합 화면**과 **HWPX 전용 병합 화면**이 독립적으로 분리되어 있어 각 포맷별로 섞이지 않고 쾌적하게 작업할 수 있습니다.
- **한/글 미설치 환경에서도 동작하는 순수 Python HWPX 병합 엔진 (`HwpxMerger`)**
  - 개방형 한/글 표준 포맷(`.hwpx`, OWPML)의 `Contents/header.xml`(글꼴, 글자 모양, 문단 모양, 테두리/배경, 스타일)과 `BinData/*`(첨부 이미지), `Contents/section*.xml`(본문 문단 및 표)을 분석하여 서식과 이미지를 보존한 채 병합합니다.
  - **쪽 나눔 옵션 제공**: HWPX 병합 화면 하단의 **`[✓] 파일마다 새 페이지(쪽 나눔)로 구분`** 체크박스를 통해 파일마다 새 페이지에서 시작할지, 이전 문서 본문 뒤에 바로 이어 붙일지 선택할 수 있습니다.
- **Windows 네이티브 GUI 디자인**
  - 운영체제 버전에 맞춘 Windows 기본 테마(`vista` / `xpnative`)와 `맑은 고딕` 시스템 폰트, 고해상도(High-DPI) 디스플레이 스케일링을 지원합니다.
- **탐색기 드래그 앤 드롭(Drag & Drop) 및 복수 파일 추가 버튼**
  - Windows 탐색기에서 여러 개의 PDF 또는 HWPX 파일(또는 폴더)을 현재 열려 있는 탭 화면에 직접 끌어다 놓거나, **`[파일 추가...]`** 버튼을 눌러 복수의 파일을 한 번에 삽입할 수 있습니다.
- **마우스 드래그 앤 드롭 순서 변경**
  - 목록에 올라온 파일들을 마우스로 클릭한 채 위/아래로 드래그 앤 드롭하여 병합 순서를 실시간으로 변경할 수 있습니다.
  - 보조 컨트롤로 **`[▲ 위로]`**, **`[▼ 아래로]`**, **`[선택 삭제]`**(`Delete` 키), **`[전체 삭제]`** 버튼을 제공합니다.
- **페이지 수 기반 진행도 다이얼로그 & 파일 저장 창**
  - **`[MERGE]`** 버튼을 누르면 전체 페이지 수만큼 진행률 바(`0 / N 페이지` → `N / N 페이지`)가 올라가는 진행 확인 대화상자가 표시됩니다.
  - 병합 완료 후 자동으로 열리는 **파일 저장 대화상자**에서 저장할 경로와 파일 이름(`.pdf` 또는 `.hwpx`)을 지정할 수 있습니다.
- **독립 실행형 `.exe` 자동 빌드 및 배포 (GitHub Actions)**
  - 푸시 시 GitHub Actions가 자동으로 단일 실행 파일(`PDF_Merger.exe`)을 빌드 및 디지털 서명(SHA-256)하여 GitHub Releases에 배포합니다.

---

## 실행 방법

### 1. 실행 파일(`.exe`)로 바로 실행 (Python 설치 불필요)
[GitHub Releases 페이지](https://github.com/PJM03/pdf_merge/releases)에서 최신 `PDF_Merger.exe` 파일을 다운로드하여 바로 실행합니다.

### 2. Python 소스 코드로 실행
Python 3.10 이상 환경에서 아래 명령어로 필요한 패키지를 설치한 뒤 실행할 수 있습니다.

```bash
pip install -r requirements.txt
python pdf_merge.py
```

---

## 사용 방법

1. 프로그램을 실행하고 상단 탭에서 **`[PDF 병합]`** 또는 **`[HWPX 병합]`** 카테고리를 선택합니다.
2. 병합할 파일들을 창 안으로 **드래그 앤 드롭**하거나 상단의 **`[파일 추가...]`** 버튼을 눌러 추가합니다.
3. 목록에서 항목을 마우스로 드래그 앤 드롭(또는 **`[▲ 위로]`** / **`[▼ 아래로]`** 버튼 클릭)하여 원하는 병합 순서로 정렬합니다.
4. *(HWPX 병합 시)* 하단의 **`파일마다 새 페이지(쪽 나눔)로 구분`** 체크박스 옵션을 원하는 대로 선택합니다.
5. 우측 하단의 **`[MERGE]`** 버튼을 클릭합니다.
6. 페이지 단위로 진행도가 표시되는 다이얼로그가 완료되면, 자동으로 열리는 **파일 저장 창**에서 저장할 위치와 파일명을 입력하고 저장합니다.

---

## 기술 스택 및 개발 환경

- **Language**: Python 3.14
- **GUI**: Tkinter / `ttk` (`ttk.Notebook` 탭 분리, Windows Native Theme) + Win32 API (`ctypes` 기반 `WM_DROPFILES` 훅 및 High-DPI 설정)
- **Document Engines**: [`pypdf`](https://pypi.org/project/pypdf/) (PDF), 내장 `zipfile` + `xml.etree.ElementTree` 기반 `HwpxMerger` (OWPML HWPX)
- **CI/CD & Packaging**: GitHub Actions (`windows-latest`), `PyInstaller`, Windows SDK `signtool.exe` (SHA-256 Authenticode)
- **AI Agent & Model**: **Antigravity** (**Gemini 4 Argon**)
