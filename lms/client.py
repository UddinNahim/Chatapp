import asyncio

import httpx
from sockudo_python import ChannelAuthorizationOptions, SockudoClient, SockudoOptions

API = "http://127.0.0.1:8000"


async def main() -> None:
    async with httpx.AsyncClient() as http:
        instructor = (await http.post(f"{API}/lms/instructors", json={"name": "Rahim"})).json()
        student = (await http.post(f"{API}/lms/students", json={"name": "Karim"})).json()

    chat = f"private-lms.{instructor['id']}.{student['id']}"
    print(
        f"Protocol V2 · instructor {instructor['name']} #{instructor['id']}"
        f" ↔ student {student['name']} #{student['id']}"
    )
    print(f"chat {chat}")
    print("type:  i hello   or   s hello")

    instructor_ws = SockudoClient(
        "app-key",
        SockudoOptions(
            cluster="local",
            ws_host="127.0.0.1",
            ws_port=6001,
            force_tls=False,
            protocol_version=2,
            connection_recovery=True,
            channel_authorization=ChannelAuthorizationOptions(
                endpoint=f"{API}/lms/auth",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                params={"role": "instructor", "person_id": str(instructor["id"])},
            ),
        ),
    )
    student_ws = SockudoClient(
        "app-key",
        SockudoOptions(
            cluster="local",
            ws_host="127.0.0.1",
            ws_port=6001,
            force_tls=False,
            protocol_version=2,
            connection_recovery=True,
            channel_authorization=ChannelAuthorizationOptions(
                endpoint=f"{API}/lms/auth",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                params={"role": "student", "person_id": str(student["id"])},
            ),
        ),
    )

    instructor_ws.bind(
        "state_change",
        lambda change, _: print("instructor", change["previous"], "→", change["current"]),
    )
    instructor_ws.bind("sockudo:resume_success", lambda data, _: print("instructor resume ok", data))
    instructor_ws.bind("sockudo:resume_failed", lambda data, _: print("instructor resume failed", data))
    instructor_chat = instructor_ws.subscribe(chat)
    instructor_chat.bind("sockudo:subscription_succeeded", lambda *_: print("instructor subscribed", chat))
    instructor_chat.bind("message.new", lambda data, _: print("instructor chat", data))
    instructor_inbox = instructor_ws.subscribe(f"private-lms-user.instructor.{instructor['id']}")
    instructor_inbox.bind(
        "sockudo:subscription_succeeded",
        lambda *_: print("instructor subscribed inbox"),
    )
    instructor_inbox.bind("notification.new", lambda data, _: print("instructor notif", data))

    student_ws.bind(
        "state_change",
        lambda change, _: print("student", change["previous"], "→", change["current"]),
    )
    student_ws.bind("sockudo:resume_success", lambda data, _: print("student resume ok", data))
    student_ws.bind("sockudo:resume_failed", lambda data, _: print("student resume failed", data))
    student_chat = student_ws.subscribe(chat)
    student_chat.bind("sockudo:subscription_succeeded", lambda *_: print("student subscribed", chat))
    student_chat.bind("message.new", lambda data, _: print("student chat", data))
    student_inbox = student_ws.subscribe(f"private-lms-user.student.{student['id']}")
    student_inbox.bind("sockudo:subscription_succeeded", lambda *_: print("student subscribed inbox"))
    student_inbox.bind("notification.new", lambda data, _: print("student notif", data))

    await instructor_ws.connect()
    await student_ws.connect()

    try:
        while True:
            line = await asyncio.to_thread(input, "> ")
            parts = line.strip().split(" ", 1)
            if len(parts) != 2 or parts[0] not in ("i", "s") or not parts[1].strip():
                print("use: i hello   or   s hello")
                continue
            async with httpx.AsyncClient() as http:
                res = await http.post(
                    f"{API}/lms/messages",
                    json={
                        "instructor_id": instructor["id"],
                        "student_id": student["id"],
                        "sender": "instructor" if parts[0] == "i" else "student",
                        "text": parts[1].strip(),
                    },
                )
            if res.status_code >= 400:
                print("POST failed", res.status_code, res.text)
    finally:
        await instructor_ws.disconnect()
        await student_ws.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
