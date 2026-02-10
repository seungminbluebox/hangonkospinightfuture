import os
import time
import requests
import json
from datetime import datetime,timedelta
import pytz
from supabase import create_client, Client
from dotenv import load_dotenv
import sys
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

# .env 파일 로드
dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(dotenv_path)

# [설정] 환경 변수나 config.py에서 가져오기
LS_APP_KEY = os.getenv("LS_APP_KEY")
LS_APP_SECRET = os.getenv("LS_APP_SECRET")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

BASE_URL = "https://openapi.ls-sec.co.kr:8080"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# 전역 변수 (토큰 재사용)
CURRENT_TOKEN = None

# ------------------------------------------------------------------
# 🔑 1. 토큰 관리
# ------------------------------------------------------------------
def get_access_token():
    url = f"{BASE_URL}/oauth2/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {
        "grant_type": "client_credentials",
        "appkey": LS_APP_KEY,
        "appsecretkey": LS_APP_SECRET,
        "scope": "oob"
    }
    # 10초 타임아웃 설정
    res = requests.post(url, headers=headers, data=data, timeout=10)
    if res.status_code == 200:
        return res.json()["access_token"]
    raise Exception(f"토큰 발급 실패: {res.text}")

# ------------------------------------------------------------------
# ⏰ 2. 시간 및 청소 로직
# ------------------------------------------------------------------
def is_market_open():
    """지금이 야간선물 장 운영 시간(18:00 ~ 05:00)인지 체크"""
    kst = pytz.timezone('Asia/Seoul')
    now = datetime.now(kst)
    # 저녁 6시 이후 ~ 밤 12시 전 OR 새벽 0시 ~ 새벽 5시 전
    if now.hour >= 18 or now.hour < 5:
        return True
    return False

def cleanup_old_data(days=2):
    """오래된 데이터 삭제 (DB 용량 관리)"""
    try:
        cutoff_date = datetime.now(pytz.utc) - timedelta(days=days)
        cutoff_str = cutoff_date.isoformat()
        # 로그는 청소할 때만 출력
        print(f"🧹 데이터 정리 시작 ({days}일 이전 데이터 삭제)...")
        supabase.table("market_night_futures").delete().lt("recorded_at", cutoff_str).execute()
        print("✅ 데이터 정리 완료")
    except Exception as e:
        print(f"⚠️ 데이터 정리 실패: {e}")

# ------------------------------------------------------------------
# 📡 3. 핵심 데이터 수집 (Safe Mode)
# ------------------------------------------------------------------
def get_night_futures_price_safe(max_retries=3):
    global CURRENT_TOKEN
    
    # 토큰 없으면 발급
    if not CURRENT_TOKEN:
        CURRENT_TOKEN = get_access_token()

    for attempt in range(max_retries):
        try:
            headers = {
                "content-type": "application/json; charset=UTF-8",
                "authorization": f"Bearer {CURRENT_TOKEN}",
                "tr_cd": "t8432",
                "tr_cont": "N",
                "tr_cont_key": "",
                "mac_address": "000000000000"
            }
            
            # [Step 1] 마스터 조회 (종목 찾기)
            # 네가 테스트했던 그 로직 그대로 사용!
            res = requests.post(f"{BASE_URL}/futureoption/market-data", 
                                headers=headers, 
                                json={"t8432InBlock": {"gubun": "0"}},
                                timeout=10)
            
            # 토큰 만료 에러 체크 (401 Unauthorized 등)
            if res.status_code == 401 or "유효하지 않은 토큰" in res.text:
                print("🔄 토큰 만료! 재발급 시도...")
                CURRENT_TOKEN = get_access_token()
                continue # 재시도
                
            master_list = res.json().get("t8432OutBlock", [])
            
            # 💡 핵심 필터링 로직 (네가 검증한 로직 적용)
            # F로 시작하고(선물), A01이나 101로 시작하는(코스피200) 첫 번째 종목
            target = next((item for item in master_list 
                           if item["hname"].startswith("F ") and (item["shcode"].startswith("A01") or item["shcode"].startswith("101"))), None)
            
            if not target:
                print("❌ 코스피200 선물 종목을 찾을 수 없습니다.")
                return None

            # [Step 2] 시세 조회 (t8456 - 야간 전용)
            focode = target["shcode"]
            headers["tr_cd"] = "t8456"
            
            res_price = requests.post(f"{BASE_URL}/futureoption/market-data", 
                                      headers=headers, 
                                      json={"t8456InBlock": {"focode": focode}},
                                      timeout=10)
            
            data = res_price.json().get("t8456OutBlock")
            if data:
                return {
                    "symbol": target["hname"],
                    "price": float(data["price"]),
                    "change": float(data["change"]),
                    "diff": float(data["diff"]),
                    "volume": int(data["volume"])
                }
            return None

        except Exception as e:
            print(f"⚠️ API 호출 실패 ({attempt+1}/{max_retries}): {e}")
            time.sleep(2)
            # 마지막 시도 실패 시 토큰 초기화 (다음 루프 때 갱신 유도)
            if attempt == max_retries - 1:
                CURRENT_TOKEN = None
                return None

# ------------------------------------------------------------------
# 🚀 4. 메인 실행 루프 (무한 실행)
# ------------------------------------------------------------------
def run_monitor_forever():
    print("🚀 야간선물 트래커 가동 (18:00 ~ 05:00)")
    
    # 시작 시 청소 1회
    cleanup_old_data(days=2)
    last_cleanup_time = time.time()
    
    while True:
        try:
            # 1️⃣ 장 시간 체크
            if not is_market_open():
                kst = pytz.timezone('Asia/Seoul')
                now = datetime.now(kst)
                
                # [정밀 대기 로직] 17시 50분부터는 18시 정각을 기다림
                if now.hour == 17 and now.minute >= 50:
                    target_time = now.replace(hour=18, minute=0, second=0, microsecond=0)
                    sleep_seconds = (target_time - now).total_seconds()
                    
                    if sleep_seconds > 0:
                        print(f"⏱️ 개장 임박! {sleep_seconds:.1f}초 대기 후 시작합니다...")
                        time.sleep(sleep_seconds + 1)
                        continue # 바로 시작

                # 평소 대기 (30분마다 로그)
                if now.minute % 30 == 0:
                    print(f"😴 야간장이 아닙니다. (현재: {now.strftime('%H:%M')}) 대기 중...")
                
                time.sleep(60) # 1분 대기
                continue

            # 2️⃣ 정기 청소 (24시간마다)
            if time.time() - last_cleanup_time > 86400:
                cleanup_old_data(days=2)
                last_cleanup_time = time.time()

            # 3️⃣ 데이터 수집 및 저장
            market_data = get_night_futures_price_safe()
            
            if market_data:
                try:
                    supabase.table("market_night_futures").insert(market_data).execute()
                    
                    # 로그 출력 (한국 시간)
                    now_kst = datetime.now(pytz.timezone('Asia/Seoul')).strftime('%H:%M:%S')
                    print(f"[{now_kst}] {market_data['symbol']}: {market_data['price']}")
                    
                except Exception as db_err:
                    print(f"🔥 DB 저장 실패: {db_err}")
            
            # 4️⃣ 1분 대기
            time.sleep(60)
            
        except KeyboardInterrupt:
            print("\n🛑 사용자 중단")
            break
        except Exception as e:
            print(f"💀 알 수 없는 에러: {e}")
            time.sleep(60) # 에러 나도 죽지 않고 대기 후 재시작

if __name__ == "__main__":
    run_monitor_forever()