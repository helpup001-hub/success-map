"""
Google Drive 폴더를 스캔해서 index.html의 REGIONS 데이터를 자동 업데이트하는 스크립트.

폴더 구조 (어떤 계층이어도 OK):
  루트폴더 / ... / 지역폴더 / 지자체폴더 / 이미지파일
  - 지역폴더 이름이 REGION_KEY_MAP의 키와 일치하면 지역으로 인식
  - 지역폴더 바로 아래 폴더는 지자체로 인식
  - 연도폴더 등 인식 안 되는 폴더는 그냥 통과

index.html 안의 /* DRIVE_AUTO_START */ ~ /* DRIVE_AUTO_END */ 블록을 교체.
기존 수동 항목(cdn.imweb.me 등)은 절대 건드리지 않음.
"""

import os
import json
import re
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ── 설정 ──────────────────────────────────────────────────────────────────────

ROOT_FOLDER_ID = "1Gbve23pAgAxQdznqYP_chtxLfMcVoVpd"
HTML_FILE = "index.html"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

# 지역 폴더명 → REGIONS key 매핑 (전체 이름 + 약칭 모두 지원)
REGION_KEY_MAP = {
    # 전체 이름
    "경기도":        "gyeonggi",
    "강원도":        "gangwon",
    "충청북도":      "chungbuk",
    "충청남도":      "chungnam",
    "전라북도":      "jeonbuk",
    "전라남도":      "jeonnam",
    "경상북도":      "gyeongbuk",
    "경상남도":      "gyeongnam",
    "제주특별자치도": "jeju",
    "부산광역시":    "busan",
    "대구광역시":    "daegu",
    "인천광역시":    "incheon",
    "광주광역시":    "gwangju",
    "대전광역시":    "daejeon",
    "울산광역시":    "ulsan",
    "세종특별자치시": "sejong",
    "서울특별시":    "seoul",
    # 약칭
    "경기":  "gyeonggi",
    "강원":  "gangwon",
    "충북":  "chungbuk",
    "충남":  "chungnam",
    "전북":  "jeonbuk",
    "전남":  "jeonnam",
    "경북":  "gyeongbuk",
    "경남":  "gyeongnam",
    "제주":  "jeju",
    "부산":  "busan",
    "대구":  "daegu",
    "인천":  "incheon",
    "광주":  "gwangju",
    "대전":  "daejeon",
    "울산":  "ulsan",
    "세종":  "sejong",
    "서울":  "seoul",
}

# ── Drive API 초기화 ───────────────────────────────────────────────────────────

def get_drive_service():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if not creds_json:
        raise ValueError("GOOGLE_CREDENTIALS 환경변수가 없습니다.")
    creds_info = json.loads(creds_json)
    creds = service_account.Credentials.from_service_account_info(
        creds_info,
        scopes=["https://www.googleapis.com/auth/drive.readonly"]
    )
    return build("drive", "v3", credentials=creds)

# 지자체 폴더명 정규화 (시/군/구 제거, 괄호 내용 제거 등)
def normalize_area(name):
    """Drive 폴더명 → index.html 탭 이름으로 정규화"""
    # 괄호 및 내용 제거: "강원 기타(양양,삼척,동해)" → "강원 기타" → "기타"
    name = re.sub(r'\([^)]*\)', '', name).strip()
    # 앞에 지역명 접두어 제거: "강원 기타" → "기타"
    for region in ["강원", "경기", "충남", "충북", "전남", "전북", "경남", "경북", "제주", "서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종"]:
        if name.startswith(region + " "):
            name = name[len(region)+1:].strip()
    # 시/군/구 접미어 제거: "강릉시" → "강릉", "영광군" → "영광"
    if len(name) > 2 and name[-1] in ("시", "군", "구"):
        name = name[:-1]
    return name

# ── Drive 유틸 ────────────────────────────────────────────────────────────────

def list_children(service, folder_id):
    results, page_token = [], None
    while True:
        resp = service.files().list(
            q=f"'{folder_id}' in parents and trashed = false",
            fields="nextPageToken, files(id, name, mimeType, createdTime)",
            orderBy="createdTime",
            pageToken=page_token,
            pageSize=1000,
        ).execute()
        results.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return results

def is_folder(item):
    return item["mimeType"] == "application/vnd.google-apps.folder"

def is_image(item):
    return os.path.splitext(item["name"])[1].lower() in IMAGE_EXTENSIONS

def thumbnail_url(file_id):
    return f"https://drive.google.com/thumbnail?id={file_id}&sz=w1000"

# ── 드라이브 스캔 ─────────────────────────────────────────────────────────────

def scan_drive(service):
    """루트 폴더를 재귀 탐색 → {region_key: [{url, area}]} 반환"""
    drive_cases = {}

    def walk(folder_id, region_key=None, area=None):
        for item in list_children(service, folder_id):
            if is_image(item):
                if region_key:
                    drive_cases.setdefault(region_key, []).append({
                        "url":  thumbnail_url(item["id"]),
                        "area": area or "",
                    })
            elif is_folder(item):
                name = item["name"]
                if name in REGION_KEY_MAP:
                    # 지역 폴더
                    walk(item["id"], region_key=REGION_KEY_MAP[name], area=None)
                elif region_key is not None and area is None:
                    # 지자체 폴더 (지역 바로 아래) — 이름 정규화
                    walk(item["id"], region_key=region_key, area=normalize_area(name))
                else:
                    # 연도 폴더 등 — 통과
                    walk(item["id"], region_key=region_key, area=area)

    walk(ROOT_FOLDER_ID)
    return drive_cases

# ── HTML 패치 ─────────────────────────────────────────────────────────────────

START = "/* DRIVE_AUTO_START:{key} */"
END   = "/* DRIVE_AUTO_END:{key} */"

def build_block(region_key, cases):
    """드라이브 이미지 블록 문자열 생성"""
    if not cases:
        return f"        {START.format(key=region_key)}\n        {END.format(key=region_key)}"
    lines = [f"        {START.format(key=region_key)}"]
    prev_area = None
    for c in cases:
        area = c.get("area", "")
        if area and area != prev_area:
            lines.append(f"        /* {area} */")
            prev_area = area
        area_js = f', area: "{area}"' if area else ""
        lines.append(f'        {{ url: "{c["url"]}"{area_js} }},')
    lines.append(f"        {END.format(key=region_key)}")
    return "\n".join(lines)

def patch_html(html, drive_cases):
    """
    기존 DRIVE_AUTO 블록이 있으면 교체, 없으면 각 region의 cases 배열 끝에 삽입.
    수동 항목(cdn.imweb.me 등)은 건드리지 않음.
    """
    for region_key, cases in drive_cases.items():
        start_tag = START.format(key=region_key)
        end_tag   = END.format(key=region_key)
        new_block = build_block(region_key, cases)

        if start_tag in html:
            # 기존 블록 교체
            pattern = re.escape(start_tag) + r".*?" + re.escape(end_tag)
            html = re.sub(pattern, new_block, html, flags=re.DOTALL)
        else:
            # 최초 삽입: 해당 region key의 cases 배열 닫히는 ] 직전에 추가
            pattern = (
                r'(key:\s*"' + re.escape(region_key) + r'"'
                r'.*?cases:\s*\[)(.*?)(\n\s*\]\s*\})'
            )
            def inserter(m, blk=new_block):
                inner = m.group(2).rstrip()
                # 마지막 항목에 쉼표가 없으면 추가
                if inner and not inner.rstrip().endswith(","):
                    inner = inner.rstrip() + ","
                return m.group(1) + inner + "\n" + blk + "\n      " + m.group(3).lstrip()
            html = re.sub(pattern, inserter, html, flags=re.DOTALL)

    return html

# ── 메인 ──────────────────────────────────────────────────────────────────────

def main():
    print("🔍 Google Drive 스캔 중...")
    service = get_drive_service()
    drive_cases = scan_drive(service)

    for key, cases in drive_cases.items():
        print(f"  {key}: {len(cases)}개 이미지")

    print(f"\n📝 {HTML_FILE} 업데이트 중...")
    with open(HTML_FILE, "r", encoding="utf-8") as f:
        html = f.read()

    # index.html에 존재하는 DRIVE_AUTO 마커 중 Drive에서 0개인 지역도 빈 블록으로 교체
    for key in REGION_KEY_MAP.values():
        if key not in drive_cases:
            start_tag = START.format(key=key)
            if start_tag in html:
                drive_cases[key] = []
                print(f"  {key}: 0개 이미지 (블록 비움)")

    html = patch_html(html, drive_cases)

    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print("✅ 완료!")

if __name__ == "__main__":
    main()
