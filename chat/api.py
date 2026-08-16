import json
import re

import msgspec
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from django_bolt import BoltAPI, cors
from django_bolt.exceptions import HTTPException
from django_bolt.param_functions import Form
from sockudo_http import Config, Sockudo, TriggerOptions

from chat.models import ChatMessage, MessageReceipt, Room, RoomMember

User = get_user_model()
api = BoltAPI()

CORS_DEMO = dict(
    origins=["*"],
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    headers=["content-type", "authorization", "x-user-id", "x-requested-with"],
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


def user_channel(user_id: int) -> str:
    return f"private-user.{user_id}"


def require_demo_user(raw: str | int | None) -> int:
    if raw is None or raw == "":
        raise HTTPException(status_code=401, detail="user_id required")
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="invalid user_id")


def room_channel(room_id: int) -> str:
    return f"private-room.{room_id}"


def parse_room_channel(channel: str) -> int | None:
    match = re.fullmatch(r"private-room\.(\d+)", channel)
    return int(match.group(1)) if match else None


def pair_key(a: int, b: int) -> str:
    lo, hi = sorted((a, b))
    return f"{lo}:{hi}"


def usernames(ids: list[int]) -> dict[int, str]:
    return {u.id: u.username for u in User.objects.filter(id__in=ids)}


def unread_in_room(room_id: int, user_id: int, last_read_at) -> int:
    qs = ChatMessage.objects.filter(room_id=room_id).exclude(sender_id=user_id)
    if last_read_at is not None:
        qs = qs.filter(created_at__gt=last_read_at)
    return qs.count()


def total_unread(user_id: int) -> int:
    total = 0
    for member in RoomMember.objects.filter(user_id=user_id).only("room_id", "last_read_at"):
        total += unread_in_room(member.room_id, user_id, member.last_read_at)
    return total


def room_title(room: Room, me: int, names: dict[int, str], member_ids: list[int]) -> str:
    if room.kind == Room.GROUP:
        return room.title or "Group"
    other = next((uid for uid in member_ids if uid != me), me)
    return names.get(other, f"User {other}")


def last_message_row(room_id: int) -> ChatMessage | None:
    return ChatMessage.objects.filter(room_id=room_id).order_by("-created_at").first()


def iso(value) -> str | None:
    return value.isoformat() if value else None


def message_payload(row: ChatMessage, names: dict[int, str], me: int) -> dict:
    receipts = list(row.receipts.all())
    recipient_count = len(receipts)
    delivered = [r for r in receipts if r.delivered_at]
    read = [r for r in receipts if r.read_at]
    return {
        "id": row.id,
        "room_id": row.room_id,
        "sender_id": row.sender_id,
        "sender": names.get(row.sender_id, f"User {row.sender_id}"),
        "text": row.text,
        "created_at": row.created_at.isoformat(),
        "mine": row.sender_id == me,
        "delivered_at": iso(max((r.delivered_at for r in delivered), default=None))
        if recipient_count and len(delivered) == recipient_count
        else None,
        "read_at": iso(max((r.read_at for r in read), default=None))
        if recipient_count and len(read) == recipient_count
        else None,
        "delivered_count": len(delivered),
        "read_count": len(read),
        "recipient_count": recipient_count,
        "receipts": [
            {
                "user_id": r.user_id,
                "delivered_at": iso(r.delivered_at),
                "read_at": iso(r.read_at),
            }
            for r in receipts
        ],
    }


def publish_receipts(room_id: int, items: list[dict]) -> None:
    if not items:
        return
    sockudo = get_sockudo()
    try:
        result = sockudo.trigger(
            room_channel(room_id),
            "receipt.update",
            {"room_id": room_id, "items": items},
            TriggerOptions(idempotency_key=f"receipt-{room_id}-{items[-1]['id']}-{items[-1].get('read_at') or items[-1].get('delivered_at')}"),
        )
        if not result.ok:
            raise HTTPException(status_code=502, detail=f"Sockudo receipt failed: {result.message}")
    finally:
        sockudo.close()


def stamp_receipts(user_id: int, messages: list[ChatMessage], *, read: bool) -> list[ChatMessage]:
    now = timezone.now()
    ids = [row.id for row in messages]
    rows = list(MessageReceipt.objects.filter(user_id=user_id, message_id__in=ids))
    changed_ids: list[int] = []
    for row in rows:
        fields: list[str] = []
        if row.delivered_at is None:
            row.delivered_at = now
            fields.append("delivered_at")
        if read and row.read_at is None:
            row.read_at = now
            fields.append("read_at")
        if fields:
            row.save(update_fields=fields)
            changed_ids.append(row.message_id)
    return [row for row in messages if row.id in changed_ids]


def room_payload(room: Room, me: int, member: RoomMember) -> dict:
    member_ids = list(
        RoomMember.objects.filter(room=room).values_list("user_id", flat=True)
    )
    names = usernames(member_ids)
    last = last_message_row(room.id)
    unread = unread_in_room(room.id, me, member.last_read_at)
    return {
        "id": room.id,
        "kind": room.kind,
        "title": room_title(room, me, names, member_ids),
        "member_ids": member_ids,
        "members": [{"id": uid, "username": names.get(uid, f"User {uid}")} for uid in member_ids],
        "unread": unread,
        "updated_at": room.updated_at.isoformat(),
        "last_message": None
        if last is None
        else {
            "id": last.id,
            "sender_id": last.sender_id,
            "sender": names.get(last.sender_id, f"User {last.sender_id}"),
            "text": last.text,
            "created_at": last.created_at.isoformat(),
        },
    }


def require_member(room_id: int, user_id: int) -> tuple[Room, RoomMember]:
    try:
        room = Room.objects.get(id=room_id)
    except Room.DoesNotExist:
        raise HTTPException(status_code=404, detail="room not found")
    try:
        member = RoomMember.objects.get(room=room, user_id=user_id)
    except RoomMember.DoesNotExist:
        raise HTTPException(status_code=403, detail="not a member of this room")
    return room, member


def publish_inbox(user_id: int, payload: dict) -> None:
    sockudo = get_sockudo()
    try:
        result = sockudo.trigger(
            user_channel(user_id),
            "inbox.update",
            payload,
            TriggerOptions(
                idempotency_key=f"inbox-{user_id}-{payload.get('room_id')}-{payload.get('event')}-{(payload.get('last_message') or {}).get('id', 0)}"
            ),
        )
        if not result.ok:
            raise HTTPException(status_code=502, detail=f"Sockudo notify failed: {result.message}")
    finally:
        sockudo.close()


class RoomCreateSchema(msgspec.Struct):
    user_id: int
    kind: str
    member_ids: list[int]
    title: str = ""


class ChatMessageCreateSchema(msgspec.Struct):
    user_id: int
    text: str


class RoomReadSchema(msgspec.Struct):
    user_id: int


@api.post("/chat/auth")
@cors(**CORS_DEMO)
def chat_auth(
    socket_id: str = Form(...),
    channel_name: str = Form(...),
    user_id: str | None = Form(None),
) -> dict:
    me = require_demo_user(user_id)
    room_id = parse_room_channel(channel_name)
    if room_id is not None:
        require_member(room_id, me)
    elif channel_name == user_channel(me):
        pass
    else:
        raise HTTPException(status_code=403, detail="unknown channel")

    sockudo = get_sockudo()
    try:
        return json.loads(sockudo.authenticate(socket_id, channel_name))
    finally:
        sockudo.close()


@api.get("/chat/rooms")
@cors(**CORS_DEMO)
def list_rooms(user_id: int) -> dict:
    me = require_demo_user(user_id)
    memberships = list(
        RoomMember.objects.filter(user_id=me).select_related("room").order_by("-room__updated_at")
    )
    rooms = [room_payload(m.room, me, m) for m in memberships]
    return {
        "total_unread": sum(r["unread"] for r in rooms),
        "rooms": rooms,
    }


@api.post("/chat/rooms")
@cors(**CORS_DEMO)
def create_room(payload: RoomCreateSchema) -> dict:
    me = require_demo_user(payload.user_id)
    others = sorted({int(uid) for uid in payload.member_ids if int(uid) != me})
    if payload.kind == Room.DIRECT:
        if len(others) != 1:
            raise HTTPException(status_code=400, detail="direct room needs exactly one other user")
        key = pair_key(me, others[0])
        room = Room.objects.filter(kind=Room.DIRECT, pair_key=key).first()
        if room is None:
            if User.objects.filter(id__in=[me, others[0]]).count() != 2:
                raise HTTPException(status_code=404, detail="user not found")
            room = Room.objects.create(kind=Room.DIRECT, pair_key=key)
            RoomMember.objects.bulk_create(
                [RoomMember(room=room, user_id=me), RoomMember(room=room, user_id=others[0])]
            )
            for uid in (me, others[0]):
                member = RoomMember.objects.get(room=room, user_id=uid)
                publish_inbox(
                    uid,
                    {"event": "room.created", "room_id": room.id, **room_payload(room, uid, member), "total_unread": total_unread(uid)},
                )
        member = RoomMember.objects.get(room=room, user_id=me)
        return room_payload(room, me, member)

    if payload.kind != Room.GROUP:
        raise HTTPException(status_code=400, detail="kind must be direct or group")
    if not others:
        raise HTTPException(status_code=400, detail="group needs at least one other member")
    member_ids = [me, *others]
    names = usernames(member_ids)
    title = payload.title.strip() or ", ".join(names.get(uid, f"User {uid}") for uid in member_ids)
    if User.objects.filter(id__in=member_ids).count() != len(member_ids):
        raise HTTPException(status_code=404, detail="user not found")
    room = Room.objects.create(kind=Room.GROUP, title=title)
    RoomMember.objects.bulk_create([RoomMember(room=room, user_id=uid) for uid in member_ids])
    for uid in member_ids:
        member = RoomMember.objects.get(room=room, user_id=uid)
        publish_inbox(
            uid,
            {"event": "room.created", "room_id": room.id, **room_payload(room, uid, member), "total_unread": total_unread(uid)},
        )
    member = RoomMember.objects.get(room=room, user_id=me)
    return room_payload(room, me, member)


@api.get("/chat/rooms/{room_id}/messages")
@cors(**CORS_DEMO)
def list_messages(room_id: int, user_id: int, limit: int = 80) -> dict:
    me = require_demo_user(user_id)
    room, member = require_member(room_id, me)
    cap = min(max(limit, 1), 200)
    rows = list(
        ChatMessage.objects.filter(room=room)
        .prefetch_related("receipts")
        .order_by("-created_at")[:cap]
    )
    rows.reverse()
    names = usernames(list({row.sender_id for row in rows} | {me}))
    incoming = [row for row in rows if row.sender_id != me]
    changed = stamp_receipts(me, incoming, read=False)
    if changed:
        changed = list(
            ChatMessage.objects.filter(id__in=[row.id for row in changed]).prefetch_related("receipts")
        )
        publish_receipts(room.id, [message_payload(row, names, row.sender_id) for row in changed])
        rows = list(
            ChatMessage.objects.filter(id__in=[row.id for row in rows]).prefetch_related("receipts")
        )
        rows.sort(key=lambda row: row.created_at)
    return {
        "room": room_payload(room, me, member),
        "items": [message_payload(row, names, me) for row in rows],
    }


@api.post("/chat/rooms/{room_id}/messages")
@cors(**CORS_DEMO)
def send_message(room_id: int, payload: ChatMessageCreateSchema) -> dict:
    me = require_demo_user(payload.user_id)
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="text is required")
    room, member = require_member(room_id, me)
    text = payload.text.strip()
    message = ChatMessage.objects.create(room=room, sender_id=me, text=text)
    others = list(RoomMember.objects.filter(room=room).exclude(user_id=me))
    MessageReceipt.objects.bulk_create(
        [MessageReceipt(message=message, user_id=other.user_id) for other in others]
    )
    room.updated_at = message.created_at
    room.save(update_fields=["updated_at"])
    member.last_read_at = message.created_at
    member.save(update_fields=["last_read_at"])

    names = usernames([me])
    message = ChatMessage.objects.prefetch_related("receipts").get(id=message.id)
    data = message_payload(message, names, me)

    sockudo = get_sockudo()
    try:
        result = sockudo.trigger(
            room_channel(room.id),
            "message.new",
            data,
            TriggerOptions(idempotency_key=f"chat-msg-{message.id}"),
        )
        if not result.ok:
            raise HTTPException(status_code=502, detail=f"Sockudo publish failed: {result.message}")
    finally:
        sockudo.close()

    for other in others:
        publish_inbox(
            other.user_id,
            {
                "event": "message.new",
                "room_id": room.id,
                **room_payload(room, other.user_id, other),
                "total_unread": total_unread(other.user_id),
            },
        )
    return data


@api.post("/chat/rooms/{room_id}/read")
@cors(**CORS_DEMO)
def mark_room_read(room_id: int, payload: RoomReadSchema) -> dict:
    me = require_demo_user(payload.user_id)
    room, member = require_member(room_id, me)
    member.last_read_at = timezone.now()
    member.save(update_fields=["last_read_at"])
    incoming = list(
        ChatMessage.objects.filter(room=room)
        .exclude(sender_id=me)
        .prefetch_related("receipts")
    )
    changed = stamp_receipts(me, incoming, read=True)
    if changed:
        names = usernames(list({row.sender_id for row in changed}))
        changed = list(
            ChatMessage.objects.filter(id__in=[row.id for row in changed]).prefetch_related("receipts")
        )
        publish_receipts(room.id, [message_payload(row, names, row.sender_id) for row in changed])
    return {
        "room_id": room.id,
        "unread": 0,
        "total_unread": total_unread(me),
    }


class ReceiptSchema(msgspec.Struct):
    user_id: int


@api.post("/chat/messages/{message_id}/delivered")
@cors(**CORS_DEMO)
def mark_delivered(message_id: int, payload: ReceiptSchema) -> dict:
    me = require_demo_user(payload.user_id)
    try:
        message = ChatMessage.objects.prefetch_related("receipts").get(id=message_id)
    except ChatMessage.DoesNotExist:
        raise HTTPException(status_code=404, detail="message not found")
    require_member(message.room_id, me)
    if message.sender_id == me:
        return message_payload(message, usernames([me]), me)
    changed = stamp_receipts(me, [message], read=False)
    message = ChatMessage.objects.prefetch_related("receipts").get(id=message_id)
    data = message_payload(message, usernames([message.sender_id, me]), message.sender_id)
    if changed:
        publish_receipts(message.room_id, [data])
    return data
