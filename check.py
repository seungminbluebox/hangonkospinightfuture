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

def get_access_token():
    print("🔑 토큰 발급 중...")
    url = f"{BASE_URL}/oauth2/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {
        "grant_type": "client_credentials",
        "appkey": LS_APP_KEY,
        "appsecretkey": LS_APP_SECRET,
        "scope": "oob"
    }
    res = requests.post(url, headers=headers, data=data)
    if res.status_code == 200:
        return res.json()["access_token"]
    raise Exception(f"토큰 발급 실패: {res.text}")

def check_master_list():
    try:
        token = get_access_token()
        
        print("📡 지수선물마스터(t8432) 조회 중...")
        headers = {
            "content-type": "application/json; charset=UTF-8",
            "authorization": f"Bearer {token}",
            "tr_cd": "t8432",
            "tr_cont": "N",
            "tr_cont_key": "",
            "mac_address": "000000000000"
        }
        
        # t8432InBlock의 gubun: "0"은 전체 조회를 의미합니다.
        body = {
            "t8432InBlock": {
                "gubun": "0" 
            }
        }
        
        res = requests.post(f"{BASE_URL}/futureoption/market-data", headers=headers, json=body)
        data = res.json()
        
        # 결과 리스트 추출
        master_list = data.get("t8432OutBlock", [])
        
        if not master_list:
            print("❌ 데이터를 가져오지 못했습니다.")
            print(data)
            return

        print(f"✅ 총 {len(master_list)}개의 선물 종목을 찾았습니다.")

        # 1. 전체 데이터를 파일로 저장 (분석용)
        filename = "t8432_full_result.json"
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(master_list, f, indent=2, ensure_ascii=False)
        print(f"📁 전체 데이터가 '{filename}' 파일로 저장되었습니다.")

        # 2. 우리가 찾는 '코스피 200 선물'만 필터링해서 터미널에 출력
        print("\n🔎 [필터링 결과] 코스피 200 선물 (A01로 시작하는 종목):")
        targets = [
            item for item in master_list 
            if item["shcode"].startswith("A01") or item["shcode"].startswith("101")
        ]
        
        for item in targets:
            print(f"- 종목명: {item['hname']}, 코드: {item['shcode']}, 만기: {item.get('expcode', 'N/A')}")

    except Exception as e:
        print(f"❌ 에러 발생: {e}")

if __name__ == "__main__":
    check_master_list()