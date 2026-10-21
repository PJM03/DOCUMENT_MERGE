# PDF 병합 프로그램 (PDF Merger)

Windows 기본(Native) GUI 디자인을 적용한 간편하고 직관적인 **PDF 병합(Merge) 프로그램**입니다.  
여러 개의 PDF 파일을 드래그 앤 드롭하거나 파일 선택 창으로 추가하고, 원하는 순서로 자유롭게 배치한 뒤 하나의 PDF 파일로 병합할 수 있습니다.

> **개발 정보 (Built with AI)**  
> 본 프로젝트는 **Google Antigravity** 환경에서 **Gemini 4 Argon** 모델을 사용하여 설계·개발 및 패키징되었습니다.

---

## 주요 기능

- **Windows 네이티브 GUI 디자인**
  - 운영체제 버전에 맞춘 Windows 기본 테마(`vista` / `xpnative`)와 `맑은 고딕` 시스템 폰트, 고해상도(High-DPI) 디스플레이 스케일링을 지원합니다.
- **탐색기 드래그 앤 드롭(Drag & Drop) 지원**
  - Windows 탐색기에서 여러 개의 PDF 파일(또는 PDF 파일이 들어 있는 폴더)을 프로그램 창에 직접 끌어다 놓아 한 번에 추가할 수 있습니다. (유니코드/한글 파일명 및 경로 완벽 지원)
- **복수 파일 추가 버튼**
  - 상단의 **`[파일 추가...]`** 버튼을 클릭하여 파일 열기 대화상자에서 여러 개의 PDF 파일을 동시에 선택해 삽입할 수 있습니다.
- **마우스 드래그 앤 드롭 순서 변경**
  - 목록에 올라온 PDF 파일들을 마우스로 클릭한 채 위/아래로 드래그 앤 드롭하여 병합 순서를 실시간으로 변경할 수 있습니다.
  - 보조 컨트롤로 **`[▲ 위로]`**, **`[▼ 아래로]`**, **`[선택 삭제]`**(`Delete` 키), **`[전체 삭제]`** 버튼도 함께 제공합니다.
- **페이지 수 기반 진행도 다이얼로그 & 파일 저장 창**
  - **`[MERGE (병합하기)]`** 버튼을 누르면 병합 대상 PDF들의 **전체 페이지 수**만큼 진행률 바(`0 / N 페이지` → `N / N 페이지`)가 올라가는 진행 확인 대화상자가 표시됩니다.
  - 병합이 완료되면 즉시 **파일 저장 대화상자**가 열려 저장할 경로와 파일 이름을 직접 지정할 수 있습니다.
- **독립 실행형 `.exe` 및 디지털 서명**
  - 별도의 Python 설치 없이 바로 실행할 수 있도록 단일 실행 파일(`PDF_Merger.exe`)로 포팅되어 있으며, SHA-256 코드 서명 인증서 및 RFC 3161 타임스탬프 서명이 적용되어 있습니다.

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

1. 프로그램을 실행합니다.
2. 병합할 PDF 파일들을 창 안으로 **드래그 앤 드롭**하거나 상단의 **`[파일 추가...]`** 버튼을 눌러 추가합니다.
3. 목록에서 항목을 마우스로 드래그 앤 드롭(또는 **`[▲ 위로]`** / **`[▼ 아래로]`** 버튼 클릭)하여 원하는 병합 순서로 정렬합니다.
4. 우측 하단의 **`[MERGE (병합하기)]`** 버튼을 클릭합니다.
5. 페이지 단위로 진행도가 표시되는 다이얼로그가 완료되면, 자동으로 열리는 **파일 저장 창**에서 저장할 위치와 파일명을 입력하고 저장합니다.

---

## 단일 실행 파일(`.exe`) 빌드 방법

직접 소스 코드로부터 `.exe` 파일을 빌드하려면 `PyInstaller`를 사용합니다.

```bash
pip install pyinstaller pypdf
python -m PyInstaller --noconsole --onefile --clean --name "PDF_Merger" pdf_merge.py
```

---

## 기술 스택 및 개발 환경

- **Language**: Python 3.14
- **GUI**: Tkinter / `ttk` (Windows Native Theme) + Win32 API (`ctypes` 기반 `WM_DROPFILES` 훅 및 High-DPI 설정)
- **PDF Engine**: [`pypdf`](https://pypi.org/project/pypdf/)
- **Packaging & Signing**: `PyInstaller`, Windows SDK `signtool.exe` (SHA-256 Authenticode)
- **AI Agent & Model**: **Antigravity** (**Gemini 4 Argon**)
