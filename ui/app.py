from __future__ import annotations

import os

from flask import Flask, render_template, request
from services import create_chat_service


def create_app() -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config["SECRET_KEY"] = "dev"
    mock_data_enabled = os.getenv(
        "MOCK_DATA_ENABLED",
        os.getenv("MOCK_RESPONSES_ENABLED", "true"),
    )
    app.config["MOCK_DATA_ENABLED"] = mock_data_enabled.strip().lower() == "true"
    chat_service = create_chat_service(app.config["MOCK_DATA_ENABLED"])

    @app.get("/")
    def index() -> str:
        return render_template("users.html", users=chat_service.get_users())

    @app.get("/users")
    def user_list() -> str:
        return render_template("users.html", users=chat_service.get_users())

    @app.get("/chat")
    def chat_room() -> str:
        selected_user = request.args.get("user", "").strip()
        return render_template(
            "chat.html",
            messages=chat_service.get_messages(selected_user),
            selected_user=selected_user,
        )

    @app.post("/messages")
    def post_message() -> str:
        content = request.form.get("message", "").strip()
        selected_user = request.form.get("user", "").strip()

        if not content:
            return render_template(
                "partials/message_list.html",
                messages=chat_service.get_messages(selected_user),
            )

        chat_service.post_user_message(selected_user, content)
        return render_template(
            "partials/message_list.html",
            messages=chat_service.get_messages(selected_user),
        )

    return app