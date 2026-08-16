import json
import re

import msgspec
from django.conf import settings
from django.utils import timezone
from django_bolt import BoltAPI, cors
from django_bolt.exceptions import HTTPException
from django_bolt.param_functions import Form
from sockudo_http import Config, Sockudo, TriggerOptions

from lms.models import Instructor, LmsNotification, Message, Post, Student

api = BoltAPI()

CORS_DEMO = dict(
    origins=["*"],
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    headers=["content-type", "authorization", "x-requested-with"],
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


def dm_channel(instructor_id: int, student_id: int) -> str:
    # instructor age, student pore — pair always same channel
    return f"private-lms.{instructor_id}.{student_id}"


def parse_dm_channel(channel: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"private-lms\.(\d+)\.(\d+)", channel)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def inbox_channel(role: str, person_id: int) -> str:
    # shudhu oi manush — chat channel na
    return f"private-lms-user.{role}.{person_id}"


def parse_inbox_channel(channel: str) -> tuple[str, int] | None:
    match = re.fullmatch(r"private-lms-user\.(instructor|student)\.(\d+)", channel)
    if not match:
        return None
    return match.group(1), int(match.group(2))


def unread_count_for(role: str, person_id: int) -> int:
    return LmsNotification.objects.filter(
        recipient_role=role,
        recipient_id=person_id,
        read_at__isnull=True,
    ).count()


def notification_payload(row: LmsNotification, unread_count: int) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "body": row.body,
        "from_role": row.from_role,
        "from_name": row.from_name,
        "channel": row.channel,
        "unread_count": unread_count,
        "read_at": row.read_at.isoformat() if row.read_at else None,
        "created_at": row.created_at.isoformat(),
    }


class NameSchema(msgspec.Struct):
    name: str


class PersonSchema(msgspec.Struct):
    id: int
    name: str


class PostCreateSchema(msgspec.Struct):
    instructor_id: int
    title: str
    body: str


class PostSchema(msgspec.Struct):
    id: int
    instructor_id: int
    instructor_name: str
    title: str
    body: str


@api.post("/lms/instructors")
@cors(**CORS_DEMO)
def create_instructor(payload: NameSchema) -> PersonSchema:
    instructor = Instructor.objects.create(name=payload.name.strip())
    return PersonSchema(id=instructor.id, name=instructor.name)


@api.post("/lms/students")
@cors(**CORS_DEMO)
def create_student(payload: NameSchema) -> PersonSchema:
    student = Student.objects.create(name=payload.name.strip())
    return PersonSchema(id=student.id, name=student.name)


@api.post("/lms/posts")
def create_post(payload: PostCreateSchema) -> PostSchema:
    """Shudhu instructor post create korte pare."""
    try:
        instructor = Instructor.objects.get(id=payload.instructor_id)
    except Instructor.DoesNotExist:
        raise HTTPException(status_code=403, detail="only an instructor can create a post")

    if not payload.title.strip() or not payload.body.strip():
        raise HTTPException(status_code=400, detail="title and body are required")

    post = Post.objects.create(
        instructor=instructor,
        title=payload.title.strip(),
        body=payload.body.strip(),
    )
    return PostSchema(
        id=post.id,
        instructor_id=instructor.id,
        instructor_name=instructor.name,
        title=post.title,
        body=post.body,
    )


@api.get("/lms/posts")
def list_posts(student_id: int) -> list[PostSchema]:
    """Shudhu student post list dekhte pare."""
    if not Student.objects.filter(id=student_id).exists():
        raise HTTPException(status_code=403, detail="only a student can list posts")

    posts = Post.objects.select_related("instructor").all()
    return [
        PostSchema(
            id=post.id,
            instructor_id=post.instructor_id,
            instructor_name=post.instructor.name,
            title=post.title,
            body=post.body,
        )
        for post in posts
    ]


class MessageCreateSchema(msgspec.Struct):
    instructor_id: int
    student_id: int
    sender: str  # "instructor" or "student"
    text: str


class MessageSchema(msgspec.Struct):
    id: int
    instructor_id: int
    student_id: int
    sender: str
    text: str


def require_pair(instructor_id: int, student_id: int) -> tuple[Instructor, Student]:
    try:
        instructor = Instructor.objects.get(id=instructor_id)
    except Instructor.DoesNotExist:
        raise HTTPException(status_code=404, detail="instructor not found")
    try:
        student = Student.objects.get(id=student_id)
    except Student.DoesNotExist:
        raise HTTPException(status_code=404, detail="student not found")
    return instructor, student


@api.post("/lms/auth")
@cors(**CORS_DEMO)
def lms_auth(
    socket_id: str = Form(...),
    channel_name: str = Form(...),
    role: str | None = Form(None),
    person_id: str | None = Form(None),
) -> dict:
    """Chat channel ba nijer inbox — onno keu join korte pare na."""
    if role not in ("instructor", "student") or not person_id:
        raise HTTPException(status_code=401, detail="role and person_id required")

    pair = parse_dm_channel(channel_name)
    inbox = parse_inbox_channel(channel_name)

    if pair is not None:
        instructor_id, student_id = pair
        require_pair(instructor_id, student_id)
        allowed = (
            (role == "instructor" and str(instructor_id) == str(person_id))
            or (role == "student" and str(student_id) == str(person_id))
        )
        if not allowed:
            raise HTTPException(status_code=403, detail="only this pair can join the chat")
    elif inbox is not None:
        inbox_role, inbox_id = inbox
        if role != inbox_role or str(inbox_id) != str(person_id):
            raise HTTPException(status_code=403, detail="only the owner can join this inbox")
    else:
        raise HTTPException(status_code=403, detail="unknown channel")

    sockudo = get_sockudo()
    try:
        return json.loads(sockudo.authenticate(socket_id, channel_name))
    finally:
        sockudo.close()


@api.post("/lms/messages")
@cors(**CORS_DEMO)
def create_message(payload: MessageCreateSchema) -> MessageSchema:
    """Instructor ba student — duijonei message pathate pare."""
    instructor, student = require_pair(payload.instructor_id, payload.student_id)

    if payload.sender not in ("instructor", "student"):
        raise HTTPException(status_code=400, detail="sender must be instructor or student")
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="text is required")

    text = payload.text.strip()
    message = Message.objects.create(
        instructor=instructor,
        student=student,
        sender=payload.sender,
        text=text,
    )
    data = MessageSchema(
        id=message.id,
        instructor_id=instructor.id,
        student_id=student.id,
        sender=message.sender,
        text=message.text,
    )

    # sender instructor hole student pabe; student hole instructor pabe
    if payload.sender == "instructor":
        to_role, to_id, from_name = "student", student.id, instructor.name
    else:
        to_role, to_id, from_name = "instructor", instructor.id, student.name

    chat = dm_channel(instructor.id, student.id)
    inbox = LmsNotification.objects.create(
        recipient_role=to_role,
        recipient_id=to_id,
        from_role=payload.sender,
        from_name=from_name,
        title=from_name,
        body=text[:80],
        channel=chat,
    )
    inbox_event = notification_payload(inbox, unread_count_for(to_role, to_id))

    sockudo = get_sockudo()
    try:
        result = sockudo.trigger(
            chat,
            "message.new",
            {
                "id": data.id,
                "instructor_id": data.instructor_id,
                "student_id": data.student_id,
                "sender": data.sender,
                "text": data.text,
            },
            TriggerOptions(idempotency_key=f"lms-msg-{message.id}"),
        )
        if not result.ok:
            raise HTTPException(
                status_code=502,
                detail=f"Sockudo publish failed: {result.message}",
            )
        notify = sockudo.trigger(
            inbox_channel(to_role, to_id),
            "notification.new",
            inbox_event,
            TriggerOptions(idempotency_key=f"lms-notif-{inbox.id}"),
        )
        if not notify.ok:
            raise HTTPException(
                status_code=502,
                detail=f"Sockudo notify failed: {notify.message}",
            )
    finally:
        sockudo.close()
    return data


@api.get("/lms/messages")
@cors(**CORS_DEMO)
def list_messages(instructor_id: int, student_id: int) -> list[MessageSchema]:
    """Ei instructor + ei student er 1:1 kotha."""
    require_pair(instructor_id, student_id)
    messages = Message.objects.filter(
        instructor_id=instructor_id,
        student_id=student_id,
    )
    return [
        MessageSchema(
            id=message.id,
            instructor_id=message.instructor_id,
            student_id=message.student_id,
            sender=message.sender,
            text=message.text,
        )
        for message in messages
    ]


class NotificationReadSchema(msgspec.Struct):
    role: str
    person_id: int


@api.get("/lms/notifications")
@cors(**CORS_DEMO)
def list_notifications(role: str, person_id: int, limit: int = 50) -> dict:
    if role not in ("instructor", "student"):
        raise HTTPException(status_code=400, detail="role must be instructor or student")
    cap = min(max(limit, 1), 100)
    rows = list(
        LmsNotification.objects.filter(recipient_role=role, recipient_id=person_id)[:cap]
    )
    unread = unread_count_for(role, person_id)
    return {
        "unread_count": unread,
        "items": [notification_payload(row, unread) for row in rows],
    }


@api.post("/lms/notifications/read-all")
@cors(**CORS_DEMO)
def read_all_notifications(payload: NotificationReadSchema) -> dict:
    if payload.role not in ("instructor", "student"):
        raise HTTPException(status_code=400, detail="role must be instructor or student")
    updated = LmsNotification.objects.filter(
        recipient_role=payload.role,
        recipient_id=payload.person_id,
        read_at__isnull=True,
    ).update(read_at=timezone.now())
    return {"updated": updated, "unread_count": 0}
