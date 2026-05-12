from __future__ import annotations

from flask import Flask, render_template, request


def create_app() -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config["SECRET_KEY"] = "dev"

    users: list[dict[str, str]] = [
        {"name": "Ava Patel", "status": "Online"},
        {"name": "Noah Kim", "status": "In chat"},
        {"name": "Mia Chen", "status": "Away"},
        {"name": "Liam Garcia", "status": "Offline"},
    ]

    messages: list[dict[str, str]] = [
        {"role": "assistant", "content": "Welcome. This scaffold is ready for a real chat backend."},
    ]

    @app.get("/")
    def index() -> str:
        return render_template("users.html", users=users)

    @app.get("/users")
    def user_list() -> str:
        return render_template("users.html", users=users)

    @app.get("/chat")
    def chat_room() -> str:
        selected_user = request.args.get("user", "").strip()
        return render_template("chat.html", messages=messages, selected_user=selected_user)

    @app.post("/messages")
    def post_message() -> str:
        content = request.form.get("message", "").strip()

        if not content:
            return render_template("partials/message_list.html", messages=messages)

        messages.append({"role": "user", "content": content})
        messages.append(
            {
                "role": "assistant",
                "content": "Hook this route to your model, queue, or websocket bridge.",
            }
        )
        return render_template("partials/message_list.html", messages=messages)

    return app