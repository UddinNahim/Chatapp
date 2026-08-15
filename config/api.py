import json
import re

import msgspec
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.utils import timezone
from django_bolt import BoltAPI, cors
from django_bolt.exceptions import HTTPException
from django_bolt.openapi import OpenAPIConfig, SwaggerRenderPlugin
from django_bolt.param_functions import Form
from sockudo_http import Config, Sockudo

from notifications.models import Notification

# Browser demos run on :5500; allow preflight + custom/simple headers.
CORS_DEMO = dict(
    origins=["*"],
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    headers=[
        "content-type",
        "authorization",
        "x-user-id",
        "x-requested-with",
    ],
)


def get_sockudo() -> Sockudo:
    config = settings.SOCKUDO
    return Sockudo(
        Config(
            app_id=config["APP_ID"],
            key=config["APP_KEY"],
            secret=config["APP_SECRET"],
            host=config["HOST"],
            port=config["PORT"],
        )
    )


user = get_user_model()
api = BoltAPI(
    openapi_config=OpenAPIConfig(
        title="Learn Django-Bolt",
        version="0.1.0",
        description="Users, DM, LMS chat, and notifications.",
        render_plugins=[SwaggerRenderPlugin()],
    )
)


class UserSchema(msgspec.Struct):
    id: int
    username: str
    email: str


class UserCreateSchema(msgspec.Struct):
    username: str
    email: str


class MessageCreateSchema(msgspec.Struct):
    username: str
    text: str


@api.post("/messages")
@cors(**CORS_DEMO)
def create_message(payload: MessageCreateSchema) -> dict:
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    data = {
        "username": payload.username,
        "text": payload.text,
    }

    sockudo = get_sockudo()
    try:
        result = sockudo.trigger("chat-general", "message.new", data)
        if not result.ok:
            raise HTTPException(
                status_code=502,
                detail=f"Sockudo publish failed: {result.message}",
            )
    finally:
        sockudo.close()
    return data


@api.post("/users")
def create_user(payload: UserCreateSchema) -> UserSchema:
    try:
        user_instance = user.objects.create_user(
            username=payload.username,
            email=payload.email,
        )
    except IntegrityError:
        raise HTTPException(status_code=409, detail="User already exists")

    schema = UserSchema(
        id=user_instance.id,
        username=user_instance.username,
        email=user_instance.email,
    )

    sockudo = get_sockudo()
    try:
        result = sockudo.trigger(
            "users",
            "user.created",
            {
                "id": schema.id,
                "username": schema.username,
                "email": schema.email,
            },
        )
        if not result.ok:
            raise HTTPException(
                status_code=502,
                detail=f"Sockudo publish failed: {result.message}",
            )
    finally:
        sockudo.close()
    return schema


@api.get("/users")
def get_users() -> list[UserSchema]:
    users = user.objects.all()
    return [
        UserSchema(id=u.id, username=u.username, email=u.email) for u in users
    ]


@api.get("/users/{user_id}")
def get_user(user_id: int) -> UserSchema:
    try:
        user_instance = user.objects.get(id=user_id)
        return UserSchema(
            id=user_instance.id,
            username=user_instance.username,
            email=user_instance.email,
        )
    except user.DoesNotExist:
        raise HTTPException(status_code=404, detail="User not found")


# --- One-to-one DM (private channels) ---------------------------------------


def dm_channel(user_a: int, user_b: int) -> str:
    lo, hi = sorted((user_a, user_b))
    return f"private-chat.{lo}.{hi}"


def parse_dm_channel(channel: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"private-chat\.(\d+)\.(\d+)", channel)
    if not match:
        return None
    a, b = int(match.group(1)), int(match.group(2))
    if a >= b:
        return None
    return a, b


def user_channel(user_id: int) -> str:
    return f"private-user.{user_id}"


def parse_user_channel(channel: str) -> int | None:
    match = re.fullmatch(r"private-user\.(\d+)", channel)
    return int(match.group(1)) if match else None


def authorize_private_channel(me: int, channel_name: str) -> None:
    pair = parse_dm_channel(channel_name)
    if pair is not None:
        if me not in pair:
            raise HTTPException(status_code=403, detail="forbidden channel")
        return
    owner = parse_user_channel(channel_name)
    if owner is not None:
        if me != owner:
            raise HTTPException(status_code=403, detail="forbidden channel")
        return
    raise HTTPException(status_code=403, detail="unknown channel")


def notification_payload(row: Notification, unread_count: int) -> dict:
    return {
        "id": row.id,
        "kind": row.kind,
        "title": row.title,
        "body": row.body,
        "from_user_id": row.from_user_id,
        "channel": row.channel,
        "unread_count": unread_count,
        "read_at": row.read_at.isoformat() if row.read_at else None,
        "created_at": row.created_at.isoformat(),
    }


def unread_count_for(user_id: int) -> int:
    return Notification.objects.filter(user_id=user_id, read_at__isnull=True).count()


def require_demo_user(raw: str | int | None) -> int:
    """Learning-only identity. Replace with real auth later."""
    if raw is None or raw == "":
        raise HTTPException(status_code=401, detail="user_id required")
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="invalid user_id")


class DmMessageCreateSchema(msgspec.Struct):
    from_user_id: int
    to_user_id: int
    text: str


@api.post("/pusher/auth")
@cors(**CORS_DEMO)
def pusher_auth(
    socket_id: str = Form(...),
    channel_name: str = Form(...),
    user_id: str | None = Form(None),
) -> dict:
    """Private-channel auth. pusher-js sends form fields; pass user_id via params."""
    me = require_demo_user(user_id)
    authorize_private_channel(me, channel_name)

    sockudo = get_sockudo()
    try:
        auth_json = sockudo.authenticate(socket_id, channel_name)
        return json.loads(auth_json)
    finally:
        sockudo.close()


@api.post("/dm/messages")
@cors(**CORS_DEMO)
def create_dm_message(payload: DmMessageCreateSchema) -> dict:
    me = require_demo_user(payload.from_user_id)
    if me == payload.to_user_id:
        raise HTTPException(status_code=400, detail="cannot DM yourself")
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="text is required")

    text = payload.text.strip()
    channel = dm_channel(me, payload.to_user_id)
    data = {
        "from_user_id": me,
        "to_user_id": payload.to_user_id,
        "text": text,
    }

    inbox = Notification.objects.create(
        user_id=payload.to_user_id,
        kind="dm",
        title=f"User {me}",
        body=text[:80],
        from_user_id=me,
        channel=channel,
    )
    inbox_event = notification_payload(inbox, unread_count_for(payload.to_user_id))

    sockudo = get_sockudo()
    try:
        result = sockudo.trigger(channel, "message.new", data)
        if not result.ok:
            raise HTTPException(
                status_code=502,
                detail=f"Sockudo publish failed: {result.message}",
            )
        notify = sockudo.trigger(
            user_channel(payload.to_user_id),
            "notification.new",
            inbox_event,
        )
        if not notify.ok:
            raise HTTPException(
                status_code=502,
                detail=f"Sockudo notify failed: {notify.message}",
            )
    finally:
        sockudo.close()
    return {"channel": channel, "notification": inbox_event, **data}


class NotificationReadSchema(msgspec.Struct):
    user_id: int


@api.get("/notifications")
@cors(**CORS_DEMO)
def list_notifications(user_id: int, limit: int = 50) -> dict:
    me = require_demo_user(user_id)
    cap = min(max(limit, 1), 100)
    rows = list(Notification.objects.filter(user_id=me)[:cap])
    unread = unread_count_for(me)
    return {
        "unread_count": unread,
        "items": [notification_payload(row, unread) for row in rows],
    }


@api.post("/notifications/{notification_id}/read")
@cors(**CORS_DEMO)
def read_notification(notification_id: int, payload: NotificationReadSchema) -> dict:
    me = require_demo_user(payload.user_id)
    try:
        row = Notification.objects.get(id=notification_id, user_id=me)
    except Notification.DoesNotExist:
        raise HTTPException(status_code=404, detail="notification not found")
    if row.read_at is None:
        row.read_at = timezone.now()
        row.save(update_fields=["read_at"])
    unread = unread_count_for(me)
    return notification_payload(row, unread)


@api.post("/notifications/read-all")
@cors(**CORS_DEMO)
def read_all_notifications(payload: NotificationReadSchema) -> dict:
    me = require_demo_user(payload.user_id)
    updated = Notification.objects.filter(user_id=me, read_at__isnull=True).update(
        read_at=timezone.now()
    )
    return {"updated": updated, "unread_count": 0}
