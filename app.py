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

# Supabase 클라이언트
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# 전역 변수 (토큰 캐싱)
CURRENT_TOKEN = None

def get_access_token():
    url = f"{BASE_URL}/oauth2/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {
        "grant_type": "client_credentials",
        "appkey": LS_APP_KEY,
        "appsecretkey": LS_APP_SECRET,
        "scope": "oob"
    }
    res = requests.post(url, headers=headers, data=data, timeout=10)
    if res.status_code == 200:
        return res.json()["access_token"]
    raise Exception(f"Token fetch failed: {res.text}")

def get_night_futures_price_safe(max_retries=3):
    global CURRENT_TOKEN
    
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
            
            # 1. 마스터 조회
            res = requests.post(f"{BASE_URL}/futureoption/market-data", 
                                headers=headers, 
                                json={"t8432InBlock": {"gubun": "0"}},
                                timeout=10)
            
            if res.status_code == 401 or "유효하지 않은 토큰" in res.text:
                print("🔄 토큰 만료! 재발급 시도...")
                CURRENT_TOKEN = get_access_token()
                continue
                
            master_list = res.json().get("t8432OutBlock", [])
            target = next((item for item in master_list 
                           if item["hname"].startswith("F ") and item["shcode"].startswith("A01")), None)
            
            if not target: return None

            # 2. 시세 조회
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
            if attempt == max_retries - 1:
                CURRENT_TOKEN = None
                return None

def cleanup_old_data(days=2):
    try:
        cutoff_date = datetime.now(pytz.utc) - timedelta(days=days)
        cutoff_str = cutoff_date.isoformat()
        
        # 로그가 너무 많이 쌓이지 않게 청소 때는 출력
        print(f"🧹 데이터 정리 시작 (기준: {cutoff_str} 이전 삭제)")

        supabase.table("market_night_futures") \
            .delete() \
            .lt("recorded_at", cutoff_str) \
            .execute()
            
        print(f"✅ 데이터 정리 완료")
        
    except Exception as e:
        print(f"⚠️ 데이터 정리 실패: {e}")

def run_monitor_forever():
    print(f"🚀 클라우드 서버 모니터링 시작 (무한 실행)")
    
    # 1. 시작하자마자 데이터 정리 한 번 수행
    cleanup_old_data(days=2)
    last_cleanup_time = time.time()
    
    # 💡 무한 루프 (duration 체크 없음)
    while True:
        try:
            # 2. 24시간마다 데이터 정리 수행 (86400초)
            if time.time() - last_cleanup_time > 86400:
                cleanup_old_data(days=2)
                last_cleanup_time = time.time()

            # 3. 데이터 수집
            market_data = get_night_futures_price_safe()
            
            if market_data:
                try:
                    supabase.table("market_night_futures").insert(market_data).execute()
                    # 클라우드 로그 용량을 위해 print는 최소화하거나 필요시에만 사용
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Saved: {market_data['price']}")
                except Exception as db_err:
                    print(f"🔥 DB 저장 실패: {db_err}")
            
            # 4. 1분 대기
            time.sleep(60)
            
        except KeyboardInterrupt:
            print("\n🛑 사용자에 의해 종료됨")
            break
        except Exception as e:
            print(f"💀 치명적 에러 (재시작 대기): {e}")
            time.sleep(60) # 에러나면 1분 쉬고 다시 시작

if __name__ == "__main__":
    run_monitor_forever()