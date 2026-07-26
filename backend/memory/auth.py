"""
Authentication and authorization module

Background: AsterMem is a single-user self-hosted service that only needs one admin account.
On first launch (or after the user manually deletes data/memories.db), default credentials
are created automatically.
Design intent: Default credentials are fixed as admin / admin; users should change them
after first login. For LAN-only usage, login protection can be disabled in the admin page
to allow unauthenticated access.
Key constraints:
  - Changing username or password requires verifying the current password
    (unified entry point: update_credentials)
  - Login protection toggle is stored at config["auth"]["login_required"]; this module
    only modifies the in-memory config — persistence to config.yaml is handled by the
    API layer (this module is unaware of config file paths)

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

import os
import secrets
import hashlib
from datetime import datetime, timedelta
from typing import Optional, List, Dict
import json

from .database import Database

DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin"
MIN_USERNAME_LENGTH = 2
MIN_PASSWORD_LENGTH = 4
API_TOKEN_SCOPES = ("read", "write", "config", "admin", "destructive")
DEFAULT_API_TOKEN_SCOPES = ("read", "write", "config")


class AuthError(Exception):
    """Credential verification failed: status determines the HTTP status code returned by the API layer"""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class AuthManager:
    """Authentication manager"""
    
    def __init__(self, database: Database, config: dict):
        self.database = database
        self.config = config
        self._init_auth_tables()
        self._ensure_default_admin()
    
    def _init_auth_tables(self):
        """Initialize authentication-related tables"""
        with self.database.get_connection() as conn:
            cursor = conn.cursor()
            
            # Admin accounts table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS admins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TEXT,
                    last_login TEXT
                )
            """)
            
            # API Token table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS api_tokens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    token TEXT UNIQUE NOT NULL,
                    created_at TEXT,
                    last_used TEXT,
                    is_active INTEGER DEFAULT 1,
                    scopes TEXT NOT NULL DEFAULT '["read","write","config"]'
                )
            """)
            cursor.execute("PRAGMA table_info(api_tokens)")
            if "scopes" not in {row[1] for row in cursor.fetchall()}:
                cursor.execute(
                    """ALTER TABLE api_tokens ADD COLUMN scopes TEXT
                       NOT NULL DEFAULT '["read","write","config"]'"""
                )
            
            # Sessions table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT UNIQUE NOT NULL,
                    admin_id INTEGER NOT NULL,
                    created_at TEXT,
                    expires_at TEXT,
                    FOREIGN KEY (admin_id) REFERENCES admins(id)
                )
            """)
    
    def _ensure_default_admin(self):
        """Ensure a default admin exists (default credentials: admin / admin, user should change after login)"""
        with self.database.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM admins")
            count = cursor.fetchone()[0]

            if count == 0:
                # Config can override default password (for testing and automated deployment), otherwise use fixed admin
                default_password = self.config.get("auth", {}).get("default_password") or DEFAULT_PASSWORD
                password_hash = self._hash_password(default_password)
                cursor.execute("""
                    INSERT INTO admins (username, password_hash, created_at)
                    VALUES (?, ?, ?)
                """, (DEFAULT_USERNAME, password_hash, datetime.now().isoformat()))

                if default_password == DEFAULT_PASSWORD:
                    print("\n🔐 Default admin account created:")
                    print(f"   Username: {DEFAULT_USERNAME}")
                    print(f"   Password: {DEFAULT_PASSWORD}")
                    print("   Please change your username and password after logging in!\n")
    
    def _hash_password(self, password: str) -> str:
        """Password hashing"""
        salt = self.config.get("auth", {}).get("salt", "xs_memory_salt")
        return hashlib.sha256(f"{password}{salt}".encode()).hexdigest()
    
    # ==================== Admin Authentication ====================
    
    def verify_admin(self, username: str, password: str) -> Optional[int]:
        """Verify admin login"""
        password_hash = self._hash_password(password)
        
        with self.database.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id FROM admins 
                WHERE username = ? AND password_hash = ?
            """, (username, password_hash))
            row = cursor.fetchone()
            
            if row:
                # Update last login time
                cursor.execute("""
                    UPDATE admins SET last_login = ? WHERE id = ?
                """, (datetime.now().isoformat(), row[0]))
                return row[0]
            
            return None
    
    def create_session(self, admin_id: int, expires_hours: int = 24) -> str:
        """Create a session"""
        session_id = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(hours=expires_hours)
        
        with self.database.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO sessions (session_id, admin_id, created_at, expires_at)
                VALUES (?, ?, ?, ?)
            """, (session_id, admin_id, datetime.now().isoformat(), expires_at.isoformat()))
        
        return session_id
    
    def verify_session(self, session_id: str) -> Optional[int]:
        """Verify a session"""
        with self.database.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT admin_id, expires_at FROM sessions 
                WHERE session_id = ?
            """, (session_id,))
            row = cursor.fetchone()
            
            if row:
                expires_at = datetime.fromisoformat(row[1])
                if expires_at > datetime.now():
                    return row[0]
                else:
                    # Expired, delete the session
                    cursor.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            
            return None
    
    def delete_session(self, session_id: str):
        """Delete a session (logout)"""
        with self.database.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
    
    def get_primary_admin_id(self) -> Optional[int]:
        """Get the sole admin's id (used as identity substitute when login protection is disabled)"""
        with self.database.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM admins ORDER BY id LIMIT 1")
            row = cursor.fetchone()
            return row[0] if row else None

    def get_admin(self, admin_id: Optional[int] = None) -> Optional[Dict]:
        """
        Retrieve admin information. When admin_id is omitted, returns the sole admin.
        is_default_credentials indicates whether the user is still using admin / admin.
        """
        with self.database.get_connection() as conn:
            cursor = conn.cursor()
            if admin_id is None:
                cursor.execute("SELECT id, username, password_hash, last_login FROM admins ORDER BY id LIMIT 1")
            else:
                cursor.execute("SELECT id, username, password_hash, last_login FROM admins WHERE id = ?", (admin_id,))
            row = cursor.fetchone()

        if not row:
            return None

        return {
            "id": row[0],
            "username": row[1],
            "last_login": row[3],
            "is_default_credentials": (
                row[1] == DEFAULT_USERNAME and row[2] == self._hash_password(DEFAULT_PASSWORD)
            ),
        }

    def verify_password(self, admin_id: int, password: str) -> bool:
        """Verify an admin's current password (secondary confirmation before changing credentials or toggling login protection)"""
        with self.database.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT password_hash FROM admins WHERE id = ?", (admin_id,))
            row = cursor.fetchone()

        return bool(row) and row[0] == self._hash_password(password)

    def update_credentials(self, admin_id: int, current_password: str,
                           username: Optional[str] = None,
                           new_password: Optional[str] = None) -> Dict:
        """
        Update username / password. Either can be changed independently, but both
        require verifying the current password first.
        Raises AuthError on verification failure, mapped to 4xx by the API layer.
        """
        if not self.verify_password(admin_id, current_password):
            raise AuthError("Current password is incorrect", status=401)

        updates = []
        params: List[str] = []

        if username is not None:
            username = username.strip()
            if len(username) < MIN_USERNAME_LENGTH or " " in username:
                raise AuthError(f"Username must be at least {MIN_USERNAME_LENGTH} characters and cannot contain spaces")
            updates.append("username = ?")
            params.append(username)

        if new_password:
            if len(new_password) < MIN_PASSWORD_LENGTH:
                raise AuthError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
            updates.append("password_hash = ?")
            params.append(self._hash_password(new_password))

        if not updates:
            raise AuthError("Nothing to update")

        with self.database.get_connection() as conn:
            cursor = conn.cursor()
            if username is not None:
                cursor.execute("SELECT id FROM admins WHERE username = ? AND id != ?", (username, admin_id))
                if cursor.fetchone():
                    raise AuthError("Username is already taken", status=409)
            cursor.execute(f"UPDATE admins SET {', '.join(updates)} WHERE id = ?", (*params, admin_id))
            if cursor.rowcount == 0:
                raise AuthError("Admin does not exist", status=404)

        return self.get_admin(admin_id)

    def reset_to_default(self) -> Dict:
        """
        Reset admin to default credentials admin / admin and clear all sessions.
        Offline escape hatch for forgotten passwords (`python server.py --reset-admin`):
        can only be run on the local machine with read/write access to data/, not exposed via HTTP.
        """
        with self.database.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM admins ORDER BY id LIMIT 1")
            row = cursor.fetchone()
            if row:
                cursor.execute("UPDATE admins SET username = ?, password_hash = ? WHERE id = ?",
                               (DEFAULT_USERNAME, self._hash_password(DEFAULT_PASSWORD), row[0]))
            cursor.execute("DELETE FROM sessions")

        if not row:
            self._ensure_default_admin()

        return self.get_admin()

    # ==================== Login Protection Toggle ====================

    def is_login_required(self) -> bool:
        """Whether login protection is enabled (on by default; when off, all pages/REST endpoints are accessible without login)"""
        return bool((self.config.get("auth") or {}).get("login_required", True))

    def set_login_required(self, enabled: bool) -> None:
        """
        Toggle login protection. Only modifies the in-memory config; persistence to
        config.yaml is handled by the API layer.
        When disabling, all sessions are cleared to prevent stale cookies from remaining
        valid if protection is re-enabled later.
        """
        self.config.setdefault("auth", {})["login_required"] = bool(enabled)
        if not enabled:
            with self.database.get_connection() as conn:
                conn.cursor().execute("DELETE FROM sessions")
    
    # ==================== API Token Management ====================
    
    def create_api_token(self, name: str, scopes: Optional[List[str]] = None) -> str:
        """Create an API Token"""
        token = f"ast_{secrets.token_urlsafe(32)}"
        normalized_scopes = list(dict.fromkeys(scopes or DEFAULT_API_TOKEN_SCOPES))
        invalid = [scope for scope in normalized_scopes if scope not in API_TOKEN_SCOPES]
        if invalid:
            raise AuthError(f"Unknown token scopes: {', '.join(invalid)}")
        
        with self.database.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO api_tokens (name, token, created_at, is_active, scopes)
                VALUES (?, ?, ?, 1, ?)
            """, (name, token, datetime.now().isoformat(), json.dumps(normalized_scopes)))
        
        return token
    
    def verify_api_token(self, token: str) -> Optional[Dict]:
        """Verify an API Token and return token info for permission checks."""
        with self.database.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, name, scopes FROM api_tokens
                WHERE token = ? AND is_active = 1
            """, (token,))
            row = cursor.fetchone()
            
            if row:
                # Update last used time
                cursor.execute("""
                    UPDATE api_tokens SET last_used = ? WHERE id = ?
                """, (datetime.now().isoformat(), row[0]))
                try:
                    scopes = json.loads(row[2] or "[]")
                except (TypeError, json.JSONDecodeError):
                    scopes = list(DEFAULT_API_TOKEN_SCOPES)
                return {"id": row[0], "name": row[1], "scopes": scopes}
            
            return None
    
    def list_api_tokens(self) -> List[Dict]:
        """List all API Tokens"""
        with self.database.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, name, token, created_at, last_used, is_active, scopes
                FROM api_tokens ORDER BY created_at DESC
            """)
            rows = cursor.fetchall()
            
            return [{
                "id": row[0],
                "name": row[1],
                "prefix": row[2][:12] + "..." if row[2] else "",
                "created_at": row[3],
                "last_used_at": row[4],
                "revoked": not bool(row[5]),
                "scopes": json.loads(row[6] or "[]"),
            } for row in rows]
    
    def get_api_token_value(self, token_id: int) -> Optional[str]:
        """
        Retrieve the full value of a token. The list endpoint only returns prefixes,
        but handing tokens to AI requires the full credential. Only available to
        authenticated admin callers; revoked tokens are not returned.
        """
        with self.database.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT token FROM api_tokens WHERE id = ? AND is_active = 1
            """, (token_id,))
            row = cursor.fetchone()
            return row[0] if row else None

    def revoke_api_token(self, token_id: int) -> bool:
        """Revoke an API Token"""
        with self.database.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE api_tokens SET is_active = 0 WHERE id = ?
            """, (token_id,))
            return cursor.rowcount > 0
    
    def delete_api_token(self, token_id: int) -> bool:
        """Delete an API Token"""
        with self.database.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM api_tokens WHERE id = ?", (token_id,))
            return cursor.rowcount > 0


# ==================== Sample Data ====================

SAMPLE_MEMORIES_EN = [
    {
        "title": "About me",
        "content": """## Personal info

My name is **Alex**. I am 28 and work as a software engineer.

### Basics
- Birthday: March 15
- Location: San Francisco
- Languages: English, some Spanish

### Personality
- Introverted but talkative with close friends
- Detail-oriented, a bit of a perfectionist
- Enjoy working alone with music on

### Motto
> Ship it, then iterate.
""",
        "tags": ["personal", "about-me"],
        "priority": 10
    },
    {
        "title": "Hobbies and interests",
        "content": """## What I enjoy

### Activities
- Running (5 km every morning)
- Reading (sci-fi, design, non-fiction)
- Cooking (Italian food mostly)
- Photography (street and landscape)

### Favorite food
- Coffee: flat white, no sugar
- Cuisine: ramen, tacos, sourdough pizza
- Snack: dark chocolate

### Music
- Genres: indie, lo-fi, jazz
- Work playlist: ambient or white noise
""",
        "tags": ["hobbies", "interests", "lifestyle"],
        "priority": 9
    },
    {
        "title": "People at work",
        "content": """## Work contacts

### Manager
- **Sarah Chen** — Engineering Manager
  - Prefers async updates over meetings
  - Cares about test coverage

### Team
- **Dan** — Backend engineer
  - Very reliable, great at debugging
  - Loves board games

- **Priya** — Product designer
  - Sharp eye for detail
  - Best reached on Slack, not email

- **Tom** — Frontend engineer
  - Fast coder, sometimes skips docs
  - Buys the team donuts on Fridays
""",
        "tags": ["work", "colleagues", "people"],
        "priority": 8
    },
    {
        "title": "Family and friends",
        "content": """## Important people

### Family
- **Mom**: retired teacher, call her every Sunday
- **Dad**: engineer, loves hiking, birthday June 10
- **Sister (Emma)**: med school, always busy

### Close friends
- **Jake** — college roommate, lives in NYC
  - Monthly video call
  - Go-to person for advice

- **Mia** — met at a hackathon
  - Works at a startup
  - We swap book recommendations

### Key dates
- Mom's birthday: September 2
- Dad's birthday: June 10
- Emma's birthday: December 5
""",
        "tags": ["family", "friends", "people"],
        "priority": 9
    },
    {
        "title": "Daily routine",
        "content": """## My typical day

### Weekday schedule
- 6:30 — Wake up, run
- 7:30 — Shower, breakfast
- 8:30 — Start work
- 12:00 — Lunch
- 12:30 — Short walk
- 18:00 — Wrap up work
- 19:00 — Cook dinner
- 22:00 — Read
- 23:00 — Sleep

### Weekly rituals
- **Monday**: team standup at 10 AM
- **Wednesday**: gym after work
- **Friday**: call parents
- **Weekend**: side projects, hiking, or lazy morning

### Habits to maintain
- Drink enough water (easy to forget)
- Stretch every hour
- No screens after 10 PM
""",
        "tags": ["routine", "habits", "daily"],
        "priority": 7
    },
    {
        "title": "Current to-do list",
        "content": """## To-do

### Work
- [ ] Finish API refactor
- [ ] Write design doc for auth flow
- [ ] Review Dan's pull request

### Life
- [ ] Pay rent (due the 5th)
- [ ] Book dentist appointment
- [ ] Order new running shoes

### Learning
- [ ] Finish Chapter 4 of the Rust book
- [ ] Try building a CLI tool
- [ ] Organize notes from last conference
""",
        "tags": ["to-do", "tasks", "planning"],
        "priority": 8
    },
    {
        "title": "Thoughts and wishes",
        "content": """## Private notes

### What is on my mind
- Thinking about switching teams, but the current one is great
- Want to adopt a dog, worried about the schedule
- Savings goal: $20k by year end

### Someday list
- Visit Japan
- Learn to play guitar
- Build and launch a side project

### Random facts about me
- Collect vinyl records
- Rewatch favorite movies constantly
- Prefer texting over phone calls
""",
        "tags": ["thoughts", "goals", "personal"],
        "priority": 6
    },
]

SAMPLE_MEMORIES_KO = [
    {
        "title": "나에 대해",
        "content": """## 개인 정보

저는 **지호**라고 해요. 28살이고 서울에서 소프트웨어 개발자로 일하고 있어요.

### 기본 정보
- 생일: 3월 15일
- 별자리: 물고기자리
- 사는 곳: 서울 마포구

### 성격
- 내향적이지만 친한 사람과는 말이 많아요
- 꼼꼼한 편이고 완벽주의 기질이 있어요
- 혼자 조용히 일하는 걸 좋아해요

### 좌우명
> 천 리 길도 한 걸음부터.
""",
        "tags": ["개인정보", "자기소개"],
        "priority": 10
    },
    {
        "title": "취미와 관심사",
        "content": """## 좋아하는 것

### 활동
- 달리기 (아침마다 5km)
- 독서 (SF, 에세이, 기술서)
- 요리 (한식 위주)
- 카페 탐방

### 좋아하는 음식
- 커피: 아이스 아메리카노
- 음식: 된장찌개, 삼겹살, 냉면
- 간식: 약과, 호두과자

### 음악
- 장르: 인디, K-pop, 재즈
- 작업할 때: 로파이 또는 백색소음
""",
        "tags": ["취미", "관심사", "생활"],
        "priority": 9
    },
    {
        "title": "직장 사람들",
        "content": """## 회사 사람들

### 팀장님
- **김수현 팀장** — 개발팀 리드
  - 회의보다 슬랙 메시지를 선호해요
  - 코드 리뷰를 꼼꼼하게 봐요

### 팀원
- **민수** — 백엔드 개발자
  - 믿음직하고 디버깅을 잘해요
  - 보드게임을 좋아해요

- **유진** — 프로덕트 디자이너
  - 디테일에 강해요
  - 이메일보다 슬랙이 빨라요

- **태현** — 프론트엔드 개발자
  - 코딩이 빠르지만 문서를 가끔 빼먹어요
  - 금요일에 간식을 사와요
""",
        "tags": ["회사", "동료", "사람"],
        "priority": 8
    },
    {
        "title": "가족과 친구",
        "content": """## 소중한 사람들

### 가족
- **아버지**: 은퇴한 교사, 등산 좋아해요, 매주 일요일 전화
- **어머니**: 요리를 잘하시고 잔소리가 많지만 따뜻해요
- **여동생 (지은)**: 대학교 3학년, 디자인 전공

### 친한 친구
- **상우** — 고등학교 동창, 부산에 살아요
  - 한 달에 한 번 영상통화
  - 고민 상담은 이 친구한테

- **하늘** — 대학 동아리 친구
  - 스타트업에서 일해요
  - 서로 책 추천을 해요

### 중요한 날
- 아버지 생신: 6월 10일
- 어머니 생신: 9월 2일
- 지은이 생일: 12월 5일
""",
        "tags": ["가족", "친구", "사람"],
        "priority": 9
    },
    {
        "title": "하루 일과",
        "content": """## 평일 일과

### 시간표
- 6:30 — 기상, 달리기
- 7:30 — 샤워, 아침
- 8:30 — 출근
- 12:00 — 점심
- 12:30 — 산책
- 18:00 — 퇴근
- 19:00 — 저녁 준비
- 22:00 — 독서
- 23:00 — 취침

### 주간 루틴
- **월요일**: 팀 스탠드업 10시
- **수요일**: 퇴근 후 헬스장
- **금요일**: 부모님 전화
- **주말**: 사이드 프로젝트, 등산, 늦잠

### 지키고 싶은 습관
- 물 자주 마시기
- 한 시간마다 스트레칭
- 밤 10시 이후 스크린 보지 않기
""",
        "tags": ["루틴", "습관", "일상"],
        "priority": 7
    },
    {
        "title": "요즘 할 일",
        "content": """## 할 일 목록

### 업무
- [ ] API 리팩토링 마무리
- [ ] 인증 플로우 설계 문서 작성
- [ ] 민수 PR 리뷰

### 생활
- [ ] 월세 납부 (5일까지)
- [ ] 치과 예약
- [ ] 운동화 새로 주문

### 공부
- [ ] Rust 책 4장 끝내기
- [ ] CLI 도구 만들어 보기
- [ ] 컨퍼런스 노트 정리
""",
        "tags": ["할일", "계획", "업무"],
        "priority": 8
    },
    {
        "title": "생각과 바람",
        "content": """## 혼자만의 기록

### 요즘 고민
- 팀을 옮길까 생각 중이지만 지금 팀이 좋아요
- 고양이를 키우고 싶은데 시간이 걱정돼요
- 연말까지 저축 목표: 2천만원

### 언젠가 하고 싶은 것
- 일본 여행
- 기타 배우기
- 사이드 프로젝트 런칭

### 나만 아는 사실
- 좋아하는 영화는 반복해서 봐요
- 예쁜 만년필 모으는 게 취미예요
- 전화보다 메시지가 편해요
""",
        "tags": ["생각", "목표", "개인"],
        "priority": 6
    },
]

SAMPLE_MEMORIES_ZH_TW = [
    {
        "title": "關於我自己",
        "content": """## 個人資訊

我叫**宇澤**，今年 28 歲，在台北當軟體工程師。

### 基本資料
- 生日：3 月 15 日
- 星座：雙魚座
- 坐標：台北大安區

### 性格
- 偏內向，但跟熟人話很多
- 做事細心，有點完美主義
- 喜歡獨處，享受安靜的時光

### 座右銘
> 日拱一卒，行則將至。
""",
        "tags": ["個人資訊", "自我介紹"],
        "priority": 10
    },
    {
        "title": "興趣愛好",
        "content": """## 喜歡的事

### 活動
- 跑步（每天早上 5 公里）
- 看書（科幻、設計、技術書）
- 料理（台式家常菜為主）
- 逛咖啡廳

### 喜歡的食物
- 咖啡：冰美式，不加糖
- 最愛：滷肉飯、牛肉麵、珍珠奶茶
- 甜點：芋泥蛋糕

### 音樂
- 類型：獨立音樂、電子、日系
- 工作時聽：白噪音或純音樂
""",
        "tags": ["興趣", "愛好", "生活"],
        "priority": 9
    },
    {
        "title": "工作上的人",
        "content": """## 公司同事

### 主管
- **陳經理** — 開發部門主管
  - 偏好非同步溝通，少開會
  - 很重視程式碼品質

### 團隊
- **阿凱** — 後端工程師
  - 很可靠，除錯能力強
  - 喜歡桌遊

- **雅婷** — 產品設計師
  - 對細節很敏銳
  - 用 Slack 比 email 快

- **志偉** — 前端工程師
  - 寫程式很快但偶爾跳過文件
  - 每週五帶點心給大家
""",
        "tags": ["工作", "同事", "人際"],
        "priority": 8
    },
    {
        "title": "家人和朋友",
        "content": """## 重要的人

### 家人
- **爸爸**：退休老師，喜歡爬山，每週日打電話
- **媽媽**：在家照顧家庭，記得節日買禮物給她
- **妹妹（小恩）**：大三，念設計系

### 好朋友
- **阿翔** — 高中同學，現在在新竹
  - 每個月約一次吃飯
  - 什麼都能聊的人

- **小安** — 大學室友
  - 在台中工作
  - 喜歡攝影

### 重要日期
- 爸爸生日：6 月 10 日
- 媽媽生日：9 月 2 日
- 妹妹生日：12 月 5 日
""",
        "tags": ["家人", "朋友", "人際"],
        "priority": 9
    },
    {
        "title": "每日作息",
        "content": """## 平日作息

### 時間表
- 6:30 — 起床、跑步
- 7:30 — 洗澡、早餐
- 8:30 — 開始工作
- 12:00 — 午餐
- 12:30 — 散步
- 18:00 — 下班
- 19:00 — 煮晚餐
- 22:00 — 看書
- 23:00 — 睡覺

### 每週固定
- **週一**：團隊站會 10 點
- **週三**：下班後健身
- **週五**：跟爸媽視訊
- **週末**：做 side project、爬山、睡懶覺

### 要保持的習慣
- 多喝水（容易忘記）
- 每小時站起來伸展
- 晚上十點後不看螢幕
""",
        "tags": ["作息", "習慣", "日常"],
        "priority": 7
    },
    {
        "title": "最近的待辦",
        "content": """## 待辦清單

### 工作
- [ ] 完成 API 重構
- [ ] 寫認證流程設計文件
- [ ] Review 阿凱的 PR

### 生活
- [ ] 繳房租（每月 5 號前）
- [ ] 預約牙醫
- [ ] 買新的跑鞋

### 學習
- [ ] 看完 Rust 書第四章
- [ ] 試做一個 CLI 工具
- [ ] 整理上次研討會的筆記
""",
        "tags": ["待辦", "計畫", "任務"],
        "priority": 8
    },
    {
        "title": "一些想法和願望",
        "content": """## 只有我知道的事

### 最近在想
- 有點想換團隊，但現在的團隊很好
- 想養一隻貓，但怕照顧不來
- 存款目標：年底存到 50 萬

### 有一天想做的事
- 去日本旅行
- 學吉他
- 做一個自己的產品上線

### 關於我的小事
- 喜歡收集好看的鋼筆
- 會一直重看喜歡的電影
- 比起打電話更喜歡傳訊息
""",
        "tags": ["想法", "目標", "個人"],
        "priority": 6
    },
]

SAMPLE_MEMORIES_ZH_CN = [
    {
        "title": "关于我自己",
        "content": """## 个人信息

我叫**宇泽**，今年 28 岁，在杭州做软件工程师。

### 基本信息
- 生日：3 月 15 日
- 星座：双鱼座
- 坐标：杭州西湖区

### 性格特点
- 偏内向，但和熟人话很多
- 做事比较细心，有点完美主义
- 喜欢独处，享受安静的时光

### 座右铭
> 日拱一卒，行则将至。
""",
        "tags": ["个人信息", "自我介绍"],
        "priority": 10
    },
    {
        "title": "兴趣爱好",
        "content": """## 喜欢的事

### 活动
- 跑步（每天早上 5 公里）
- 看书（科幻、设计、技术书）
- 做饭（家常菜为主）
- 逛咖啡馆

### 喜欢的食物
- 咖啡：冰美式，不加糖
- 最爱：螺蛳粉、牛肉面、奶茶
- 甜品：提拉米苏

### 音乐
- 类型：独立音乐、电子、日系
- 工作时听：白噪音或纯音乐
""",
        "tags": ["兴趣", "爱好", "生活"],
        "priority": 9
    },
    {
        "title": "工作上的人",
        "content": """## 公司同事

### 直属领导
- **陈经理** — 开发部主管
  - 偏好异步沟通，少开会
  - 很重视代码质量

### 团队
- **阿凯** — 后端工程师
  - 很靠谱，调试能力强
  - 喜欢桌游

- **雅婷** — 产品设计师
  - 对细节很敏锐
  - 用 Slack 比邮件快

- **志伟** — 前端工程师
  - 写代码很快但偶尔跳过文档
  - 每周五给大家带零食
""",
        "tags": ["工作", "同事", "人际关系"],
        "priority": 8
    },
    {
        "title": "家人和朋友",
        "content": """## 重要的人

### 家人
- **爸爸**：退休教师，喜欢钓鱼，每周日打电话
- **妈妈**：在家照顾家庭，记得节日给她买礼物
- **妹妹（小恩）**：大三，学的设计

### 好朋友
- **阿翔** — 高中同学，现在在深圳
  - 每个月约一次吃饭
  - 什么都能聊的人

- **小安** — 大学室友
  - 在上海工作
  - 喜欢摄影

### 重要日期
- 爸爸生日：6 月 10 日
- 妈妈生日：9 月 2 日
- 妹妹生日：12 月 5 日
""",
        "tags": ["家人", "朋友", "人际关系"],
        "priority": 9
    },
    {
        "title": "每日作息",
        "content": """## 平日作息

### 时间表
- 6:30 — 起床、跑步
- 7:30 — 洗澡、早餐
- 8:30 — 开始工作
- 12:00 — 午饭
- 12:30 — 散步
- 18:00 — 下班
- 19:00 — 做晚饭
- 22:00 — 看书
- 23:00 — 睡觉

### 每周固定
- **周一**：团队站会 10 点
- **周三**：下班后健身
- **周五**：和爸妈视频通话
- **周末**：做 side project、跑步、睡懒觉

### 要保持的习惯
- 多喝水（容易忘记）
- 每小时站起来伸展
- 晚上十点后不看屏幕
""",
        "tags": ["作息", "习惯", "日常"],
        "priority": 7
    },
    {
        "title": "最近的待办",
        "content": """## 待办清单

### 工作
- [ ] 完成 API 重构
- [ ] 写认证流程设计文档
- [ ] Review 阿凯的 PR

### 生活
- [ ] 交房租（每月 5 号前）
- [ ] 预约牙医
- [ ] 买新的跑鞋

### 学习
- [ ] 看完 Rust 书第四章
- [ ] 试做一个 CLI 工具
- [ ] 整理上次会议的笔记
""",
        "tags": ["待办", "计划", "任务"],
        "priority": 8
    },
    {
        "title": "一些想法和愿望",
        "content": """## 只有我知道的事

### 最近在想
- 有点想换组，但现在的团队很好
- 想养一只猫，但怕照顾不来
- 存款目标：年底存到 10 万

### 有一天想做的事
- 去日本旅行
- 学吉他
- 做一个自己的产品上线

### 关于我的小事
- 喜欢收集好看的钢笔
- 会一直重看喜欢的电影
- 比起打电话更喜欢发消息
""",
        "tags": ["想法", "目标", "个人"],
        "priority": 6
    },
]

_SAMPLE_MEMORIES_MAP = {
    "en": SAMPLE_MEMORIES_EN,
    "ko": SAMPLE_MEMORIES_KO,
    "zh-TW": SAMPLE_MEMORIES_ZH_TW,
    "zh-CN": SAMPLE_MEMORIES_ZH_CN,
}


def add_sample_memories(sync_manager, lang: str = "en") -> int:
    samples = _SAMPLE_MEMORIES_MAP.get(lang, SAMPLE_MEMORIES_EN)
    count = 0
    for sample in samples:
        try:
            sync_manager.add_memory(
                title=sample["title"],
                content=sample["content"],
                tags=sample["tags"],
                priority=sample["priority"],
                source="api"
            )
            count += 1
        except Exception as e:
            print(f"Failed to add sample: {sample['title']}, error: {e}")
    return count

