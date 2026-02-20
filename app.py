import os
import time
import requests
import json
from datetime import datetime, timedelta
import pytz
from supabase import create_client, Client
from dotenv import load_dotenv
import sys
from revalidate import revalidate_path

# 1. 환경변수 및 기본 설정
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(dotenv_path)

LS_APP_KEY = os.getenv("LS_APP_KEY")
LS_APP_SECRET = os.getenv("LS_APP_SECRET")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
BASE_URL = "https://openapi.ls-sec.co.kr:8080"

# Supabase 연결
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
    res = requests.post(url, headers=headers, data=data, timeout=10)
    if res.status_code == 200:
        return res.json()["access_token"]
    raise Exception(f"Token fetch failed: {res.text}")

# ------------------------------------------------------------------
# ⏰ 2. 시간 및 청소 로직 (수정됨)
# ------------------------------------------------------------------
def is_market_open():
    """지금이 야간선물 장 운영 시간(18:00 ~ 06:00)인지 체크 및 주말/휴일 제외"""
    kst = pytz.timezone('Asia/Seoul')
    now = datetime.now(kst)
    wd = now.weekday()  # 월:0, 화:1, ..., 금:4, 토:5, 일:6
    hr = now.hour
    mn = now.minute

    # 1. 주말 차단 (토요일 06:01 ~ 월요일 17:59)
    # 토요일 아침 6시 이후
    if wd == 5 and (hr > 6 or (hr == 6 and mn > 0)):
        return False
    # 일요일 전체
    if wd == 6:
        return False
    # 월요일 오후 6시 이전
    if wd == 0 and hr < 18:
        return False

    # 2. 운영 시간 체크 (18:00 ~ 06:00)
    # [수정됨] 새벽 06:00:59까지 허용하여 6시 정각 데이터를 수집하도록 함
    if hr >= 18 or hr < 6 or (hr == 6 and mn == 0):
        return True
        
    return False

def manage_data_limit(limit=1440):
    """
    [수정됨] 날짜 기준이 아니라 '개수' 기준으로 삭제
    - 최신 데이터 limit(1440)개만 남기고 나머지는 삭제
    - 주말/휴일에도 데이터가 사라지지 않도록 보호
    """
    try:
        # 1. limit번째(1441번째) 레코드의 시간 찾기 (내림차순 정렬)
        # range(start, end)는 0부터 시작하므로 range(1440, 1440)은 1441번째 데이터를 의미함
        res = supabase.table("market_night_futures") \
            .select("recorded_at") \
            .order("recorded_at", desc=True) \
            .range(limit, limit) \
            .execute()
        
        # 1441번째 데이터가 존재한다면 (즉, 데이터가 1440개를 초과했다면)
        if res.data and len(res.data) > 0:
            cutoff_time = res.data[0]['recorded_at']
            print(f"🧹 데이터 정리 시작 (최신 {limit}개 유지, 기준: {cutoff_time} 및 이전 삭제)...")
            
            # 2. 해당 시간보다 작거나 같은(lte) 데이터 삭제 (= 오래된 데이터 삭제)
            supabase.table("market_night_futures") \
                .delete() \
                .lte("recorded_at", cutoff_time) \
                .execute()
                
            print("✅ 데이터 정리 완료")
        else:
            # 데이터가 아직 1440개 안됨
            pass
            
    except Exception as e:
        print(f"⚠️ 데이터 정리 실패: {e}")

# ------------------------------------------------------------------
# 📡 3. 핵심 데이터 수집 (Safe Mode)
# ------------------------------------------------------------------
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
            
            # [Step 1] 마스터 조회
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
                           if item["hname"].startswith("F ") and (item["shcode"].startswith("A01") or item["shcode"].startswith("101"))), None)
            
            if not target: return None

            # [Step 2] 시세 조회
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

# ------------------------------------------------------------------
# 🚀 4. 메인 실행 루프 (정각 보정 적용)
# ------------------------------------------------------------------
def run_monitor_forever():
    print("🚀 야간선물 트래커 가동 (18:00 ~ 06:00) - 정각 보정 & 개수 유지 모드")
    
    # 시작 시 데이터 개수 정리 1회 수행
    manage_data_limit(limit=1440)
    last_cleanup_time = time.time()
    
    # 휴장 세션 여부 (거래량 0일 때 해당 세션 종료 시까지 True 유지)
    is_holiday_session = False
    
    while True:
        try:
            # 1️⃣ 장 시간 체크 (주말 포함)
            if not is_market_open():
                # 장이 닫히면 휴장 플래그 초기화 (다음 세션을 위해)
                if is_holiday_session:
                    print("🌙 세션 종료. 휴장 플래그를 초기화합니다.")
                    is_holiday_session = False
                
                kst = pytz.timezone('Asia/Seoul')
                now = datetime.now(kst)
                
                # [개장 임박] 17:50분부터는 18:00:01까지 정확히 대기
                if now.hour == 17 and now.minute >= 50:
                    target_time = now.replace(hour=18, minute=0, second=30, microsecond=0)
                    sleep_seconds = (target_time - now).total_seconds()
                    
                    if sleep_seconds > 0:
                        print(f"⏱️ 개장 임박! {sleep_seconds:.1f}초 대기 후 시작합니다...")
                        time.sleep(sleep_seconds)
                        continue 

                # [평소 대기] 30분마다 로그 출력
                if now.minute % 30 == 0 and now.second < 2:
                    print(f"😴 야간장이 아닙니다. (현재: {now.strftime('%H:%M')}) 대기 중...")
                
                # 다음 분 00초까지 대기
                sleep_to_next_minute = 60 - now.second
                time.sleep(sleep_to_next_minute)
                continue

            # 2️⃣ 정기 데이터 정리 (1시간마다 수행)
            if time.time() - last_cleanup_time > 3600:
                manage_data_limit(limit=1440)
                last_cleanup_time = time.time()

            # 2.5️⃣ 휴장 상태 체크
            if is_holiday_session:
                # 이미 거래량 0으로 확인된 세션이면 수집 없이 대기
                now = datetime.now()
                sleep_to_next_minute = 60 - now.second
                time.sleep(max(0, sleep_to_next_minute))
                continue

            # 3️⃣ 데이터 수집 및 저장
            market_data = get_night_futures_price_safe()
            
            if market_data:
                # [핵심] 휴장 감지: 거래량이 0이면 수집 중단
                if market_data['volume'] == 0:
                    now_kst = datetime.now(pytz.timezone('Asia/Seoul')).strftime('%H:%M:%S')
                    print(f"[{now_kst}] ⚠️ 거래량이 0입니다. 오늘 야간선물 휴장으로 판단하고 이번 세션 수집을 중단합니다.")
                    is_holiday_session = True
                    continue

                try:
                    supabase.table("market_night_futures").insert(market_data).execute()
                    
                    # On-Demand Revalidation
                    revalidate_path("/kospi-night-futures")
                    
                    # 로그 출력 (한국 시간)
                    now_kst = datetime.now(pytz.timezone('Asia/Seoul')).strftime('%H:%M:%S')
                    print(f"[{now_kst}] {market_data['symbol']}: {market_data['price']} (Vol: {market_data['volume']})")
                    
                except Exception as db_err:
                    print(f"🔥 DB 저장 실패: {db_err}")
            
            # 4️⃣ [핵심] 다음 실행 시간 보정 (Drift 방지)
            now = datetime.now()
            target_next_run = (now + timedelta(minutes=1)).replace(second=1, microsecond=0)
            
            sleep_seconds = (target_next_run - now).total_seconds()
            
            if sleep_seconds < 0:
                sleep_seconds = 0
            
            time.sleep(sleep_seconds)
            
        except KeyboardInterrupt:
            print("\n🛑 사용자 중단")
            break
        except Exception as e:
            print(f"💀 알 수 없는 에러: {e}")
            time.sleep(60)
            
        except KeyboardInterrupt:
            print("\n🛑 사용자 중단")
            break
        except Exception as e:
            print(f"💀 알 수 없는 에러: {e}")
            time.sleep(60)

if __name__ == "__main__":
    run_monitor_forever()