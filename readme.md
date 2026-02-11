# 🚀 Hang on! Kospi Night Future Tracker

LS증권 API를 활용하여 코스피 200 야간 선물 시세를 실시간으로 수집하고 Supabase DB에 저장하는 24시간 자동화 시스템입니다.

## 1. GCP VM 인스턴스 최적 설정 (생성 단계)

인스턴스를 처음 생성할 때 아래 옵션으로 구성하는 것을 권장합니다.

| 항목             | 권장 설정값                    | 비고                                 |
| ---------------- | ------------------------------ | ------------------------------------ |
| **리전(Region)** | `asia-northeast3 (Seoul)`      | API 지연 시간 최소화                 |
| **머신 시리즈**  | `E2`                           | 비용 효율적 선택                     |
| **머신 유형**    | `e2-small` (2 vCPU, 2GB RAM)   | 안정적인 24시간 가동을 위해 2GB 추천 |
| **운영체제(OS)** | `Ubuntu 24.04 LTS`             | 최신 파이썬 환경 지원                |
| **부팅 디스크**  | `20GB` (균형 있는 영구 디스크) | 기본 10GB보다 여유로운 용량          |
| **방화벽**       | `HTTP/HTTPS 트래픽 허용`       | 향후 대시보드 확장 대비              |

## 🔑 부록: GitHub 연결을 위한 SSH 키 설정

서버(GCP VM)가 깃허브 저장소에 접근할 수 있도록 권한을 부여하는 과정이다-

### 1. 서버에서 SSH 키 생성

터미널에 아래 명령어를 입력한다- (이메일 주소만 본인 것으로 바꾼다-)

```bash
ssh-keygen -t ed25519 -C "your_email@example.com"

```

- **Enter file in which to save the key**: 그냥 **Enter**를 누른다-
- **Enter passphrase**: 비밀번호 없이 쓰려면 그냥 **Enter**를 두 번 누른다-

### 2. 생성된 공개키(Public Key) 확인 및 복사

아래 명령어를 입력해 출력되는 긴 문자열을 전부 복사한다-

```bash
cat ~/.ssh/id_ed25519.pub

```

- `ssh-ed25519`로 시작해서 본인 이메일로 끝나는 한 줄이다-

### 3. GitHub 저장소에 등록

1. 본인의 **GitHub Repository** 페이지로 이동한다-
2. **Settings** 탭 -> 왼쪽 메뉴의 **Deploy keys**를 클릭한다-
3. **Add deploy key** 버튼을 누른다-
4. **Title**: `GCP-Kospi-Night` (구분하기 쉬운 이름)
5. **Key**: 방금 서버에서 복사한 내용을 붙여넣는다-
6. **Add key** 버튼을 눌러 저장한다-

## 2. 서버 초기화 및 환경 구축

인스턴스 생성 후 SSH로 접속하여 아래 명령어를 순서대로 실행합니다.

### 시스템 기본 도구 설치

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install git python3-pip python3-venv nodejs npm -y
sudo npm install pm2 -g

```

### 프로젝트 클론 및 가상 환경 설정

```bash
# 1. 저장소 가져오기
git clone [본인의_GitHub_저장소_주소] hangon_kospinight
cd hangon_kospinight

# 2. 가상 환경 생성 및 활성화
python3 -m venv venv
source venv/bin/activate

# 3. 필수 라이브러리 설치
pip install requests supabase python-dotenv pytz

```

### 환경 변수(.env) 설정

```bash
nano .env

```

아래 내용을 입력합니다:

```text
LS_APP_KEY=본인의_LS증권_앱키
LS_APP_SECRET=본인의_LS증권_비밀키
SUPABASE_URL=본인의_수파베이스_URL
SUPABASE_KEY=본인의_수파베이스_키

```

---

## 3. 서비스 실행 및 자동 재시작 (PM2)

터미널을 닫아도 프로그램이 멈추지 않도록 설정합니다.

```bash
# 서비스 시작
pm2 start app.py --interpreter ./venv/bin/python3 --name kospi-night

# 서버 재부팅 시 자동 실행 설정
pm2 startup
# (출력되는 sudo 명령어를 복사하여 실행)
pm2 save

```

---

## 4. 로그 모니터링 및 업데이트

### 실시간 로그 확인

트래커가 정상적으로 시세를 수집하는지 확인합니다.

```bash
pm2 logs kospi-night

```

### 코드 업데이트 반영

로컬에서 수정된 코드를 서버에 적용하는 방법입니다.

```bash
# 2. 프로젝트 폴더 이동
cd hangon_kospinight/
# 3. 가상 환경 활성화
source venv/bin/activate
git pull origin main
pm2 restart kospi-night

```

### 데이터 관리 루틴

- **수집 주기**: 1분마다 1회 수집 및 저장
- **작동 시간**: 한국 시간 기준 18:00 ~ 익일 05:00
- **자동 정리**: 24시간마다 2일 이상 된 과거 데이터 자동 삭제

---
