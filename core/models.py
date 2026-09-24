from __future__ import annotations
from enum import Enum
from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field
from datetime import datetime


class AccountType(str, Enum):
    USER = "user"
    BOT = "bot"


class TaskMode(str, Enum):
    HISTORICAL = "historical"
    LIVE_SYNC = "live_sync"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MediaType(str, Enum):
    ALL = "all"
    TEXT = "text"
    PHOTO = "photo"
    VIDEO = "video"
    DOCUMENT = "document"
    AUDIO = "audio"
    VOICE = "voice"
    ANIMATION = "animation"
    STICKER = "sticker"
    VIDEO_NOTE = "video_note"
    POLL = "poll"


# Auth Schemas
class AuthSendCodeRequest(BaseModel):
    phone_number: str


class AuthVerifyCodeRequest(BaseModel):
    phone_code: str
    phone_code_hash: str
    phone_number: str


class AuthVerifyPasswordRequest(BaseModel):
    password: str
    phone_number: str


class AuthBotLoginRequest(BaseModel):
    bot_token: str


class AccountStatusResponse(BaseModel):
    is_authenticated: bool
    account_type: Optional[AccountType] = None
    account_name: Optional[str] = None
    phone_number: Optional[str] = None
    user_id: Optional[int] = None
    username: Optional[str] = None


# Rule Schemas
class TextRuleCreate(BaseModel):
    name: str
    rule_type: str = "replace"  # "replace", "remove_links", "remove_mentions", "header", "footer"
    pattern: str = ""
    replacement: str = ""
    is_regex: bool = False
    enabled: bool = True


class TextRuleUpdate(BaseModel):
    name: Optional[str] = None
    rule_type: Optional[str] = None
    pattern: Optional[str] = None
    replacement: Optional[str] = None
    is_regex: Optional[bool] = None
    enabled: Optional[bool] = None


class TextRuleResponse(TextRuleCreate):
    id: int
    created_at: str


# Task Schemas
class TaskCreate(BaseModel):
    name: str = "Clonagem de Canal"
    mode: TaskMode = TaskMode.HISTORICAL
    origin_chat: str
    dest_chat: str
    start_message_id: Optional[int] = None
    end_message_id: Optional[int] = None
    media_types: List[str] = Field(default_factory=lambda: ["all"])
    clean_forward: bool = True
    delay_seconds: Optional[float] = None
    skip_delay_seconds: Optional[float] = None
    remove_captions: bool = False
    remove_links: bool = False
    remove_mentions: bool = False
    header_text: Optional[str] = ""
    footer_text: Optional[str] = ""
    custom_replacements: Optional[List[Dict[str, Any]]] = Field(default_factory=list)


class TaskUpdate(BaseModel):
    name: Optional[str] = None
    mode: Optional[TaskMode] = None
    origin_chat: Optional[str] = None
    dest_chat: Optional[str] = None
    start_message_id: Optional[int] = None
    end_message_id: Optional[int] = None
    media_types: Optional[List[str]] = None
    clean_forward: Optional[bool] = None
    delay_seconds: Optional[float] = None
    skip_delay_seconds: Optional[float] = None
    remove_captions: Optional[bool] = None
    remove_links: Optional[bool] = None
    remove_mentions: Optional[bool] = None
    header_text: Optional[str] = None
    footer_text: Optional[str] = None
    custom_replacements: Optional[List[Dict[str, Any]]] = None


class TaskResponse(BaseModel):
    id: int
    owner_id: Optional[int] = None
    name: str
    mode: TaskMode
    origin_chat: str
    origin_title: Optional[str] = None
    dest_chat: str
    dest_title: Optional[str] = None
    status: TaskStatus
    start_message_id: int = 1
    end_message_id: Optional[int] = None
    current_message_id: int = 0
    total_messages: int = 0
    processed_messages: int = 0
    copied_count: int = 0
    skipped_count: int = 0
    error_count: int = 0
    media_types: List[str]
    clean_forward: bool = True
    delay_seconds: float = 10.0
    skip_delay_seconds: float = 0.5
    remove_captions: bool = False
    remove_links: bool = False
    remove_mentions: bool = False
    header_text: str = ""
    footer_text: str = ""
    custom_replacements: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: str
    updated_at: str


class LogEntry(BaseModel):
    task_id: Optional[int] = None
    level: str = "info"  # "info", "warning", "error", "success"
    message: str
    timestamp: str = Field(default_factory=lambda: datetime.now().strftime("%H:%M:%S"))


# Publisher & Managed Groups Schemas
class ScheduleType(str, Enum):
    NOW = "now"
    ONCE = "once"
    RECURRING = "recurring"


class PostStatus(str, Enum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    PARTIALLY_FAILED = "partially_failed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ManagedGroupCreate(BaseModel):
    chat_id: str
    title: str
    chat_type: str = "supergroup"
    is_admin: bool = False


class ManagedGroupResponse(ManagedGroupCreate):
    id: int
    added_at: str


class RecurrenceRule(BaseModel):
    freq: str  # "interval", "daily", "weekly"
    interval_hours: Optional[int] = None
    weekday: Optional[int] = None  # 0=Monday ... 6=Sunday
    time_hhmm: Optional[str] = "09:00"


class PostCreate(BaseModel):
    name: str
    text: str
    media_path: Optional[str] = None
    media_type: Optional[str] = None
    target_group_ids: List[int] = Field(default_factory=list)
    schedule_type: ScheduleType = ScheduleType.NOW
    recurrence_rule: Optional[RecurrenceRule] = None
    run_at: Optional[str] = None


class PostUpdate(BaseModel):
    name: Optional[str] = None
    text: Optional[str] = None
    media_path: Optional[str] = None
    media_type: Optional[str] = None
    target_group_ids: Optional[List[int]] = None
    schedule_type: Optional[ScheduleType] = None
    recurrence_rule: Optional[RecurrenceRule] = None
    run_at: Optional[str] = None
    status: Optional[PostStatus] = None


class PostDeliveryResponse(BaseModel):
    id: int
    post_id: int
    chat_id: str
    chat_title: Optional[str] = None
    message_id: Optional[int] = None
    status: str
    error: Optional[str] = None
    sent_at: str


class PostResponse(BaseModel):
    id: int
    owner_id: Optional[int] = None
    name: str
    text: str
    media_path: Optional[str] = None
    media_type: Optional[str] = None
    target_group_ids: List[int] = Field(default_factory=list)
    status: str
    schedule_type: str
    recurrence_rule: Optional[RecurrenceRule] = None
    run_at: Optional[str] = None
    next_run_at: Optional[str] = None
    last_run_at: Optional[str] = None
    created_at: str
    updated_at: str
    total_deliveries: int = 0
    successful_deliveries: int = 0

