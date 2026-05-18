from __future__ import annotations

import os
import time

from flask import Flask, render_template, request, send_from_directory, session
from ui.services import create_chat_service


def create_app() -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config["SECRET_KEY"] = "dev"
    mock_data_enabled = os.getenv(
        "MOCK_DATA_ENABLED",
        os.getenv("MOCK_RESPONSES_ENABLED", "true"),
    )
    app.config["MOCK_DATA_ENABLED"] = mock_data_enabled.strip().lower() == "false"
    chat_service = create_chat_service(app.config["MOCK_DATA_ENABLED"], 
                                       refreshes={
                                           "users": lambda users: render_template("partials/users_list.html", users=users),
                                           "messages": lambda messages: render_template(
                                               "partials/message_list.html",
                                               messages=messages,
                                               users=chat_service.get_users(),
                                               users_map={u.get("ip", ""): u.get("name", "") for u in chat_service.get_users()},
                                           ),
                                       })
    app.chat_service = chat_service
    app.connected_state = {
            "connected": False,
            "username": "",
            "ip": ""
        }

    @app.get("/")
    def index() -> str:
        return render_template(
            "chat.html",
            users=chat_service.get_users(),
            messages=chat_service.get_messages(),
            **app.connected_state,
        )

    @app.get("/users")
    def user_list() -> str:
        return render_template("users.html", users=chat_service.get_users())

    @app.get("/favicon.ico")
    def favicon() -> object:
        return send_from_directory(app.static_folder, "assets/favicon.svg", mimetype="image/svg+xml")

    @app.get("/users/partial")
    def users_partial() -> str:
        # Render the small users partial used inside the chat UI aside.
        users = chat_service.get_users()
        return render_template("partials/users_list.html", users=users)

    def render_connect_state(connected: bool, username: str = "", ip: str = "") -> str:
        return render_template(
            "partials/connect_state.html",
            connected=connected,
            username=username,
            ip=ip,
        )

    def render_users_and_connect_state(connected: bool, username: str = "", ip: str = "") -> str:
        users_html = render_template("partials/users_list.html", users=chat_service.get_users())
        connect_html = render_connect_state(connected=connected, username=username, ip=ip)
        return users_html + connect_html

    def render_all_partials(connected: bool, username: str = "", ip: str = "") -> str:
        users_html = render_template("partials/users_list.html", users=chat_service.get_users())
        messages_html = render_template(
            "partials/message_list.html",
            messages=chat_service.get_messages(),
            users=chat_service.get_users(),
            users_map={u.get("ip", ""): u.get("name", "") for u in chat_service.get_users()},
        )
        connect_html = render_connect_state(connected=connected, username=username, ip=ip)
        return users_html + messages_html + connect_html

    @app.post("/connect")
    def connect() -> str:
        username = request.form.get("username", "").strip()
        ip = request.form.get("ip", "").strip()
        chat_service.connect(username, ip)
        print(f"================{chat_service._messages}================")
        app.connected_state["connected"] = True
        app.connected_state["username"] = username
        app.connected_state["ip"] = ip
        return render_all_partials(connected=True, username=username, ip=ip)

    @app.post("/disconnect")
    def disconnect() -> str:
        username = request.form.get("username", "").strip()
        app.connected_state["connected"] = False
        app.connected_state["username"] = ""
        app.connected_state["ip"] = ""
        chat_service.disconnect(username)
        return render_all_partials(connected=False, username="", ip="")

    @app.post("/messages")
    def post_message() -> str:
        content = request.form.get("message", "").strip()

        if not content:
            return render_template(
                "partials/message_list.html",
                messages=chat_service.get_messages(),
                users=chat_service.get_users(),
                users_map={u.get("ip", ""): u.get("name", "") for u in chat_service.get_users()},
            )

        chat_service.post_message(content)
        time.sleep(0.5)  # small delay to allow message to propagate and be included in the history before re-rendering
        # Re-render messages and include a users_map for exact IP->username lookup
        return render_template(
            "partials/message_list.html",
            messages=chat_service.get_messages(),
            users=chat_service.get_users(),
            users_map={u.get("ip", ""): u.get("name", "") for u in chat_service.get_users()},
        )

    return app