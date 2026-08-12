from django.conf import settings
import msgspec
from django.contrib.auth import get_user_model
from django_bolt import BoltAPI, cors
from django_bolt.exceptions import HTTPException
from django.db import IntegrityError
from sockudo_http import Config, Sockudo

def get_sockudo() -> Sockudo:
    config = settings.SOCKUDO
    return Sockudo(
        Config(
            app_id=config['APP_ID'],
            key=config['APP_KEY'],
            secret=config['APP_SECRET'],
            host=config['HOST'],
            port=config['PORT'],
        )
    )
    

user = get_user_model()

api = BoltAPI()


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
@cors(origins=["*"])
def create_message(payload: MessageCreateSchema) -> dict:
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    
    data = {
        "username": payload.username,
        "text": payload.text,
    }

    sockudo = get_sockudo()
    try:
        result = sockudo.trigger(
            "chat:general",
            "message.new",
            data,
        )

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
    """
    Create a new user.
    """

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
    """
    Get all users.
    """
    users = user.objects.all()
    return [UserSchema(
        id=user.id,
        username=user.username,
        email=user.email,
    ) for user in users]


@api.get("/users/{user_id}")
def get_user(user_id: int) -> UserSchema:
    """
    Get a user by ID.

    Args:
        user_id (int): The ID of the user to retrieve.

    Returns:
        UserSchema: The user data.
    """
    try:
        user_instance = user.objects.get(id=user_id)
        return UserSchema(
            id=user_instance.id,
            username=user_instance.username,
            email=user_instance.email,
        )
    except user.DoesNotExist:
        raise HTTPException(status_code=404, detail="User not found")
